"""FYERS API v3 Data Socket (HSM v1-5) packet qualification, offline only.

This module decodes recorded FYERS data-socket frames and reconstructs quote
state from them. It never opens a socket and never places an order: the
research contract keeps execution out of scope, and every fixture is labelled
``synthetic``.

Protocol facts (verified 2026-11-09 against ``fyers_apiv3==3.2.1`` -- the
readable reference implementation -- and the official v3 WebSocket docs):

* Endpoint ``wss://socket.fyers.in/hsm/v1-5/prod``; frames are binary.
* Outer frame: ``uint16`` big-endian length (bytes after the length field),
  ``uint8`` response type.
* Response types: ``1`` auth, ``4`` subscribe, ``5`` unsubscribe, ``6`` data
  feed, ``7`` pause, ``8`` resume, ``12`` mode, ``51`` custom message.
* Data feed (``6``): ``uint32`` big-endian message number, ``uint16``
  big-endian scrip count, then that many scrip blocks.
* Scrip block type: ``83`` snapshot, ``85`` full/incremental, ``76`` lite.
* Topic names are ``sf|<segment>|<token>`` (symbol), ``if|...`` (index) and
  ``dp|...`` (depth). Snapshot blocks carry the topic name; full and lite
  blocks carry only the numeric topic id, so a snapshot must be seen first.
* Field vectors are ``int32`` big-endian. ``INT32_MIN`` (``-2147483648``)
  means "field absent/unchanged", never zero. Prices are
  ``raw / (10**precision * multiplier)``; time, size, quantity and order-count
  fields are not scaled.
* ``sf`` carries ``last_traded_time`` and ``exch_feed_time``; ``if`` carries
  only ``exch_feed_time``; ``dp`` carries no source timestamp at all.
* Both time fields are documented as Unix epoch seconds. This module refuses
  to guess a different unit: an implausible value is flagged, not reinterpreted.

Topic ids are read little-endian to match ``fyers_apiv3`` 3.2.1, which unpacks
them with native ``H`` on little-endian hosts. Topic ids are only correlation
keys, so snapshot and incremental frames stay consistent either way; the wire
byte order is recorded as an open ambiguity in the evidence manifest.

Shoonya's ``tk``/``tf``/``ft`` vocabulary is deliberately absent here. The two
feeds share a reason-code vocabulary for downstream convenience, but no Shoonya
frame type, field name or reconnect assumption is reused.
"""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Final

from jev_imrp.common.timeutils import is_within_session

# --- provider / version identity ------------------------------------------------
PROVIDER: Final = "FYERS"
API_VERSION: Final = "v3"
DATA_SOCKET_VERSION: Final = "HSM v1-5"
SOCKET_ENDPOINT: Final = "wss://socket.fyers.in/hsm/v1-5/prod"
REFERENCE_SDK: Final = "fyers_apiv3==3.2.1"
PARSER_VERSION: Final = "fyers_socket_v0"

#: Project spread-feature staleness default, mirrored here so the ingest layer
#: does not import the pandas/feature stack just for one constant.
DEFAULT_MAX_QUOTE_AGE_MS: Final = 3_000.0

# --- response types -------------------------------------------------------------
RESP_AUTH: Final = 1
RESP_SUBSCRIBE: Final = 4
RESP_UNSUBSCRIBE: Final = 5
RESP_DATA: Final = 6
RESP_PAUSE: Final = 7
RESP_RESUME: Final = 8
RESP_MODE: Final = 12
RESP_CUSTOM: Final = 51

RESPONSE_NAMES: Final[dict[int, str]] = {
    RESP_AUTH: "cn",
    RESP_SUBSCRIBE: "sub",
    RESP_UNSUBSCRIBE: "unsub",
    RESP_PAUSE: "cp",
    RESP_RESUME: "cr",
    RESP_DATA: "data",
    RESP_MODE: "mode",
    RESP_CUSTOM: "msg",
}

# --- scrip block types ----------------------------------------------------------
BLOCK_SNAPSHOT: Final = 83
BLOCK_FULL: Final = 85
BLOCK_LITE: Final = 76

FRAME_KIND_BY_BLOCK: Final[dict[int, str]] = {
    BLOCK_SNAPSHOT: "snapshot",
    BLOCK_FULL: "full",
    BLOCK_LITE: "lite",
}

#: ``INT32_MIN`` sentinel: absent or unchanged, never a real value.
ABSENT: Final = -2_147_483_648

#: Numeric field vectors, in wire order. The trailing ``type``/``symbol``
#: entries of the SDK map are not part of the int vector (the server's
#: ``field_count`` stops before them).
SF_FIELDS: Final[tuple[str, ...]] = (
    "ltp",
    "vol_traded_today",
    "last_traded_time",
    "exch_feed_time",
    "bid_size",
    "ask_size",
    "bid_price",
    "ask_price",
    "last_traded_qty",
    "tot_buy_qty",
    "tot_sell_qty",
    "avg_trade_price",
    "OI",
    "low_price",
    "high_price",
    "Yhigh",
    "Ylow",
    "lower_ckt",
    "upper_ckt",
    "open_price",
    "prev_close_price",
)
IF_FIELDS: Final[tuple[str, ...]] = (
    "ltp",
    "prev_close_price",
    "exch_feed_time",
    "high_price",
    "low_price",
    "open_price",
)
DP_FIELDS: Final[tuple[str, ...]] = tuple(
    [f"bid_price{i}" for i in range(1, 6)]
    + [f"ask_price{i}" for i in range(1, 6)]
    + [f"bid_size{i}" for i in range(1, 6)]
    + [f"ask_size{i}" for i in range(1, 6)]
    + [f"bid_order{i}" for i in range(1, 6)]
    + [f"ask_order{i}" for i in range(1, 6)]
)

FIELDS_BY_TOPIC: Final[dict[str, tuple[str, ...]]] = {
    "sf": SF_FIELDS,
    "if": IF_FIELDS,
    "dp": DP_FIELDS,
}

#: Fields the vendor scales by ``10**precision * multiplier``.
SF_PRICE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "ltp",
        "bid_price",
        "ask_price",
        "avg_trade_price",
        "low_price",
        "high_price",
        "open_price",
        "prev_close_price",
    }
)
IF_PRICE_FIELDS: Final[frozenset[str]] = frozenset(
    {"ltp", "prev_close_price", "high_price", "low_price", "open_price"}
)
DP_PRICE_FIELDS: Final[frozenset[str]] = frozenset(
    {f"bid_price{i}" for i in range(1, 6)} | {f"ask_price{i}" for i in range(1, 6)}
)
PRICE_FIELDS_BY_TOPIC: Final[dict[str, frozenset[str]]] = {
    "sf": SF_PRICE_FIELDS,
    "if": IF_PRICE_FIELDS,
    "dp": DP_PRICE_FIELDS,
}

#: Source-clock fields, in preference order. ``exch_feed_time`` advances with
#: the exchange feed; ``last_traded_time`` can legitimately stand still while
#: no trade prints, so it is a fallback only.
SOURCE_TIME_FIELDS: Final[tuple[str, ...]] = ("exch_feed_time", "last_traded_time")

#: Plausible Unix-epoch-seconds window (2000-01-01 .. 2100-01-01). A value
#: outside it is not reinterpreted as ms/us: it is flagged and left unparsed.
MIN_EPOCH_SECONDS: Final = 946_684_800
MAX_EPOCH_SECONDS: Final = 4_102_444_800

# --- reason codes (stable strings; they land in evidence) -----------------------
REASON_CROSSED: Final = "crossed_quote"
REASON_LOCKED: Final = "locked_quote"
REASON_NON_POSITIVE: Final = "non_positive_price"
REASON_MISSING_SIZE: Final = "missing_size"
REASON_MISSING_PRICE: Final = "missing_price"
REASON_OUT_OF_TICK: Final = "price_off_tick"
REASON_STALE: Final = "stale_quote"
REASON_TIMESTAMP_REGRESSION: Final = "feed_timestamp_regression"
REASON_NO_SNAPSHOT: Final = "awaiting_snapshot"
REASON_RECONNECT: Final = "awaiting_resubscription_snapshot"
REASON_CLOCK_IMPLAUSIBLE: Final = "source_clock_implausible"
REASON_OUTSIDE_SESSION: Final = "outside_session"

#: Reported but non-invalidating: a locked book is a real market state, a clock
#: unit we distrust does not make the price wrong, and a timestamp outside the
#: continuous session is evidence, not corruption.
SOFT_REASONS: Final[frozenset[str]] = frozenset(
    {REASON_LOCKED, REASON_CLOCK_IMPLAUSIBLE, REASON_OUTSIDE_SESSION}
)


class FyersDecodeError(ValueError):
    """A frame could not be decoded without guessing."""


def _u8(data: bytes, offset: int) -> int:
    if offset + 1 > len(data):
        raise FyersDecodeError("truncated uint8")
    return data[offset]


def _u16be(data: bytes, offset: int) -> int:
    if offset + 2 > len(data):
        raise FyersDecodeError("truncated uint16")
    return int(struct.unpack_from("!H", data, offset)[0])


def _u16le(data: bytes, offset: int) -> int:
    if offset + 2 > len(data):
        raise FyersDecodeError("truncated topic id")
    return int(struct.unpack_from("<H", data, offset)[0])


def _u32be(data: bytes, offset: int) -> int:
    if offset + 4 > len(data):
        raise FyersDecodeError("truncated uint32")
    return int(struct.unpack_from("!I", data, offset)[0])


def _i32be(data: bytes, offset: int) -> int:
    if offset + 4 > len(data):
        raise FyersDecodeError("truncated int32")
    return int(struct.unpack_from("!i", data, offset)[0])


def _bytes_field(data: bytes, offset: int, length: int) -> bytes:
    if offset + length > len(data):
        raise FyersDecodeError("truncated string field")
    return data[offset : offset + length]


def parse_source_time(raw: int | None) -> tuple[datetime | None, str | None]:
    """Parse a documented epoch-seconds field, refusing to guess another unit.

    Returns ``(utc, None)`` when plausible and ``(None, REASON_CLOCK_IMPLAUSIBLE)``
    otherwise. The raw integer is always preserved by the caller.
    """
    if raw is None:
        return None, None
    if MIN_EPOCH_SECONDS <= raw <= MAX_EPOCH_SECONDS:
        return datetime.fromtimestamp(raw, tz=UTC), None
    return None, REASON_CLOCK_IMPLAUSIBLE


def on_tick(price: float | None, tick_size: float | None) -> bool:
    """True when ``price`` is a positive multiple of ``tick_size``."""
    if price is None or not tick_size or tick_size <= 0 or price <= 0:
        return False
    quotient = price / tick_size
    return abs(quotient - round(quotient)) < 1e-6


def merge_reasons(existing: Sequence[str], added: Sequence[str]) -> tuple[str, ...]:
    """Existing reasons first, then new ones, de-duplicated and order-stable."""
    out = list(existing)
    for reason in added:
        if reason not in out:
            out.append(reason)
    return tuple(out)


@dataclass(frozen=True, slots=True)
class FyersControlField:
    """One ``id/len/value`` field from a control frame."""

    field_id: int
    value: bytes

    @property
    def text(self) -> str | None:
        try:
            return self.value.decode("ascii")
        except UnicodeDecodeError:
            return None


@dataclass(frozen=True, slots=True)
class FyersBlock:
    """One scrip block inside a data frame."""

    block_type: int
    frame_kind: str
    topic_id: int
    topic_name: str | None
    topic_type: str | None
    exchange: str | None
    exchange_token: str | None
    symbol: str | None
    multiplier: int | None
    precision: int | None
    raw_fields: dict[str, int]
    fields: dict[str, float | int]
    source_time_raw: int | None
    source_time_utc: datetime | None
    source_clock_reason: str | None


@dataclass(frozen=True, slots=True)
class FyersFrame:
    """One decoded outer frame."""

    declared_length: int
    response_type: int
    response_name: str
    message_number: int | None
    scrip_count: int
    blocks: tuple[FyersBlock, ...]
    control_fields: tuple[FyersControlField, ...]

    @property
    def status(self) -> str | None:
        """``K``/``N`` acknowledgement status when the frame carries one."""
        for item in self.control_fields:
            if len(item.value) == 1 and item.value in (b"K", b"N"):
                return item.value.decode("ascii")
        return None


def _parse_control_fields(data: bytes, count: int, offset: int) -> tuple[FyersControlField, ...]:
    fields: list[FyersControlField] = []
    for _ in range(count):
        field_id = _u8(data, offset)
        length = _u16be(data, offset + 1)
        offset += 3
        value = _bytes_field(data, offset, length)
        offset += length
        fields.append(FyersControlField(field_id=field_id, value=value))
    return tuple(fields)


def _read_string(data: bytes, offset: int) -> tuple[str, int]:
    length = _u8(data, offset)
    raw = _bytes_field(data, offset + 1, length)
    return raw.decode("utf-8", errors="replace"), offset + 1 + length


def _source_time(raw_fields: dict[str, int]) -> tuple[int | None, datetime | None, str | None]:
    for name in SOURCE_TIME_FIELDS:
        if name in raw_fields:
            raw = raw_fields[name]
            utc, reason = parse_source_time(raw)
            return raw, utc, reason
    return None, None, None


def _decode_block(
    data: bytes, offset: int, topics: dict[int, tuple[str, str | None, int, int]]
) -> tuple[FyersBlock, int]:
    block_type = _u8(data, offset)
    offset += 1
    frame_kind = FRAME_KIND_BY_BLOCK.get(block_type)
    if frame_kind is None:
        raise FyersDecodeError(f"unknown scrip block type {block_type}")

    topic_id = _u16le(data, offset)
    offset += 2

    topic_name: str | None = None
    topic_type: str | None = None
    exchange: str | None = None
    exchange_token: str | None = None
    symbol: str | None = None
    multiplier: int | None = None
    precision: int | None = None
    raw_fields: dict[str, int] = {}

    if frame_kind == "snapshot":
        topic_name, offset = _read_string(data, offset)
        topic_type = topic_name[:2] if len(topic_name) >= 2 else None
        field_count = _u8(data, offset)
        offset += 1
        names = FIELDS_BY_TOPIC.get(topic_type or "")
        if names is None:
            raise FyersDecodeError(f"unknown topic type {topic_type!r} in {topic_name!r}")
        for index in range(field_count):
            value = _i32be(data, offset)
            offset += 4
            if value != ABSENT:
                raw_fields[names[index]] = value
        # Two reserved bytes precede the multiplier in the reference SDK.
        offset += 2
        multiplier = _u16be(data, offset)
        offset += 2
        precision = _u8(data, offset)
        offset += 1
        exchange, offset = _read_string(data, offset)
        exchange_token, offset = _read_string(data, offset)
        symbol, offset = _read_string(data, offset)
        if multiplier <= 0:
            raise FyersDecodeError("non-positive price multiplier")
        topics[topic_id] = (topic_name, symbol, multiplier, precision)
    else:
        known = topics.get(topic_id)
        if known is None:
            raise FyersDecodeError(f"{frame_kind} block for unknown topic id {topic_id}")
        topic_name, symbol, multiplier, precision = known
        topic_type = topic_name[:2] if len(topic_name) >= 2 else None
        names = FIELDS_BY_TOPIC.get(topic_type or "")
        if names is None:
            raise FyersDecodeError(f"unknown topic type {topic_type!r} for id {topic_id}")
        if frame_kind == "lite":
            values = [_i32be(data, offset)]
            offset += 4
            names = names[:1]
        else:
            field_count = _u8(data, offset)
            offset += 1
            if field_count > len(names):
                raise FyersDecodeError(f"field_count {field_count} exceeds {len(names)} fields")
            values = [_i32be(data, offset + 4 * i) for i in range(field_count)]
            offset += 4 * field_count
        for index, value in enumerate(values):
            if value != ABSENT:
                raw_fields[names[index]] = value

    # Incremental frames inherit the snapshot's scale for their own topic.
    assert multiplier is not None and precision is not None
    scale = (10**precision) * multiplier
    prices = PRICE_FIELDS_BY_TOPIC.get(topic_type or "", frozenset())
    fields: dict[str, float | int] = {
        name: (value / scale if name in prices else value) for name, value in raw_fields.items()
    }

    source_raw, source_utc, clock_reason = _source_time(raw_fields)
    block = FyersBlock(
        block_type=block_type,
        frame_kind=frame_kind,
        topic_id=topic_id,
        topic_name=topic_name,
        topic_type=topic_type,
        exchange=exchange,
        exchange_token=exchange_token,
        symbol=symbol,
        multiplier=multiplier,
        precision=precision,
        raw_fields=raw_fields,
        fields=fields,
        source_time_raw=source_raw,
        source_time_utc=source_utc,
        source_clock_reason=clock_reason,
    )
    return block, offset


def decode_frame(
    raw: bytes, topics: dict[int, tuple[str, str | None, int, int]] | None = None
) -> FyersFrame:
    """Decode one binary frame. Raises :class:`FyersDecodeError` when truncated.

    ``topics`` is the caller-owned topic registry. Snapshot blocks add to it and
    incremental blocks read from it, so a decoder must pass the same dict for
    every frame in a connection.
    """
    if len(raw) < 3:
        raise FyersDecodeError("frame shorter than header")
    declared = _u16be(raw, 0)
    if declared != len(raw) - 2:
        raise FyersDecodeError(f"declared length {declared} != actual {len(raw) - 2}")
    response_type = raw[2]
    name = RESPONSE_NAMES.get(response_type, f"unknown:{response_type}")

    if response_type != RESP_DATA:
        count = _u8(raw, 3) if len(raw) > 3 else 0
        control = _parse_control_fields(raw, count, 4) if count else ()
        return FyersFrame(
            declared_length=declared,
            response_type=response_type,
            response_name=name,
            message_number=None,
            scrip_count=0,
            blocks=(),
            control_fields=control,
        )

    if len(raw) < 9:
        raise FyersDecodeError("data frame shorter than header")
    message_number = _u32be(raw, 3)
    scrip_count = _u16be(raw, 7)
    offset = 9
    registry: dict[int, tuple[str, str | None, int, int]] = {} if topics is None else topics
    blocks: list[FyersBlock] = []
    for _ in range(scrip_count):
        block, offset = _decode_block(raw, offset, registry)
        blocks.append(block)
    return FyersFrame(
        declared_length=declared,
        response_type=response_type,
        response_name=name,
        message_number=message_number,
        scrip_count=scrip_count,
        blocks=tuple(blocks),
        control_fields=(),
    )


@dataclass(frozen=True, slots=True)
class FyersPacket:
    """One recorded unit of evidence: a control frame or one scrip block."""

    provider: str
    protocol_version: str
    parser_version: str
    data_origin: str
    generation: int
    response_type: int
    response_name: str
    message_number: int | None
    block: FyersBlock | None
    control_fields: tuple[FyersControlField, ...]
    status: str | None
    received_timestamp_utc: datetime
    received_monotonic_ns: int
    raw_sha256: str
    parse_error: str | None = None

    @property
    def topic_name(self) -> str | None:
        return self.block.topic_name if self.block is not None else None

    @property
    def frame_kind(self) -> str:
        return self.block.frame_kind if self.block is not None else "control"


class FyersPacketDecoder:
    """Stateful decoder: learns topic ids from snapshots for later frames."""

    def __init__(self) -> None:
        self.topics: dict[int, tuple[str, str | None, int, int]] = {}

    def decode(
        self,
        raw: bytes,
        *,
        received_timestamp_utc: datetime,
        received_monotonic_ns: int,
        generation: int = 1,
        data_origin: str = "live",
    ) -> list[FyersPacket]:
        digest = hashlib.sha256(raw).hexdigest()
        try:
            frame = decode_frame(raw, self.topics)
        except FyersDecodeError as exc:
            return [
                FyersPacket(
                    provider=PROVIDER,
                    protocol_version=DATA_SOCKET_VERSION,
                    parser_version=PARSER_VERSION,
                    data_origin=data_origin,
                    generation=generation,
                    response_type=-1,
                    response_name="error",
                    message_number=None,
                    block=None,
                    control_fields=(),
                    status=None,
                    received_timestamp_utc=received_timestamp_utc,
                    received_monotonic_ns=received_monotonic_ns,
                    raw_sha256=digest,
                    parse_error=str(exc),
                )
            ]

        def build(block: FyersBlock | None) -> FyersPacket:
            return FyersPacket(
                provider=PROVIDER,
                protocol_version=DATA_SOCKET_VERSION,
                parser_version=PARSER_VERSION,
                data_origin=data_origin,
                generation=generation,
                response_type=frame.response_type,
                response_name=frame.response_name,
                message_number=frame.message_number,
                block=block,
                control_fields=frame.control_fields,
                status=frame.status,
                received_timestamp_utc=received_timestamp_utc,
                received_monotonic_ns=received_monotonic_ns,
                raw_sha256=digest,
                parse_error=None,
            )

        if not frame.blocks:
            return [build(None)]
        return [build(block) for block in frame.blocks]


@dataclass(frozen=True, slots=True)
class FyersQuoteState:
    """Merged quote state for one FYERS topic."""

    topic_name: str
    topic_type: str | None
    symbol: str | None
    exchange: str | None
    exchange_token: str | None
    generation: int
    frame_kind: str
    message_number: int | None
    source_time_raw: int | None
    source_time_utc: datetime | None
    received_timestamp_utc: datetime
    received_monotonic_ns: int
    last_valid_monotonic_ns: int | None
    last_valid_utc: datetime | None
    fields: dict[str, float | int] = field(default_factory=dict)
    fields_present: tuple[str, ...] = ()
    invalid_reasons: tuple[str, ...] = ()

    @property
    def hard_reasons(self) -> tuple[str, ...]:
        return tuple(r for r in self.invalid_reasons if r not in SOFT_REASONS)

    @property
    def state_valid(self) -> bool:
        return not self.hard_reasons

    @property
    def bid_price_1(self) -> float | int | None:
        return self.fields.get("bid_price1", self.fields.get("bid_price"))

    @property
    def ask_price_1(self) -> float | int | None:
        return self.fields.get("ask_price1", self.fields.get("ask_price"))

    @property
    def bid_qty_1(self) -> float | int | None:
        return self.fields.get("bid_size1", self.fields.get("bid_size"))

    @property
    def ask_qty_1(self) -> float | int | None:
        return self.fields.get("ask_size1", self.fields.get("ask_size"))

    def quote_age_ms(self, now_monotonic_ns: int) -> float | None:
        """Age by the monotonic receipt clock, immune to wall-clock jumps."""
        if self.last_valid_monotonic_ns is None:
            return None
        return max(0.0, (now_monotonic_ns - self.last_valid_monotonic_ns) / 1_000_000.0)

    def to_row(self) -> dict[str, object]:
        return {
            "provider": PROVIDER,
            "topic_name": self.topic_name,
            "topic_type": self.topic_type,
            "symbol": self.symbol,
            "exchange": self.exchange,
            "exchange_token": self.exchange_token,
            "generation": self.generation,
            "frame_kind": self.frame_kind,
            "message_number": self.message_number,
            "source_time_raw": self.source_time_raw,
            "source_time_utc": self.source_time_utc,
            "received_timestamp_utc": self.received_timestamp_utc,
            "received_monotonic_ns": self.received_monotonic_ns,
            "bid_price_1": self.bid_price_1,
            "bid_qty_1": self.bid_qty_1,
            "ask_price_1": self.ask_price_1,
            "ask_qty_1": self.ask_qty_1,
            "fields_present": ",".join(self.fields_present),
            "state_valid": self.state_valid,
            "invalid_reasons": ",".join(self.invalid_reasons),
            "parser_version": PARSER_VERSION,
        }


class FyersQuoteBook:
    """Quote state, reconnect generations and resubscription bookkeeping."""

    def __init__(
        self,
        tick_sizes: dict[str, float] | None = None,
        duplicate_window: int = 64,
    ) -> None:
        self.tick_sizes: dict[str, float] = dict(tick_sizes or {})
        self.duplicate_window = duplicate_window
        self.states: dict[str, FyersQuoteState] = {}
        self.subscriptions: dict[str, str] = {}
        self.generation = 1
        self.connection_anomalies: list[str] = []
        self.duplicate_updates: dict[str, int] = {}
        self._recent: dict[str, list[str]] = {}

    def begin_generation(self) -> int:
        """Invalidate every state until a fresh snapshot arrives."""
        self.generation += 1
        for key, state in list(self.states.items()):
            self.states[key] = replace(
                state,
                generation=self.generation,
                invalid_reasons=merge_reasons(state.invalid_reasons, (REASON_RECONNECT,)),
            )
        if self.states:
            self.connection_anomalies.append(
                f"reconnect generation {self.generation}: "
                f"{len(self.states)} states awaiting resubscription snapshot"
            )
        return self.generation

    def register_subscription(self, topic_name: str, symbol: str | None = None) -> None:
        self.subscriptions[topic_name] = symbol or ""

    def drop_subscription(self, topic_name: str) -> None:
        self.subscriptions.pop(topic_name, None)

    def plan_resubscription(self) -> list[str]:
        """Every subscribed topic, in stable order.

        The vendor persists no server-side subscription across a reconnect, so
        the client must re-send all of them, not just the invalidated ones.
        """
        return sorted(self.subscriptions)

    def awaiting_snapshot(self) -> list[str]:
        return sorted(
            name
            for name in self.subscriptions
            if name not in self.states or not self.states[name].state_valid
        )

    def apply(self, packet: FyersPacket) -> FyersQuoteState | None:
        """Apply one packet. Returns None for control frames and parse errors."""
        if packet.parse_error is not None:
            self.connection_anomalies.append(
                f"parse_error generation {packet.generation}: {packet.parse_error}"
            )
            return None
        block = packet.block
        if block is None:
            return None
        while self.generation < packet.generation:
            self.begin_generation()

        key = block.topic_name or f"topic:{block.topic_id}"
        previous = self.states.get(key)
        if self._is_duplicate(key, block):
            self.duplicate_updates[key] = self.duplicate_updates.get(key, 0) + 1

        if block.frame_kind == "snapshot":
            fields = dict(block.fields)
            reasons: tuple[str, ...] = ()
        else:
            if previous is None:
                self.connection_anomalies.append(
                    f"{key}: {block.frame_kind} before snapshot (generation {packet.generation})"
                )
                fields = dict(block.fields)
                reasons = (REASON_NO_SNAPSHOT,)
            else:
                # Incremental frames carry a sentinel for every unchanged field.
                fields = {**previous.fields, **block.fields}
                # Reconnect and no-snapshot reasons survive until a real
                # snapshot lands; staleness is recomputed on demand.
                reasons = tuple(r for r in previous.invalid_reasons if r not in (REASON_STALE,))
        present = tuple(sorted(fields))

        state = FyersQuoteState(
            topic_name=key,
            topic_type=block.topic_type,
            symbol=block.symbol or (previous.symbol if previous else None),
            exchange=block.exchange or (previous.exchange if previous else None),
            exchange_token=block.exchange_token or (previous.exchange_token if previous else None),
            generation=self.generation,
            frame_kind=block.frame_kind,
            message_number=packet.message_number,
            source_time_raw=block.source_time_raw,
            source_time_utc=block.source_time_utc,
            received_timestamp_utc=packet.received_timestamp_utc,
            received_monotonic_ns=packet.received_monotonic_ns,
            last_valid_monotonic_ns=previous.last_valid_monotonic_ns if previous else None,
            last_valid_utc=previous.last_valid_utc if previous else None,
            fields=fields,
            fields_present=present,
            invalid_reasons=merge_reasons(
                reasons,
                self._validate(state_fields=fields, block=block, previous=previous),
            ),
        )
        if state.state_valid:
            state = replace(
                state,
                last_valid_monotonic_ns=packet.received_monotonic_ns,
                last_valid_utc=packet.received_timestamp_utc,
            )
        self.states[key] = state
        return state

    def _is_duplicate(self, key: str, block: FyersBlock) -> bool:
        signature = hashlib.sha256(
            repr((block.frame_kind, sorted(block.raw_fields.items()))).encode()
        ).hexdigest()
        recent = self._recent.setdefault(key, [])
        duplicate = signature in recent
        recent.append(signature)
        del recent[: max(0, len(recent) - self.duplicate_window)]
        return duplicate

    def _validate(
        self,
        *,
        state_fields: dict[str, float | int],
        block: FyersBlock,
        previous: FyersQuoteState | None,
    ) -> tuple[str, ...]:
        found: list[str] = []
        # Validate the merged state, not just the fields this frame changed:
        # an incremental frame leaves untouched levels out of its own vector.
        bid = state_fields.get("bid_price1", state_fields.get("bid_price"))
        ask = state_fields.get("ask_price1", state_fields.get("ask_price"))
        bid_size = state_fields.get("bid_size1", state_fields.get("bid_size"))
        ask_size = state_fields.get("ask_size1", state_fields.get("ask_size"))

        if bid is not None or ask is not None:
            if (bid is not None and bid <= 0) or (ask is not None and ask <= 0):
                found.append(REASON_NON_POSITIVE)
            if bid is None or ask is None:
                found.append(REASON_MISSING_PRICE)
            elif bid > ask:
                found.append(REASON_CROSSED)
            elif bid == ask:
                found.append(REASON_LOCKED)
        if bid is not None and (bid_size is None or bid_size <= 0):
            found.append(REASON_MISSING_SIZE)
        if ask is not None and (ask_size is None or ask_size <= 0):
            found.append(REASON_MISSING_SIZE)

        tick = self.tick_sizes.get(block.symbol or "")
        if tick:
            price_names = (
                {"bid_price1", "ask_price1"}
                if block.topic_type == "dp"
                else {"bid_price", "ask_price", "ltp"}
            )
            if any(
                name in state_fields and not on_tick(float(state_fields[name]), tick)
                for name in price_names
            ):
                found.append(REASON_OUT_OF_TICK)

        if (
            block.source_time_utc is not None
            and previous is not None
            and previous.source_time_utc is not None
            and block.source_time_utc < previous.source_time_utc
        ):
            found.append(REASON_TIMESTAMP_REGRESSION)
        if block.source_clock_reason is not None:
            found.append(block.source_clock_reason)
        if block.source_time_utc is not None and not is_within_session(block.source_time_utc):
            found.append(REASON_OUTSIDE_SESSION)
        return tuple(found)

    def stale_keys(
        self, now_monotonic_ns: int, max_quote_age_ms: float = DEFAULT_MAX_QUOTE_AGE_MS
    ) -> dict[str, float]:
        """Topics whose last valid update is older than the threshold."""
        out: dict[str, float] = {}
        for key, state in self.states.items():
            age = state.quote_age_ms(now_monotonic_ns)
            if age is not None and age > max_quote_age_ms:
                out[key] = age
        return out


@dataclass(frozen=True, slots=True)
class PacketRate:
    """Measured packet rate over the recorded receive-monotonic window."""

    frames: int
    blocks: int
    duration_seconds: float
    frames_per_second: float
    blocks_per_second: float
    per_topic_type: dict[str, int]
    per_topic: dict[str, int]


def measure_packet_rate(packets: Sequence[FyersPacket]) -> PacketRate:
    """Measure rate from the packets' own monotonic receive timestamps."""
    data = [p for p in packets if p.block is not None]
    if not data:
        return PacketRate(0, 0, 0.0, 0.0, 0.0, {}, {})
    start = min(p.received_monotonic_ns for p in data)
    end = max(p.received_monotonic_ns for p in data)
    duration = (end - start) / 1_000_000_000.0
    frame_keys = {(p.raw_sha256, p.received_monotonic_ns) for p in data}
    per_topic_type: dict[str, int] = {}
    per_topic: dict[str, int] = {}
    for packet in data:
        block = packet.block
        assert block is not None
        topic_type = block.topic_type or "unknown"
        per_topic_type[topic_type] = per_topic_type.get(topic_type, 0) + 1
        topic = block.topic_name or f"topic:{block.topic_id}"
        per_topic[topic] = per_topic.get(topic, 0) + 1
    frames = len(frame_keys)
    return PacketRate(
        frames=frames,
        blocks=len(data),
        duration_seconds=duration,
        frames_per_second=(frames / duration) if duration > 0 else 0.0,
        blocks_per_second=(len(data) / duration) if duration > 0 else 0.0,
        per_topic_type=per_topic_type,
        per_topic=per_topic,
    )
