"""OpenAlgo data-only WebSocket normalization into the replay pipeline."""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from jev_imrp.ingest.feed_state import QuoteState, QuoteStateBook
from jev_imrp.ingest.openalgo_client import parse_websocket_message
from jev_imrp.ingest.raw_event import (
    INGEST_NON_QUOTE,
    INGEST_OK,
    RawEvent,
    parse_feed_timestamp,
)


@dataclass
class OpenAlgoRecorderCounters:
    received: int = 0
    written: int = 0
    by_message_type: Counter[str] = field(default_factory=Counter)


class OpenAlgoRecorder:
    """Normalize OpenAlgo quote messages and reuse ``QuoteStateBook`` replay."""

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
            writer.provider = "openalgo"
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
        import time

        return datetime.now(UTC), time.monotonic_ns()

    @staticmethod
    def authenticate_frame(api_key: str) -> dict[str, str]:
        if not api_key:
            raise ValueError("api_key is required")
        return {"action": "authenticate", "api_key": api_key}

    @staticmethod
    def subscribe_frame(exchange: str, symbol: str, *, mode: int = 2) -> dict[str, object]:
        mode_names = {1: "LTP", 2: "Quote", 3: "Depth"}
        try:
            mode_name = mode_names[mode]
        except KeyError as exc:
            raise ValueError("mode must be 1 (LTP), 2 (Quote), or 3 (Depth)") from exc
        if not exchange or not symbol:
            raise ValueError("exchange and symbol are required")
        # Live gateway shape (verified 2026-11-09): a ``symbols`` list, not ``params``.
        return {
            "action": "subscribe",
            "symbols": [{"symbol": symbol, "exchange": exchange}],
            "mode": mode_name,
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
        received_utc, monotonic_ns = self._clock()
        self.counters.received += 1
        self.counters.by_message_type[kind] += 1
        exchange: str | None
        token: str | None
        symbol: str | None
        feed_raw: Any

        if kind in {"quote", "depth", "market_data"}:
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
            exchange = None
            token = None
            symbol = None
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
            exchange=exchange,
            token=token,
            symbol_as_received=symbol,
            feed_timestamp_raw=feed_raw,
            feed_timestamp_utc=parse_feed_timestamp(feed_raw),
            ingestion_status=status,
            data_origin=self.data_origin,
        ).with_event_id()
        self.writer.write(event)
        self.counters.written += 1
        state = self.book.apply(event) if status == INGEST_OK else None
        return event, state

    def _normalize_quote(self, payload: dict[str, Any], kind: str) -> dict[str, Any]:
        # Live ``market_data`` frames nest the quote under ``data``; flat frames do not.
        nested = payload.get("data")
        body = nested if isinstance(nested, dict) else payload
        exchange = payload.get("exchange") or body.get("exchange")
        symbol = payload.get("symbol") or body.get("symbol")
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
        field_map = {
            "timestamp": "ft",
            "ltt": "ft",
            "ltp": "lp",
            "last_quantity": "ltq",
            "bid": "bp1",
            "ask": "sp1",
            "bid_qty": "bq1",
            "ask_qty": "sq1",
            "open": "o",
            "high": "h",
            "low": "l",
            "close": "c",
            "volume": "v",
        }
        # ``ft`` must prefer the exchange trade timestamp when both exist.
        ordered = [("ltt", "ft")] + [
            (s, t) for s, t in field_map.items() if t != "ft"
        ]
        normalized.update({t: body[s] for s, t in ordered if s in body})
        if "timestamp" in body and "ft" not in normalized:
            normalized["ft"] = body["timestamp"]
        depth = body.get("depth")
        if isinstance(depth, dict):
            for side, price_key, qty_key in (("buy", "bp1", "bq1"), ("sell", "sp1", "sq1")):
                levels = depth.get(side)
                if price_key not in normalized and isinstance(levels, list) and levels:
                    top = levels[0]
                    if isinstance(top, dict):
                        if "price" in top:
                            normalized[price_key] = top["price"]
                        if "quantity" in top:
                            normalized[qty_key] = top["quantity"]
        return normalized


__all__ = ["OpenAlgoRecorder", "OpenAlgoRecorderCounters"]
