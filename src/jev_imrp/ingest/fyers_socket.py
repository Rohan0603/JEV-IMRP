"""Pure FYERS v3 data-socket packet parsing and normalization.

This module only handles already-received data packets.  It never opens a
socket, authenticates, subscribes, or calls an order/account endpoint.
Missing provider fields remain absent so downstream health gates can reject
ineligible observations instead of treating synthetic zeros as market data.
"""

from __future__ import annotations

import json
import uuid
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from jev_imrp.ingest.feed_state import QuoteState, QuoteStateBook
from jev_imrp.ingest.raw_event import INGEST_NON_QUOTE, INGEST_OK, RawEvent, parse_feed_timestamp


class FyersPacketError(ValueError):
    """The received FYERS packet is malformed or unsupported."""


def parse_fyers_message(raw: str | bytes) -> dict[str, Any]:
    """Parse one FYERS data-socket JSON object."""

    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise FyersPacketError("FYERS packet must be valid JSON object") from exc
    if not isinstance(value, dict):
        raise FyersPacketError("FYERS packet must be a JSON object")
    kind = value.get("type")
    if kind not in {"cn", "sub", "if", "dp", "sf"}:
        raise FyersPacketError(f"unsupported FYERS packet type: {kind!r}")
    return value


def normalize_fyers_message(message: Mapping[str, Any]) -> dict[str, Any]:
    """Map one FYERS packet to the canonical raw-event quote keys."""

    kind = message.get("type")
    if kind not in {"cn", "sub", "if", "dp", "sf"}:
        raise FyersPacketError(f"unsupported FYERS packet type: {kind!r}")
    normalized: dict[str, Any] = {"t": str(kind), "_fyers_payload": dict(message)}
    symbol = message.get("symbol")
    if not isinstance(symbol, str) or not symbol:
        if kind in {"if", "dp", "sf"}:
            raise FyersPacketError(f"FYERS {kind} packet requires symbol")
        return normalized

    normalized["e"] = symbol.split(":", 1)[0] if ":" in symbol else None
    normalized["tk"] = symbol
    normalized["ti"] = symbol
    if "last_traded_time" in message:
        normalized["ft"] = message["last_traded_time"]

    _copy_if_present(normalized, message, "ltp", "lp")
    _copy_if_present(normalized, message, "vol_traded_today", "v")
    _copy_if_present(normalized, message, "bid_price", "bp1")
    _copy_if_present(normalized, message, "ask_price", "sp1")
    _copy_if_present(normalized, message, "bid_size", "bq1")
    _copy_if_present(normalized, message, "ask_size", "sq1")
    for level in range(1, 6):
        _copy_if_present(normalized, message, f"bid_price{level}", f"bp{level}")
        _copy_if_present(normalized, message, f"ask_price{level}", f"sp{level}")
        _copy_if_present(normalized, message, f"bid_size{level}", f"bq{level}")
        _copy_if_present(normalized, message, f"ask_size{level}", f"sq{level}")
    return normalized


@dataclass
class FyersRecorderCounters:
    received: int = 0
    written: int = 0
    by_message_type: Counter[str] = field(default_factory=Counter)


class FyersSocketSession:
    """Deterministic subscription and reconnect state for one data socket."""

    def __init__(
        self,
        *,
        max_symbols: int = 100,
        max_reconnects: int = 50,
        base_delay_seconds: float = 0.5,
        max_delay_seconds: float = 30.0,
    ) -> None:
        if max_symbols < 1 or max_reconnects < 1:
            raise ValueError("socket limits must be positive")
        if base_delay_seconds <= 0 or max_delay_seconds < base_delay_seconds:
            raise ValueError("socket backoff limits are invalid")
        self.max_symbols = max_symbols
        self.max_reconnects = max_reconnects
        self.base_delay_seconds = base_delay_seconds
        self.max_delay_seconds = max_delay_seconds
        self._symbols: list[str] = []
        self.connected = False
        self.generation = 0

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(self._symbols)

    def subscribe(self, symbols: list[str] | tuple[str, ...]) -> dict[str, object]:
        candidate = list(dict.fromkeys(symbols))
        if any(not isinstance(symbol, str) or not symbol for symbol in candidate):
            raise ValueError("FYERS subscription symbols must be non-empty strings")
        if len(candidate) > self.max_symbols:
            raise ValueError(f"FYERS subscription exceeds {self.max_symbols} symbols")
        self._symbols = candidate
        return self._subscription_frame()

    def disconnect(self) -> None:
        self.connected = False

    def connect(self) -> dict[str, object]:
        self.connected = True
        self.generation += 1
        return self._subscription_frame()

    def can_retry(self, attempt: int) -> bool:
        if attempt < 1:
            raise ValueError("reconnect attempt must be positive")
        return attempt < self.max_reconnects

    def retry_delay_seconds(self, attempt: int) -> float:
        if attempt < 1:
            raise ValueError("reconnect attempt must be positive")
        delay = self.base_delay_seconds * float(2 ** (attempt - 1))
        return min(self.max_delay_seconds, delay)

    def _subscription_frame(self) -> dict[str, object]:
        return {"data_type": "SymbolUpdate", "symbols": list(self._symbols)}


class FyersRecorder:
    """Record already-received FYERS packets into the canonical replay format."""

    def __init__(
        self,
        writer: Any,
        book: QuoteStateBook | None = None,
        session_id: str | None = None,
        connection_id: str | None = None,
        clock: Callable[[], tuple[datetime, int]] | None = None,
        data_origin: str = "live",
    ) -> None:
        self.writer = writer
        if getattr(writer, "provider", "unknown") == "unknown":
            writer.provider = "fyers"
        if getattr(writer, "provider_version", "not_declared") == "not_declared":
            writer.provider_version = "fyers-data-socket-v3"
        self.book = book or QuoteStateBook()
        self.session_id = session_id or uuid.uuid4().hex
        self.connection_id = connection_id or uuid.uuid4().hex
        self.data_origin = data_origin
        self._clock = clock or self._system_clock
        self.counters = FyersRecorderCounters()
        self.generation = self.book.begin_generation()
        self.writer.generation = self.generation
        self._sequence = 0
        self._seen_symbols: set[tuple[str, str]] = set()

    @staticmethod
    def _system_clock() -> tuple[datetime, int]:
        import time

        return datetime.now(UTC), time.monotonic_ns()

    def feed(self, message: str | bytes | Mapping[str, Any]) -> tuple[RawEvent, QuoteState | None]:
        parsed = (
            parse_fyers_message(message)
            if isinstance(message, (str, bytes))
            else dict(message)
        )
        kind = parsed.get("type")
        normalized = normalize_fyers_message(parsed)
        received_utc, monotonic_ns = self._clock()
        self.counters.received += 1
        self.counters.by_message_type[str(kind)] += 1

        exchange = normalized.get("e")
        token = normalized.get("tk")
        symbol = normalized.get("ti")
        is_market_data = kind in {"if", "dp", "sf"} and exchange and token and symbol
        if is_market_data:
            key = (str(exchange), str(token))
            message_type = "tf" if key in self._seen_symbols else "tk"
            self._seen_symbols.add(key)
            normalized["_fyers_type"] = kind
            normalized["t"] = message_type
            ingestion_status = INGEST_OK
            feed_raw = normalized.get("ft")
        else:
            message_type = str(kind)
            ingestion_status = INGEST_NON_QUOTE
            exchange = token = symbol = None
            feed_raw = None

        self._sequence += 1
        event = RawEvent(
            session_id=self.session_id,
            connection_id=self.connection_id,
            subscription_generation=self.generation,
            sequence=self._sequence,
            message_type=message_type,
            payload=normalized,
            received_timestamp_utc=received_utc,
            received_monotonic_ns=monotonic_ns,
            exchange=None if exchange is None else str(exchange),
            token=None if token is None else str(token),
            symbol_as_received=None if symbol is None else str(symbol),
            feed_timestamp_raw=feed_raw,
            feed_timestamp_utc=parse_feed_timestamp(feed_raw),
            ingestion_status=ingestion_status,
            data_origin=self.data_origin,
        ).with_event_id()
        self.writer.write(event)
        self.counters.written += 1
        state = self.book.apply(event) if ingestion_status == INGEST_OK else None
        return event, state


def _copy_if_present(
    target: dict[str, Any], source: Mapping[str, Any], source_key: str, target_key: str
) -> None:
    if source_key in source:
        target[target_key] = source[source_key]


__all__ = [
    "FyersPacketError",
    "FyersRecorder",
    "FyersRecorderCounters",
    "FyersSocketSession",
    "normalize_fyers_message",
    "parse_fyers_message",
]
