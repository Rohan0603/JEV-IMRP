"""FYERS v3 data-socket recorder: subscription, L1 normalization, replay.

Research-only. This module never opens a socket and never places an order: the
caller owns the transport and hands received binary frames to :meth:`feed`.
Live capture is **NOT RUN** and requires credentials plus separate authorization.

Wire decoding stays in :mod:`jev_imrp.ingest.fyers_socket`; this recorder does
not parse bytes a second time. It only:

* builds the FYERS HSM auth/subscribe frames (field layout read from
  ``fyers_apiv3==3.2.1``; the live send itself is unverified);
* resolves the frozen universe through the FYERS symbol master, failing closed
  on a missing or suspended symbol;
* merges the decoded ``sf``/``if``/``dp`` topic states into one provider-neutral
  L1 quote per instrument;
* records every packet as a :class:`RawEvent` and feeds the same normalized
  payload to :class:`QuoteStateBook`, so online state and replay share one path;
* models a reconnect as a fresh generation that needs a new snapshot per
  instrument before state is trusted again.

Provider-neutral boundary: the consumer contract stays
:class:`jev_imrp.ingest.provider_contract.MarketDataProvider`. This module is one
provider implementation; nothing FYERS-specific leaks into the replay schema.
"""

from __future__ import annotations

import base64
import json
import struct
import time
import uuid
from collections import Counter, deque
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

from jev_imrp.common.config import load_universe
from jev_imrp.ingest.feed_state import QuoteState, QuoteStateBook
from jev_imrp.ingest.fyers_socket import (
    REASON_NO_SNAPSHOT,
    REASON_RECONNECT,
    RESP_AUTH,
    RESP_SUBSCRIBE,
    FyersPacket,
    FyersPacketDecoder,
    FyersQuoteBook,
    FyersQuoteState,
    PacketRate,
    measure_packet_rate,
)
from jev_imrp.ingest.fyers_symbol_master import (
    FyersInstrument,
    FyersSymbolError,
    build_fyers_mapping,
)
from jev_imrp.ingest.raw_event import (
    INGEST_NON_QUOTE,
    INGEST_OK,
    INGEST_PARSE_ERROR,
    INGEST_UNKNOWN_TYPE,
    RawEvent,
)

# --- provider / version identity ------------------------------------------------
PROVIDER: Final = "FYERS"
PROVIDER_VERSION: Final = "fyers-data-socket-v3-hsm-v1-5"
FROZEN_UNIVERSE_SIZE: Final = 20
DEFAULT_CHANNEL: Final = 11
DEFAULT_DATA_TYPES: Final[tuple[str, ...]] = ("sf",)
TOPIC_DATA_TYPES: Final[frozenset[str]] = frozenset({"sf", "if", "dp"})
NSE_CASH_SEGMENT: Final = "nse_cm"

#: Merge order for one instrument: top of book first, then index, then depth.
#: Depth level 1 overrides the ``sf`` touchline when both are present.
_TOPIC_ORDER: Final[dict[str, int]] = {"sf": 0, "if": 1, "dp": 2}


class FyersRecorderError(ValueError):
    """The recorder cannot build or trust a FYERS subscription."""


class FyersAuthError(FyersRecorderError):
    """The FYERS access token is missing, malformed, or expired."""


# --- subscription plan ----------------------------------------------------------
@dataclass(frozen=True, slots=True)
class SubscriptionEntry:
    """One frozen instrument and the topics requested for it."""

    research_symbol: str
    provider_symbol: str
    exchange: str
    exchange_token: str
    tick_size: float | None
    topics: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SubscriptionPlan:
    """The exact FYERS topics a recorder subscribes to and re-sends on reconnect."""

    entries: tuple[SubscriptionEntry, ...]
    topics: tuple[str, ...]
    master_sha256: str | None = None

    def by_topic(self) -> dict[str, SubscriptionEntry]:
        return {topic: entry for entry in self.entries for topic in entry.topics}

    def tick_sizes(self) -> dict[tuple[str, str], float]:
        """Tick sizes for the provider-neutral ``QuoteStateBook``."""
        out: dict[tuple[str, str], float] = {}
        for entry in self.entries:
            if entry.tick_size is not None:
                out[(entry.exchange, entry.exchange_token)] = entry.tick_size
        return out

    def tick_sizes_by_symbol(self) -> dict[str, float]:
        """Tick sizes for ``FyersQuoteBook``, which validates by symbol."""
        out: dict[str, float] = {}
        for entry in self.entries:
            if entry.tick_size is not None:
                out[entry.research_symbol] = entry.tick_size
        return out


def _exchange_for_segment(segment: str) -> str:
    prefix = segment.split("_", 1)[0]
    if not prefix:
        raise FyersRecorderError(f"cannot derive exchange from segment {segment!r}")
    return prefix.upper()


def _exchange_for_provider_symbol(provider_symbol: str) -> str:
    prefix, separator, _ = provider_symbol.partition(":")
    if not separator or not prefix:
        raise FyersRecorderError(f"FYERS symbol is missing an exchange: {provider_symbol!r}")
    return prefix.upper()


def build_subscription_plan(
    instruments: Sequence[FyersInstrument],
    *,
    tick_sizes: Mapping[str, float] | None = None,
    data_types: Sequence[str] = DEFAULT_DATA_TYPES,
    master_sha256: str | None = None,
) -> SubscriptionPlan:
    """Build exact ``<type>|<segment>|<token>`` topics from mapped instruments.

    Fails closed when an instrument has no FYERS exchange token, when a data
    type is not one of ``sf``/``if``/``dp``, or when two entries would collide.
    """
    if not instruments:
        raise FyersRecorderError("subscription plan requires at least one instrument")
    types = tuple(data_types)
    if not types:
        raise FyersRecorderError("at least one data type is required")
    unsupported = sorted(set(types) - TOPIC_DATA_TYPES)
    if unsupported:
        raise FyersRecorderError(f"unsupported FYERS data type(s): {', '.join(unsupported)}")

    entries: list[SubscriptionEntry] = []
    seen: set[str] = set()
    for instrument in instruments:
        token = instrument.exchange_token
        if not token:
            raise FyersSymbolError(
                f"missing FYERS exchange token for {instrument.provider_symbol}"
            )
        if "|" in token or not token.isascii():
            raise FyersSymbolError("FYERS exchange token must be ASCII without '|'")
        topics = tuple(f"{kind}|{NSE_CASH_SEGMENT}|{token}" for kind in types)
        for topic in topics:
            if len(topic.encode("ascii")) > 255:
                raise FyersSymbolError(f"FYERS topic is longer than 255 bytes: {topic!r}")
            if topic in seen:
                raise FyersSymbolError(f"duplicate FYERS topic: {topic}")
            seen.add(topic)
        tick = None if tick_sizes is None else tick_sizes.get(instrument.research_symbol)
        entries.append(
            SubscriptionEntry(
                research_symbol=instrument.research_symbol,
                provider_symbol=instrument.provider_symbol,
                exchange=_exchange_for_provider_symbol(instrument.provider_symbol),
                exchange_token=token,
                tick_size=None if tick is None else float(tick),
                topics=topics,
            )
        )
    return SubscriptionPlan(
        entries=tuple(entries),
        topics=tuple(topic for entry in entries for topic in entry.topics),
        master_sha256=master_sha256,
    )


def build_universe_subscription(
    master_rows: Sequence[Mapping[str, Any]],
    *,
    master_sha256: str,
    data_types: Sequence[str] = DEFAULT_DATA_TYPES,
) -> SubscriptionPlan:
    """Resolve ``config/universe.yaml`` through the FYERS master, fail closed.

    The frozen membership is not edited here: a missing, suspended, or
    token-less row is an error, never a silent substitution.
    """
    universe = load_universe()
    raw_instruments = universe.get("instruments")
    if not isinstance(raw_instruments, list):
        raise FyersRecorderError("universe.yaml instruments must be a list")

    symbols: list[str] = []
    ticks: dict[str, float] = {}
    for item in raw_instruments:
        if not isinstance(item, Mapping):
            raise FyersRecorderError("universe.yaml instrument must be a mapping")
        symbol = item.get("symbol")
        tick = item.get("tick_size_inr")
        if not isinstance(symbol, str) or not symbol:
            raise FyersRecorderError("universe.yaml instrument is missing symbol")
        if not isinstance(tick, (int, float)):
            raise FyersRecorderError(
                f"universe.yaml instrument {symbol} is missing tick_size_inr"
            )
        if symbol in ticks:
            raise FyersRecorderError(f"duplicate universe symbol {symbol}")
        symbols.append(symbol)
        ticks[symbol] = float(tick)

    if len(symbols) != FROZEN_UNIVERSE_SIZE:
        raise FyersRecorderError(
            f"frozen universe must contain {FROZEN_UNIVERSE_SIZE} symbols, "
            f"found {len(symbols)}"
        )
    mapping = build_fyers_mapping(symbols, master_rows, master_sha256=master_sha256)
    return build_subscription_plan(
        mapping,
        tick_sizes=ticks,
        data_types=data_types,
        master_sha256=master_sha256,
    )


# --- HSM control frames ---------------------------------------------------------
def _control_field(field_id: int, value: bytes) -> bytes:
    if not 0 <= field_id <= 255:
        raise FyersRecorderError("control field id must fit in one byte")
    if len(value) > 0xFFFF:
        raise FyersRecorderError("control field value is too long")
    return bytes([field_id]) + struct.pack("!H", len(value)) + value


def build_auth_frame(hsm_token: str, *, source: str = "JEV-IMRP") -> bytes:
    """Build the documented HSM auth frame (type 1, four fields)."""
    if not hsm_token:
        raise FyersAuthError("HSM token is required")
    try:
        token = hsm_token.encode("ascii")
        source_bytes = source.encode("ascii")
    except UnicodeEncodeError as exc:
        raise FyersAuthError("HSM token and source must be ASCII") from exc
    body = (
        bytes([RESP_AUTH, 4])
        + _control_field(1, token)
        + _control_field(2, b"P")
        + _control_field(3, b"1")
        + _control_field(4, source_bytes)
    )
    return struct.pack("!H", len(body)) + body


def build_subscribe_frame(
    topics: Sequence[str], *, channel: int = DEFAULT_CHANNEL
) -> bytes:
    """Build the documented HSM subscribe frame (type 4, two fields).

    Field 1 is ``uint16`` count followed by length-prefixed ASCII topic names;
    field 2 is the one-byte channel number. The declared outer length is the
    actual body length, which the decoder enforces.
    """
    names = tuple(topics)
    if not names:
        raise FyersRecorderError("at least one subscription topic is required")
    if len(names) > 0xFFFF:
        raise FyersRecorderError("too many subscription topics")
    if not 0 <= channel <= 255:
        raise FyersRecorderError("channel must fit in one byte")

    scrips = struct.pack("!H", len(names))
    for topic in names:
        try:
            raw = topic.encode("ascii")
        except UnicodeEncodeError as exc:
            raise FyersRecorderError(f"topic is not ASCII: {topic!r}") from exc
        if not raw or len(raw) > 255:
            raise FyersRecorderError(f"topic must be 1..255 bytes: {topic!r}")
        scrips += bytes([len(raw)]) + raw
    body = (
        bytes([RESP_SUBSCRIBE, 2])
        + _control_field(1, scrips)
        + _control_field(2, bytes([channel]))
    )
    return struct.pack("!H", len(body)) + body


def hsm_token_from_access_token(
    access_token: str, *, now: datetime | None = None
) -> str:
    """Extract ``hsm_key`` from the access-token JWT, failing closed.

    Never logs or returns the access token itself. A missing, malformed, or
    expired token is an error: live capture must not start with bad credentials.
    """
    if not access_token:
        raise FyersAuthError("FYERS access token is required")
    parts = access_token.split(".")
    if len(parts) != 3 or not parts[1]:
        raise FyersAuthError("FYERS access token is not a JWT")
    padding = "=" * (-len(parts[1]) % 4)
    try:
        decoded = base64.urlsafe_b64decode(parts[1] + padding)
        payload = json.loads(decoded)
    except (ValueError, json.JSONDecodeError) as exc:
        raise FyersAuthError("FYERS access token payload is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise FyersAuthError("FYERS access token payload must be an object")

    hsm_key = payload.get("hsm_key")
    if not isinstance(hsm_key, str) or not hsm_key:
        raise FyersAuthError("FYERS access token has no hsm_key")
    expiry = payload.get("exp")
    if not isinstance(expiry, (int, float)):
        raise FyersAuthError("FYERS access token has no expiry")
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if float(expiry) <= current.timestamp():
        raise FyersAuthError("FYERS access token is expired")
    return hsm_key


# --- recorder metrics -----------------------------------------------------------
@dataclass(frozen=True, slots=True)
class L1Availability:
    """Whether one instrument currently has a complete, trusted L1 touchline."""

    exchange: str
    token: str
    symbol: str
    bid_price: bool
    bid_size: bool
    ask_price: bool
    ask_size: bool
    source_time: bool
    state_valid: bool

    @property
    def complete(self) -> bool:
        return self.bid_price and self.bid_size and self.ask_price and self.ask_size


@dataclass(frozen=True, slots=True)
class FyersRecorderMetrics:
    """Packet-rate and L1-availability snapshot for health reporting."""

    packet_rate: PacketRate
    l1: tuple[L1Availability, ...]

    @property
    def l1_total(self) -> int:
        return len(self.l1)

    @property
    def l1_complete(self) -> int:
        return sum(1 for item in self.l1 if item.complete)

    @property
    def l1_availability(self) -> float:
        return 0.0 if not self.l1 else self.l1_complete / self.l1_total


@dataclass
class FyersRecorderCounters:
    """Observable capture health."""

    received: int = 0
    written: int = 0
    by_message_type: Counter[str] = field(default_factory=Counter)
    by_frame_kind: Counter[str] = field(default_factory=Counter)
    unknown_types: Counter[str] = field(default_factory=Counter)
    parse_errors: int = 0
    reconnects: int = 0
    snapshots: int = 0
    updates: int = 0
    invalid_states: int = 0
    first_received_utc: datetime | None = None
    last_received_utc: datetime | None = None


@dataclass(frozen=True, slots=True)
class RecordedMessage:
    """One recorded packet and the quote state it produced, if any."""

    event: RawEvent
    state: QuoteState | None


# --- recorder -------------------------------------------------------------------
class FyersRecorder:
    """Transport-agnostic FYERS data-socket recorder over an injected writer."""

    def __init__(
        self,
        writer: Any,
        plan: SubscriptionPlan,
        *,
        book: QuoteStateBook | None = None,
        session_id: str | None = None,
        connection_id: str | None = None,
        clock: Callable[[], tuple[datetime, int]] | None = None,
        data_origin: str = "live",
        access_token: str | None = None,
        rate_window: int = 10_000,
    ) -> None:
        if rate_window <= 0:
            raise ValueError("rate_window must be positive")
        if access_token is not None or data_origin == "live":
            hsm_token_from_access_token(access_token or "")

        self.writer = writer
        self.plan = plan
        if getattr(writer, "provider", "unknown") == "unknown":
            writer.provider = PROVIDER
        if getattr(writer, "provider_version", "not_declared") == "not_declared":
            writer.provider_version = PROVIDER_VERSION
        if getattr(writer, "master_hash", None) is None and plan.master_sha256 is not None:
            writer.master_hash = plan.master_sha256

        self.book = book or QuoteStateBook(tick_sizes=plan.tick_sizes())
        self.quote_book = FyersQuoteBook(tick_sizes=plan.tick_sizes_by_symbol())
        for entry in plan.entries:
            for topic in entry.topics:
                self.quote_book.register_subscription(topic, entry.research_symbol)
            self.book.register_subscription(
                entry.exchange, entry.exchange_token, entry.research_symbol
            )

        self.session_id = session_id or uuid.uuid4().hex
        self.connection_id = connection_id or uuid.uuid4().hex
        self.data_origin = data_origin
        self._clock = clock or _system_clock
        self.counters = FyersRecorderCounters()
        self._decoder = FyersPacketDecoder()
        self._rate_packets: deque[FyersPacket] = deque(maxlen=rate_window)
        self._by_topic = plan.by_topic()
        self._sequence = 0
        self._started = False
        self.generation = self.book.begin_generation()
        self.quote_book.generation = self.generation
        self.writer.generation = self.generation

    # -- connection lifecycle ------------------------------------------------
    def start(self) -> int:
        """Begin the first connection. A second call is a reconnect."""
        if self._started:
            return self.reconnect("start() called on an already-started recorder")
        self._started = True
        return self.generation

    def on_disconnect(self, reason: str) -> None:
        """Record a disconnect without bumping the generation yet."""
        self.counters.reconnects += 1
        self.quote_book.connection_anomalies.append(f"disconnect: {reason}")

    def reconnect(self, reason: str = "reconnect") -> tuple[str, ...]:
        """Start a fresh generation and return every topic to re-send.

        The decoder's topic registry is discarded: the server may reassign topic
        ids, and an incremental frame before the new snapshot must be a parse
        error rather than a stale correlation. The server persists nothing, so
        the caller must send the whole returned topic list.
        """
        self.on_disconnect(reason)
        self.connection_id = uuid.uuid4().hex
        self.generation = self.book.begin_generation()
        self.quote_book.begin_generation()
        self.quote_book.generation = self.generation
        self.writer.generation = self.generation
        self._decoder = FyersPacketDecoder()
        self._started = True
        return self.plan.topics

    def subscription_topics(self) -> tuple[str, ...]:
        return self.plan.topics

    def subscribe_frame(self, *, channel: int = DEFAULT_CHANNEL) -> bytes:
        return build_subscribe_frame(self.plan.topics, channel=channel)

    def auth_frame(self, access_token: str, *, source: str = "JEV-IMRP") -> bytes:
        return build_auth_frame(
            hsm_token_from_access_token(access_token), source=source
        )

    def awaiting_snapshot(self) -> list[str]:
        return self.quote_book.awaiting_snapshot()

    # -- the feed path -------------------------------------------------------
    def feed(self, raw: bytes) -> list[RecordedMessage]:
        """Record one binary frame and return one message per decoded packet.

        A frame can carry several scrip blocks. Parser failures and control
        frames are recorded, never dropped; only data blocks update quote state.
        """
        received_utc, monotonic_ns = self._clock()
        packets = self._decoder.decode(
            raw,
            received_timestamp_utc=received_utc,
            received_monotonic_ns=monotonic_ns,
            generation=self.generation,
            data_origin=self.data_origin,
        )
        return [self._record(packet) for packet in packets]

    def run(self, frames: Iterable[bytes]) -> list[RecordedMessage]:
        return [message for frame in frames for message in self.feed(frame)]

    def _record(self, packet: FyersPacket) -> RecordedMessage:
        self.counters.received += 1
        self.counters.last_received_utc = packet.received_timestamp_utc
        if self.counters.first_received_utc is None:
            self.counters.first_received_utc = packet.received_timestamp_utc
        self._rate_packets.append(packet)
        self.counters.by_message_type[packet.response_name] += 1

        state: QuoteState | None = None
        if packet.parse_error is not None:
            self.counters.parse_errors += 1
            self.quote_book.apply(packet)
            event = self._parse_error_event(packet)
        elif packet.block is None:
            status = INGEST_NON_QUOTE
            if packet.response_name.startswith("unknown:"):
                self.counters.unknown_types[packet.response_name] += 1
                self.quote_book.connection_anomalies.append(
                    f"unknown response type {packet.response_type}"
                )
                status = INGEST_UNKNOWN_TYPE
            event = self._control_event(packet, status)
        else:
            self.counters.by_frame_kind[packet.frame_kind] += 1
            fyers_state = self.quote_book.apply(packet)
            if fyers_state is None:
                raise FyersRecorderError("FYERS data packet produced no quote state")
            payload, source_utc = self._normalize(packet, fyers_state)
            message_type = str(payload["t"])
            event = self._quote_event(packet, payload, source_utc, message_type)
            state = self.book.apply(event)
            if message_type == "tk":
                self.counters.snapshots += 1
            else:
                self.counters.updates += 1
            if state is not None and not state.state_valid:
                self.counters.invalid_states += 1

        self._sequence += 1
        self.writer.write(event)
        self.counters.written += 1
        return RecordedMessage(event=event, state=state)

    # -- event construction --------------------------------------------------
    def _event(
        self,
        packet: FyersPacket,
        *,
        message_type: str,
        payload: dict[str, Any],
        ingestion_status: str,
        exchange: str | None = None,
        token: str | None = None,
        symbol: str | None = None,
        feed_timestamp_raw: Any = None,
        feed_timestamp_utc: datetime | None = None,
    ) -> RawEvent:
        return RawEvent(
            session_id=self.session_id,
            connection_id=self.connection_id,
            subscription_generation=self.generation,
            sequence=self._sequence,
            message_type=message_type,
            payload=payload,
            received_timestamp_utc=packet.received_timestamp_utc,
            received_monotonic_ns=packet.received_monotonic_ns,
            exchange=exchange,
            token=token,
            symbol_as_received=symbol,
            feed_timestamp_raw=feed_timestamp_raw,
            feed_timestamp_utc=feed_timestamp_utc,
            ingestion_status=ingestion_status,
            data_origin=self.data_origin,
            errors=(packet.parse_error,) if packet.parse_error is not None else (),
        ).with_event_id()

    def _parse_error_event(self, packet: FyersPacket) -> RawEvent:
        payload = {
            "t": "error",
            "_fyers_response_type": packet.response_type,
            "_raw_sha256": packet.raw_sha256,
            "_parse_error": packet.parse_error,
        }
        return self._event(
            packet,
            message_type="error",
            payload=payload,
            ingestion_status=INGEST_PARSE_ERROR,
        )

    def _control_event(self, packet: FyersPacket, status: str) -> RawEvent:
        # Control values are deliberately omitted: an auth/error frame could echo
        # a credential, and the raw event table must never carry one.
        payload: dict[str, Any] = {
            "t": packet.response_name,
            "_fyers_response_type": packet.response_type,
            "_fyers_field_ids": [item.field_id for item in packet.control_fields],
            "_fyers_field_lengths": [len(item.value) for item in packet.control_fields],
        }
        if packet.status is not None:
            payload["_fyers_status"] = packet.status
        return self._event(
            packet,
            message_type=packet.response_name,
            payload=payload,
            ingestion_status=status,
        )

    def _quote_event(
        self,
        packet: FyersPacket,
        payload: dict[str, Any],
        source_utc: datetime | None,
        message_type: str,
    ) -> RawEvent:
        symbol = payload.get("ti")
        return self._event(
            packet,
            message_type=message_type,
            payload=payload,
            ingestion_status=INGEST_OK,
            exchange=str(payload["e"]),
            token=str(payload["tk"]),
            symbol=None if symbol is None else str(symbol),
            feed_timestamp_raw=payload.get("ft"),
            feed_timestamp_utc=source_utc,
        )

    # -- L1 normalization ----------------------------------------------------
    def _normalize(
        self, packet: FyersPacket, state: FyersQuoteState
    ) -> tuple[dict[str, Any], datetime | None]:
        """Merge the instrument's topic states into one provider-neutral L1 event.

        The merged state is emitted as a full snapshot (``tk``), so replay never
        has to reproduce a provider-specific delta rule. If no topic state is
        trustworthy in this generation, the event is a sparse ``tf`` instead;
        that makes ``QuoteStateBook`` keep ``awaiting_snapshot`` rather than
        inventing a valid empty quote.
        """
        exchange, token = self._instrument_key(state)
        merged = self._merged_states(exchange, token)
        fields: dict[str, float | int] = {}
        for item in merged:
            fields.update(item.fields)

        source = next((item for item in merged if item.source_time_utc is not None), None)
        entry = self._by_topic.get(state.topic_name)
        symbol = state.symbol or (entry.research_symbol if entry is not None else None)

        payload: dict[str, Any] = {
            "t": "tk" if merged else "tf",
            "e": exchange,
            "tk": token,
            "_fyers_topic": state.topic_name,
            "_fyers_frame_kind": state.frame_kind,
            "_raw_sha256": packet.raw_sha256,
        }
        if symbol is not None:
            payload["ti"] = symbol
        if source is not None and source.source_time_raw is not None:
            payload["ft"] = source.source_time_raw

        mapping: dict[str, tuple[str, ...]] = {
            "pp": ("prev_close_price",),
            "lp": ("ltp",),
            "ls": ("last_traded_qty",),
            "o": ("open_price",),
            "h": ("high_price",),
            "l": ("low_price",),
            "ap": ("avg_trade_price",),
            "v": ("vol_traded_today",),
            "oi": ("OI",),
            "bq1": ("bid_size1", "bid_size"),
            "bp1": ("bid_price1", "bid_price"),
            "sq1": ("ask_size1", "ask_size"),
            "sp1": ("ask_price1", "ask_price"),
        }
        for target, candidates in mapping.items():
            for candidate in candidates:
                if candidate in fields:
                    payload[target] = fields[candidate]
                    break
        return payload, (source.source_time_utc if source is not None else None)

    def _instrument_key(self, state: FyersQuoteState) -> tuple[str, str]:
        if state.exchange and state.exchange_token:
            return state.exchange, state.exchange_token
        parts = state.topic_name.split("|")
        if len(parts) != 3 or not parts[2]:
            raise FyersRecorderError(
                f"cannot derive instrument key from topic {state.topic_name!r}"
            )
        return _exchange_for_segment(parts[1]), parts[2]

    def _merged_states(self, exchange: str, token: str) -> list[FyersQuoteState]:
        current = [
            item
            for item in self.quote_book.states.values()
            if item.exchange == exchange
            and item.exchange_token == token
            and item.generation == self.generation
            and REASON_RECONNECT not in item.invalid_reasons
            and REASON_NO_SNAPSHOT not in item.invalid_reasons
        ]
        current.sort(
            key=lambda item: (_TOPIC_ORDER.get(item.topic_type or "", 9), item.topic_name)
        )
        return current

    # -- health --------------------------------------------------------------
    def packet_rate(self) -> PacketRate:
        return measure_packet_rate(list(self._rate_packets))

    def metrics(self) -> FyersRecorderMetrics:
        items: list[L1Availability] = []
        for entry in self.plan.entries:
            state = self.book.states.get((entry.exchange, entry.exchange_token))
            items.append(
                L1Availability(
                    exchange=entry.exchange,
                    token=entry.exchange_token,
                    symbol=entry.research_symbol,
                    bid_price=state is not None and state.bid_price_1 is not None,
                    bid_size=state is not None and state.bid_qty_1 is not None,
                    ask_price=state is not None and state.ask_price_1 is not None,
                    ask_size=state is not None and state.ask_qty_1 is not None,
                    source_time=state is not None and state.feed_timestamp_utc is not None,
                    state_valid=state is not None and state.state_valid,
                )
            )
        return FyersRecorderMetrics(packet_rate=self.packet_rate(), l1=tuple(items))

    # -- shutdown ------------------------------------------------------------
    def flush(self) -> None:
        self.writer.flush_batch()

    def close(self) -> None:
        self.writer.finalize()


def _system_clock() -> tuple[datetime, int]:
    return datetime.now(UTC), time.monotonic_ns()


__all__ = [
    "DEFAULT_CHANNEL",
    "DEFAULT_DATA_TYPES",
    "FROZEN_UNIVERSE_SIZE",
    "PROVIDER",
    "PROVIDER_VERSION",
    "FyersAuthError",
    "FyersRecorder",
    "FyersRecorderCounters",
    "FyersRecorderError",
    "FyersRecorderMetrics",
    "L1Availability",
    "RecordedMessage",
    "SubscriptionEntry",
    "SubscriptionPlan",
    "build_auth_frame",
    "build_subscribe_frame",
    "build_subscription_plan",
    "build_universe_subscription",
    "hsm_token_from_access_token",
]
