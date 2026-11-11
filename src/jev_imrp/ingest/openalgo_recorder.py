"""OpenAlgo read-only WebSocket normalization into the replay pipeline."""

from __future__ import annotations

import time
import uuid
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from jev_imrp.ingest.feed_state import QuoteState, QuoteStateBook
from jev_imrp.ingest.openalgo_client import parse_websocket_message
from jev_imrp.ingest.raw_event import INGEST_NON_QUOTE, INGEST_OK, RawEvent, parse_feed_timestamp


@dataclass
class OpenAlgoRecorderCounters:
    received: int = 0
    written: int = 0
    by_message_type: Counter[str] = field(default_factory=Counter)


class OpenAlgoRecorder:
    """Record gateway messages while retaining FYERS upstream provenance."""

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
            writer.provider = "openalgo:fyers"
        if getattr(writer, "provider_version", "not_declared") == "not_declared":
            writer.provider_version = "openalgo-data-api-v1"
        self.book = book or QuoteStateBook()
        self.session_id = session_id or uuid.uuid4().hex
        self.connection_id = connection_id or uuid.uuid4().hex
        self.data_origin = data_origin
        self._clock = clock or self._system_clock
        self.counters = OpenAlgoRecorderCounters()
        self.generation = self.book.begin_generation()
        self.writer.generation = self.generation
        self._sequence = 0
        self._seen_symbols: set[tuple[str, str]] = set()

    @staticmethod
    def _system_clock() -> tuple[datetime, int]:
        return datetime.now(UTC), time.monotonic_ns()

    @staticmethod
    def authenticate_frame(api_key: str) -> dict[str, str]:
        if not api_key:
            raise ValueError("api_key is required")
        return {"action": "authenticate", "api_key": api_key}

    @staticmethod
    def subscribe_frame(exchange: str, symbol: str, *, mode: int = 2) -> dict[str, object]:
        return OpenAlgoRecorder.subscribe_batch_frame(exchange, [symbol], mode=mode)

    @staticmethod
    def subscribe_batch_frame(
        exchange: str, symbols: list[str], *, mode: int = 2
    ) -> dict[str, object]:
        names = {1: "LTP", 2: "Quote", 3: "Depth"}
        if mode not in names:
            raise ValueError("mode must be 1 (LTP), 2 (Quote), or 3 (Depth)")
        if not exchange or not symbols or any(not symbol for symbol in symbols):
            raise ValueError("exchange and symbols are required")
        return {
            "action": "subscribe",
            "mode": names[mode],
            "symbols": [{"exchange": exchange, "symbol": symbol} for symbol in symbols],
        }

    def feed(self, message: str | bytes | dict[str, Any]) -> tuple[RawEvent, QuoteState | None]:
        if isinstance(message, dict):
            payload = message
            kind = payload.get("type")
        else:
            kind, payload = parse_websocket_message(message)
        if not isinstance(payload, dict):
            raise ValueError("OpenAlgo message must be an object")
        kind = str(kind)
        self.counters.received += 1
        self.counters.by_message_type[kind] += 1
        exchange: str | None
        token: str | None
        symbol: str | None
        feed_raw: Any
        if kind in {"market_data", "quote", "depth"}:
            normalized = self._normalize_quote(payload, kind)
            message_type = str(normalized["t"])
            status = INGEST_OK
            exchange = str(normalized["e"])
            token = str(normalized["tk"])
            symbol = str(normalized["ti"])
            feed_raw = normalized.get("ft")
        else:
            normalized = {"t": kind, "_openalgo_payload": payload}
            message_type = kind
            status = INGEST_NON_QUOTE
            exchange = token = symbol = None
            feed_raw = None
        received_utc, monotonic_ns = self._clock()
        self._sequence += 1
        timestamp_unit = "milliseconds" if kind == "market_data" else "seconds"
        event = RawEvent(
            session_id=self.session_id,
            connection_id=self.connection_id,
            subscription_generation=self.generation,
            sequence=self._sequence,
            message_type=message_type,
            payload=normalized,
            received_timestamp_utc=received_utc,
            received_monotonic_ns=monotonic_ns,
            exchange=exchange,
            token=token,
            symbol_as_received=symbol,
            feed_timestamp_raw=feed_raw,
            feed_timestamp_utc=parse_feed_timestamp(feed_raw, unit=timestamp_unit),
            ingestion_status=status,
            data_origin=self.data_origin,
        ).with_event_id()
        self.writer.write(event)
        self.counters.written += 1
        return event, self.book.apply(event) if status == INGEST_OK else None

    def _normalize_quote(self, payload: dict[str, Any], kind: str) -> dict[str, Any]:
        source: Mapping[str, Any] = payload
        if kind == "market_data" and isinstance(payload.get("data"), Mapping):
            source = payload["data"]
            kind = "depth" if isinstance(source.get("depth"), Mapping) else "quote"
        exchange = source.get("exchange", payload.get("exchange"))
        symbol = source.get("symbol", payload.get("symbol"))
        if not exchange or not symbol:
            raise ValueError("OpenAlgo quote requires exchange and symbol")
        key = (str(exchange), str(symbol))
        message_type = "tf" if key in self._seen_symbols else "tk"
        self._seen_symbols.add(key)
        normalized: dict[str, Any] = {
            "t": message_type,
            "e": str(exchange),
            "tk": str(symbol),
            "ti": str(symbol),
            "_openalgo_type": kind,
            "_openalgo_payload": payload,
        }
        fields = {
            "timestamp": "ft", "ltt": "ft", "ltp": "lp", "bid": "bp1",
            "bid_price": "bp1", "ask": "sp1", "ask_price": "sp1",
            "bid_qty": "bq1", "bid_size": "bq1", "ask_qty": "sq1", "ask_size": "sq1",
            "open": "o",
            "high": "h", "low": "l", "close": "c", "volume": "v",
        }
        normalized.update(
            {target: source[name] for name, target in fields.items() if name in source}
        )
        depth = source.get("depth")
        if isinstance(depth, Mapping):
            for side, price_key, size_key in (
                ("buy", "bp", "bq"),
                ("sell", "sp", "sq"),
            ):
                levels = depth.get(side)
                if not isinstance(levels, list):
                    continue
                for index, level in enumerate(levels[:5], start=1):
                    if isinstance(level, Mapping):
                        if "price" in level:
                            normalized[f"{price_key}{index}"] = level["price"]
                        if "quantity" in level:
                            normalized[f"{size_key}{index}"] = level["quantity"]
        return normalized


__all__ = ["OpenAlgoRecorder", "OpenAlgoRecorderCounters"]
