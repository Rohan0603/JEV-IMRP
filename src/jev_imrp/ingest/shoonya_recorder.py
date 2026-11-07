"""Transport-agnostic Shoonya market-feed recorder (plan sections 5.2, 5.4, 5.5).

Research-only. This module never opens a socket and never reads credentials: the
caller feeds it decoded websocket text, so replay and tests exercise exactly the
same code path as a live capture (plan 5.6.10).

What the recorder owns, per the verified feed contract:

* records every received message as a raw event, whatever its type or outcome;
* accepts both documented connect-ack spellings (``ak``/``Ok`` and ``ck``/``OK``)
  and records exactly which one was seen;
* flags unknown message types instead of dropping them;
* treats the first ``tk`` per instrument as a snapshot and later ``tk`` as a
  replacement, ``tf`` as a partial merge (see :mod:`jev_imrp.ingest.feed_state`);
* on disconnect/reconnect, re-sends the connect frame and EVERY subscription and
  waits for fresh ``tk`` snapshots before state is trusted again.

Because the gateway persists nothing, the recorder treats every reconnect as a
fresh generation: previous state is kept for audit but marked invalid until a
new snapshot repairs it.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import signal
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import FrameType
from typing import Any

from jev_imrp.common.logging import get_logger
from jev_imrp.ingest.feed_state import QuoteState, QuoteStateBook
from jev_imrp.ingest.file_writer import RawEventWriter
from jev_imrp.ingest.historical_data_client import (
    HistoricalDataError,
    fetch_eod_chart_data,
    fetch_tp_series_data,
)
from jev_imrp.ingest.raw_event import (
    INGEST_NON_QUOTE,
    INGEST_OK,
    INGEST_PARSE_ERROR,
    INGEST_UNKNOWN_TYPE,
    RawEvent,
    parse_feed_timestamp,
)

logger = get_logger(__name__)

#: Message types the feed uses for the two documented connect acknowledgements.
#: The vendor documents one spelling on one page and the other on another, so both
#: are accepted and the exact spelling observed is recorded.
CONNECT_ACK_TYPES = ("ak", "ck")
#: Frames that carry no instrument quote.
CONTROL_TYPES = frozenset({"t", "u", "uk", "ak", "ck", "error", "ds", "kf"})


@dataclass
class RecorderCounters:
    """Observable capture health (feeds the health report and plan 14.2)."""

    received: int = 0
    written: int = 0
    by_message_type: Counter[str] = field(default_factory=Counter)
    parse_errors: int = 0
    unknown_types: Counter[str] = field(default_factory=Counter)
    connect_acks: Counter[str] = field(default_factory=Counter)
    reconnects: int = 0
    snapshots_seen: int = 0
    updates_merged: int = 0
    invalid_states: int = 0
    last_received_utc: datetime | None = None
    first_received_utc: datetime | None = None


class Recorder:
    """Feed recorder: raw event log + quote state, over an injected transport."""

    def __init__(
        self,
        writer: RawEventWriter,
        book: QuoteStateBook | None = None,
        session_id: str | None = None,
        connection_id: str | None = None,
        clock: Callable[[], tuple[datetime, int]] | None = None,
        data_origin: str = "live",
    ) -> None:
        self.writer = writer
        self.book = book or QuoteStateBook()
        self.session_id = session_id or uuid.uuid4().hex
        self.connection_id = connection_id or uuid.uuid4().hex
        self.data_origin = data_origin
        self._clock = clock or _system_clock
        self.counters = RecorderCounters()
        self.generation = 0
        self._sequence = 0
        self._last_signal: int | None = None
        self._websocket_task: asyncio.Task[Any] | None = None
        self._websocket_loop: asyncio.AbstractEventLoop | None = None
        self._websocket: Any = None
        self._connect_frame: dict[str, Any] | None = None
        self._subscriptions: list[tuple[str, str, str | None]] = []

    # -- clock --------------------------------------------------------------
    def now(self) -> tuple[datetime, int]:
        """(UTC wall clock, monotonic ns) from one call, so they agree."""
        return self._clock()

    # -- connection lifecycle ----------------------------------------------
    def start(self) -> None:
        """Begin a connection: bump the generation and reissue a connection id.

        The connect frame itself carries the session's user id and token, which
        this research recorder deliberately never holds, so the caller sends its
        own frame and then calls :meth:`subscribe`.
        """
        self.connection_id = uuid.uuid4().hex
        self.generation = self.book.begin_generation()
        self.writer.generation = self.generation

    def set_connect_frame(self, frame: dict[str, Any]) -> None:
        """Set the connect frame to be used for reconnects.

        The frame's ``uid``, ``actid``, and ``accesstoken`` are used for
        reconnects.
        """
        self._connect_frame = frame

    def subscribe(self, instruments: list[tuple[str, str, str | None]]) -> dict[str, Any]:
        """Build the subscribe frame and remember what was asked for.

        ``instruments`` are ``(exchange, token, symbol)``. On a reconnect the
        caller replays the identical list: the gateway keeps nothing, so every
        subscription must be re-sent before state can be trusted again.
        """
        self._subscriptions = instruments
        for exchange, token, symbol in instruments:
            self.book.register_subscription(exchange, token, symbol)
        return {
            "t": "t",
            "k": "#".join(f"{exchange}|{token}" for exchange, token, _ in instruments),
        }

    def subscribe_all_known(self) -> dict[str, Any]:
        """Re-send every subscription recorded so far (reconnect path)."""
        return {
            "t": "t",
            "k": "#".join(f"{exchange}|{token}" for exchange, token in self.book.subscriptions),
        }

    def register_subscribe_frame(self, frame: dict[str, Any]) -> list[tuple[str, str]]:
        """Record the subscriptions a subscribe frame asked for.

        The frame's ``k`` is the documented ``#``-delimited ``EXCHANGE|TOKEN``
        list, so the recorder remembers exactly what was requested and can
        reissue it verbatim after a reconnect.
        """
        registered: list[tuple[str, str]] = []
        for pair in str(frame.get("k", "")).split("#"):
            exchange, _, token = pair.partition("|")
            if not exchange or not token:
                continue
            self.book.register_subscription(exchange, token)
            registered.append((exchange, token))
        return registered

    def on_disconnect(self, reason: str) -> None:
        """Mark a disconnect. Next ``start()``/``subscribe`` reinitializes state."""
        self.counters.reconnects += 1
        self.book.connection_anomalies.append(f"disconnect: {reason}")

    # -- WebSocket connection -----------------------------------------------
    async def connect(self, websocket_url: str, connect_frame: dict[str, Any]) -> None:
        """Connect to the WebSocket feed and start listening."""
        self._connect_frame = connect_frame
        self._websocket_loop = asyncio.get_event_loop()
        self._websocket_task = self._websocket_loop.create_task(
            self._websocket_listener(websocket_url)
        )

    async def _websocket_listener(self, websocket_url: str) -> None:
        """Listen for WebSocket messages and feed them to the recorder."""
        import websockets

        while True:
            try:
                async with websockets.connect(websocket_url) as ws:
                    self._websocket = ws
                    await self._send_connect_frame()
                    await self._send_subscriptions()

                    async for message in ws:
                        self.feed(message)
            except Exception as exc:
                self.on_disconnect(f"connection error: {exc}")
                await asyncio.sleep(1)  # Backoff before reconnect

    async def _send_connect_frame(self) -> None:
        """Send the connect frame to the WebSocket."""
        if self._connect_frame:
            await self._websocket.send(json.dumps(self._connect_frame))

    async def _send_subscriptions(self) -> None:
        """Send all subscriptions to the WebSocket."""
        if self._subscriptions:
            subscribe_frame = self.subscribe(self._subscriptions)
            await self._websocket.send(json.dumps(subscribe_frame))

    # -- the feed path ------------------------------------------------------
    def feed(self, message: str | bytes | dict[str, Any]) -> tuple[RawEvent, QuoteState | None]:
        """Record one received message and update state.

        Returns the raw event (always recorded) and the new quote state, or None
        for control frames. Parser failures are recorded, not raised: losing the
        evidence of a malformed message is worse than a flagged row.
        """
        received_utc, monotonic_ns = self.now()
        payload, parse_error = _decode(message)
        self.counters.received += 1
        self.counters.last_received_utc = received_utc
        if self.counters.first_received_utc is None:
            self.counters.first_received_utc = received_utc

        message_type = str(payload.get("t", "")) if payload else ""
        if message_type:
            self.counters.by_message_type[message_type] += 1
        if message_type in CONNECT_ACK_TYPES:
            self.counters.connect_acks[message_type] += 1

        status = INGEST_OK
        if parse_error is not None:
            self.counters.parse_errors += 1
            status = INGEST_PARSE_ERROR
        elif message_type in ("tk", "tf"):
            status = INGEST_OK
        elif message_type in CONTROL_TYPES:
            # connect acks (both documented spellings), subscribe/unsubscribe
            # and their acks, plus vendor error/other control frames.
            status = INGEST_NON_QUOTE
        else:
            # Unknown types are flagged and recorded, never silently dropped.
            self.counters.unknown_types[message_type or "<missing>"] += 1
            self.book.connection_anomalies.append(f"unknown message type: {message_type!r}")
            status = INGEST_UNKNOWN_TYPE

        self._sequence += 1
        event = RawEvent(
            session_id=self.session_id,
            connection_id=self.connection_id,
            subscription_generation=self.generation,
            sequence=self._sequence,
            message_type=message_type,
            payload=payload,
            received_timestamp_utc=received_utc,
            received_monotonic_ns=monotonic_ns,
            exchange=_as_text(payload.get("e")) if payload else None,
            token=_as_text(payload.get("tk")) if payload else None,
            symbol_as_received=_as_text(payload.get("ti")) if payload else None,
            feed_timestamp_raw=payload.get("ft") if payload else None,
            # Raw feed time, parsed, never replaced by local time (see 5.3 caveat).
            feed_timestamp_utc=parse_feed_timestamp(payload.get("ft")) if payload else None,
            ingestion_status=status,
            data_origin=self.data_origin,
        ).with_event_id()
        self.writer.write(event)
        self.counters.written += 1

        state = None
        if status == INGEST_OK and message_type in ("tk", "tf"):
            state = self.book.apply(event)
            if state is not None:
                if message_type == "tk":
                    self.counters.snapshots_seen += 1
                else:
                    self.counters.updates_merged += 1
                if not state.state_valid:
                    self.counters.invalid_states += 1
        return event, state

    def run(self, messages: list[str | dict[str, Any]]) -> list[tuple[RawEvent, QuoteState | None]]:
        """Feed an ordered message sequence. Used by replay and by tests."""
        return [self.feed(message) for message in messages]

    # -- shutdown -----------------------------------------------------------
    def install_signal_handlers(self) -> Callable[[], None]:
        """Flush on SIGINT/SIGTERM (plan 5.5). Returns an undo callable.

        Handlers record the signal and let the caller's loop stop; they never
        raise from inside the signal context, so the flush happens on the normal
        path and always runs before the writer is closed.
        """
        previous: dict[int, Any] = {}

        def handler(signum: int, _frame: FrameType | None) -> None:
            self._last_signal = signum
            self.flush()

        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(sig, handler)

        def undo() -> None:
            for sig, old in previous.items():
                signal.signal(sig, old)

        return undo

    def flush(self) -> None:
        """Finalize the open batch without closing the recorder.

        Completed batches survive a hard stop because only finalized files are
        manifest-listed; the signal-handler path uses this.
        """
        self.writer.flush_batch()

    def close(self) -> None:
        """Finalize and close. Idempotent."""
        self.writer.finalize()
        if self._websocket_task and not self._websocket_task.done():
            self._websocket_task.cancel()
            if self._websocket_loop and not self._websocket_loop.is_running():
                with contextlib.suppress(asyncio.CancelledError, RuntimeError):
                    self._websocket_loop.run_until_complete(self._websocket_task)

    async def backfill_historical_data(
        self,
        exchange: str,
        symbol: str,
        from_date: str,
        to_date: str,
        interval: str = "1m",
        user_id: str | None = None,
    ) -> None:
        """Backfill historical data for a given symbol using TPSeries.

        Args:
            exchange: Exchange code (e.g., "NSE").
            symbol: Trading symbol (e.g., "SBIN-EQ").
            from_date: Start date in YYYY-MM-DD format.
            to_date: End date in YYYY-MM-DD format.
            interval: Time interval for candles (e.g., "1m", "5m", "15m", "60m", "240m").
            user_id: Shoonya user ID.

        Raises:
            HistoricalDataError: If fetching historical data fails.
        """
        try:
            # Fetch TPSeries data with pagination support
            tp_data = fetch_tp_series_data(exchange, symbol, from_date, to_date, interval, user_id)

            # Convert TPSeries data to RawEvents and write to the writer
            for candle in tp_data:
                # Schema validation: ensure required fields exist
                required_fields = ["time", "e", "tk", "ti"]
                missing_fields = [f for f in required_fields if f not in candle]
                if missing_fields:
                    logger.warning(
                        f"Skipping candle with missing fields {missing_fields} "
                        f"for {exchange}|{symbol} in backfill_historical_data"
                    )
                    continue

                ts = _parse_candle_timestamp(candle.get("time"))
                event = RawEvent(
                    session_id=self.session_id,
                    connection_id=self.connection_id,
                    subscription_generation=self.generation,
                    sequence=self._sequence,
                    message_type="tp",
                    payload=candle,
                    received_timestamp_utc=ts,
                    received_monotonic_ns=self.now()[1],
                    exchange=candle.get("e"),
                    token=candle.get("tk"),
                    symbol_as_received=candle.get("ti"),
                    feed_timestamp_raw=candle.get("time"),
                    feed_timestamp_utc=ts,
                    ingestion_status=INGEST_OK,
                    data_origin="historical",
                ).with_event_id()
                self.writer.write(event)
                self._sequence += 1
                self.counters.received += 1
                self.counters.written += 1

        except HistoricalDataError as exc:
            logger.error(f"Failed to backfill TPSeries data for {exchange}|{symbol}: {exc}")
            raise

    async def backfill_eod_data(
        self,
        exchange: str,
        symbol: str,
        from_date: str,
        to_date: str,
        user_id: str | None = None,
    ) -> None:
        """Backfill EOD data for a given symbol using EODChartData.

        Args:
            exchange: Exchange code (e.g., "NSE").
            symbol: Trading symbol (e.g., "SBIN-EQ").
            from_date: Start date in YYYY-MM-DD format.
            to_date: End date in YYYY-MM-DD format.
            user_id: Shoonya user ID.

        Raises:
            HistoricalDataError: If fetching EOD data fails.
        """
        try:
            # Fetch EODChartData data with pagination support
            eod_data = fetch_eod_chart_data(exchange, symbol, from_date, to_date, user_id)

            # Convert EOD data to RawEvents and write to the writer
            for candle in eod_data:
                # Schema validation: ensure required fields exist
                required_fields = ["date", "e", "tk", "ti"]
                missing_fields = [f for f in required_fields if f not in candle]
                if missing_fields:
                    logger.warning(
                        f"Skipping candle with missing fields {missing_fields} "
                        f"for {exchange}|{symbol} in backfill_eod_data"
                    )
                    continue

                ts = _parse_candle_timestamp(candle.get("date"))
                event = RawEvent(
                    session_id=self.session_id,
                    connection_id=self.connection_id,
                    subscription_generation=self.generation,
                    sequence=self._sequence,
                    message_type="eod",
                    payload=candle,
                    received_timestamp_utc=ts,
                    received_monotonic_ns=self.now()[1],
                    exchange=candle.get("e"),
                    token=candle.get("tk"),
                    symbol_as_received=candle.get("ti"),
                    feed_timestamp_raw=candle.get("date"),
                    feed_timestamp_utc=ts,
                    ingestion_status=INGEST_OK,
                    data_origin="historical",
                ).with_event_id()
                self.writer.write(event)
                self._sequence += 1
                self.counters.received += 1
                self.counters.written += 1

        except HistoricalDataError as exc:
            logger.error(f"Failed to backfill EODChartData data for {exchange}|{symbol}: {exc}")
            raise


def _system_clock() -> tuple[datetime, int]:
    import time as _time

    return datetime.now(UTC), _time.monotonic_ns()


def _parse_candle_timestamp(ts_val: Any) -> datetime:
    """Parse candle timestamp strings safely, converting to naive UTC datetime."""
    if not ts_val or not isinstance(ts_val, str):
        return datetime.now(UTC).replace(tzinfo=None)
    try:
        return datetime.fromisoformat(ts_val).replace(tzinfo=None)
    except ValueError:
        pass
    for fmt in ("%d-%m-%Y %H:%M:%S", "%d-%m-%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(ts_val, fmt)
        except ValueError:
            pass
    return datetime.now(UTC).replace(tzinfo=None)


def _as_text(value: Any) -> str | None:
    """Return the value as text, or None when genuinely absent."""
    if value is None:
        return None
    text = str(value)
    return text or None


def _decode(message: str | bytes | dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Decode one message into a payload dict, plus a parse-error description.

    Non-object JSON (arrays, scalars) and malformed JSON yield an empty payload
    with an error string; the caller records the message as a parse error rather
    than dropping it.
    """
    if isinstance(message, dict):
        return dict(message), None
    text = message.decode("utf-8", errors="replace") if isinstance(message, bytes) else message
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError as exc:
        # Keep the bytes exactly as received: a message we cannot parse is still
        # evidence, and rewriting it would destroy the only copy.
        return {"__unparsed__": text}, f"invalid JSON: {exc.msg} at position {exc.pos}"
    if not isinstance(decoded, dict):
        return (
            {"__unparsed__": text},
            f"expected a JSON object, got {type(decoded).__name__}",
        )
    return decoded, None
