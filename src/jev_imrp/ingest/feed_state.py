"""Provider-neutral quote-state reconstruction (plan section 5.4).

The following wire contract applies to the archived Shoonya adapter only;
active providers must supply an equivalent normalized event contract:

* ``t:'t'`` subscribes, with ``k`` = ``#``-delimited ``EXCHANGE|TOKEN`` pairs;
  ``t:'u'`` unsubscribes and ``t:'uk'`` acknowledges.
* The first message per instrument is a full snapshot ``t:'tk'`` carrying every
  currently known field. Every later ``t:'tf'`` carries ONLY changed fields.
* ``t``, ``e`` and ``tk`` are always present; everything else only when changed.
* ``bp1``/``bq1`` and ``sp1``/``sq1`` are LEVEL 1 touchline only. This feed
  exposes no deeper depth, so no levels 2-5 exist here and none are invented.
* Field presence varies by instrument type (index tokens lack the touchline,
  equity tokens lack open interest). Missing stays absent. Never zero-fill.
* On reconnect nothing is persisted server-side: re-send the connect frame and
  EVERY subscription, and wait for fresh ``tk`` snapshots before trusting state.

Rules this module enforces:

* ``tk`` creates/replaces a validated state; ``tf`` merges only present fields.
* A state is invalid until something repairs it; invalid is never silently
  accepted and a raw event is never dropped because state was rejected.
* One code path: the online recorder and the offline replay both call
  :meth:`QuoteStateBook.apply`, so replay parity is structural (plan 5.6.10).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from jev_imrp.ingest.raw_event import RawEvent

# --- invalid / anomaly reason codes (stable strings; they land in Parquet) ---
REASON_CROSSED = "crossed_quote"
REASON_LOCKED = "locked_quote"
REASON_NON_POSITIVE = "non_positive_price"
REASON_MISSING_SIZE = "missing_size"
REASON_MISSING_PRICE = "missing_price"
REASON_OUT_OF_TICK = "price_off_tick"
REASON_STALE = "stale_quote"
REASON_TIMESTAMP_REGRESSION = "feed_timestamp_regression"
REASON_DUPLICATE = "duplicate_update"
REASON_NO_SNAPSHOT = "awaiting_snapshot"
REASON_RECONNECT = "awaiting_resubscription_snapshot"
REASON_PARSE_ERROR = "parser_error"
REASON_UNKNOWN_TYPE = "unknown_message_type"

#: Reasons that are reported but do not invalidate a state.
SOFT_REASONS = frozenset({REASON_LOCKED})

#: Fields that identify or date the message without being market data. ``t``, ``e``
#: and ``tk`` are documented as always present; ``ft``/``ti`` are message-level.
MESSAGE_ONLY_FIELDS = frozenset({"t", "e", "tk", "ft", "ti"})

#: Raw feed key -> state column. Only L1 touchline exists; no levels 2-5.
NUMERIC_FIELDS: dict[str, str] = {
    "pp": "prev_close_price",
    "ls": "last_traded_qty",
    "lp": "last_price",
    "pc": "prev_close",
    "o": "open_price",
    "h": "high_price",
    "l": "low_price",
    "c": "close_price",
    "ap": "average_price",
    "v": "volume",
    "oi": "open_interest",
    "poi": "previous_open_interest",
    "toi": "total_open_interest",
    "bq1": "bid_qty_1",
    "bp1": "bid_price_1",
    "sq1": "ask_qty_1",
    "sp1": "ask_price_1",
    "ts": "trade_timestamp_raw",
}
#: Fields carried through as opaque text (not numeric, never coerced).
TEXT_FIELDS: dict[str, str] = {"ti": "instrument_identifier", "ft": "feed_timestamp_field"}

#: Price fields checked against the instrument master's tick size.
_PRICE_FIELDS = (
    "last_price",
    "bid_price_1",
    "ask_price_1",
    "open_price",
    "high_price",
    "low_price",
    "close_price",
)


def _as_float(value: Any) -> float | None:
    """Coerce a numeric feed field, keeping absent (None) distinct from 0."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def on_tick(price: float | None, tick_size: float | None) -> bool:
    """True when ``price`` is a positive multiple of ``tick_size``.

    An unknown tick size means the rule is unverifiable, which is reported by
    the caller as "no master row", never as an invalid quote.
    """
    if price is None or not tick_size or tick_size <= 0 or price <= 0:
        return False
    quotient = price / tick_size
    return abs(quotient - round(quotient)) < 1e-6


@dataclass(frozen=True, slots=True)
class QuoteState:
    """One reconstructed quote state.

    Frozen: an update produces a new state, which keeps replay comparison and
    audit trails trivial. ``fields_present`` records which raw keys the last
    message carried, so "unchanged" stays distinguishable from "unknown".
    """

    exchange: str
    token: str
    symbol: str | None
    subscription_generation: int
    event_id: str
    message_type: str
    feed_timestamp_utc: datetime | None
    received_timestamp_utc: datetime
    last_valid_update_utc: datetime
    prev_close_price: float | None = None
    last_traded_qty: float | None = None
    last_price: float | None = None
    prev_close: float | None = None
    open_price: float | None = None
    high_price: float | None = None
    low_price: float | None = None
    close_price: float | None = None
    average_price: float | None = None
    volume: float | None = None
    open_interest: float | None = None
    previous_open_interest: float | None = None
    total_open_interest: float | None = None
    bid_qty_1: float | None = None
    bid_price_1: float | None = None
    ask_qty_1: float | None = None
    ask_price_1: float | None = None
    trade_timestamp_raw: str | None = None
    instrument_identifier: str | None = None
    feed_timestamp_field: str | None = None
    fields_present: tuple[str, ...] = ()
    invalid_reasons: tuple[str, ...] = ()
    parser_version: str | None = None

    @property
    def key(self) -> tuple[str, str]:
        return (self.exchange, self.token)

    @property
    def hard_reasons(self) -> tuple[str, ...]:
        return tuple(r for r in self.invalid_reasons if r not in SOFT_REASONS)

    @property
    def state_valid(self) -> bool:
        """Valid only when no hard anomaly is outstanding."""
        return not self.hard_reasons

    @property
    def invalid_reason(self) -> str | None:
        """First hard reason, or None. A stable single-value column."""
        hard = self.hard_reasons
        return hard[0] if hard else None

    def quote_age_ms(self, as_of: datetime) -> float:
        """Milliseconds since the last update that produced a valid state."""
        return max(0.0, (as_of - self.last_valid_update_utc).total_seconds() * 1000.0)

    def to_row(self) -> dict[str, Any]:
        """One ``quote_states`` row (plan 6.1)."""
        bid, ask = self.bid_price_1, self.ask_price_1
        return {
            "event_id": self.event_id,
            "exchange": self.exchange,
            "token": self.token,
            "symbol": self.symbol,
            "subscription_generation": self.subscription_generation,
            "message_type": self.message_type,
            "feed_timestamp_utc": self.feed_timestamp_utc,
            "received_timestamp_utc": self.received_timestamp_utc,
            "last_valid_update_utc": self.last_valid_update_utc,
            "bid_price_1": bid,
            "bid_qty_1": self.bid_qty_1,
            "ask_price_1": ask,
            "ask_qty_1": self.ask_qty_1,
            "last_price": self.last_price,
            "last_traded_qty": self.last_traded_qty,
            "open_price": self.open_price,
            "high_price": self.high_price,
            "low_price": self.low_price,
            "close_price": self.close_price,
            "prev_close_price": self.prev_close_price,
            "average_price": self.average_price,
            "volume": self.volume,
            "open_interest": self.open_interest,
            "bid_ask_spread": None if bid is None or ask is None else ask - bid,
            "quote_age_ms": self.quote_age_ms(self.received_timestamp_utc),
            "state_valid": self.state_valid,
            "invalid_reason": self.invalid_reason,
            "invalid_reasons": ",".join(self.invalid_reasons),
            "fields_present": ",".join(self.fields_present),
            "parser_version": self.parser_version,
        }


class QuoteStateBook:
    """State keyed by (exchange, token), plus subscription/reconnect bookkeeping.

    Used identically online and in replay: the recorder wraps raw payloads into
    :class:`~jev_imrp.ingest.raw_event.RawEvent` and calls :meth:`apply`.
    """

    def __init__(self, tick_sizes: dict[tuple[str, str], float] | None = None,
                 duplicate_window: int = 64) -> None:
        #: (exchange, token) -> tick size from the instrument master. A missing key
        #: means the tick rule is unverifiable, which is not a failure.
        self.tick_sizes: dict[tuple[str, str], float] = dict(tick_sizes or {})
        #: How many recent payload digests to retain per instrument for duplicate
        #: detection. Sized so a replayed burst is caught; a longer outage's
        #: retransmission needs a real sequence check, not a bigger window.
        self.duplicate_window = duplicate_window
        self.states: dict[tuple[str, str], QuoteState] = {}
        self.subscriptions: dict[tuple[str, str], str] = {}
        self.generation: int = 0
        self._recent_payloads: dict[tuple[str, str], list[str]] = {}
        #: Anomalies not attributable to a single state row (connection level).
        self.connection_anomalies: list[str] = []

    # -- connection lifecycle ------------------------------------------------
    def begin_generation(self) -> int:
        """Bump the subscription generation; every state becomes untrusted.

        Called on every (re)connect. The gateway persists nothing, so previous
        state cannot stand in for a fresh ``tk`` snapshot.
        """
        self.generation += 1
        for key, state in self.states.items():
            self.states[key] = replace(
                state,
                subscription_generation=self.generation,
                invalid_reasons=merge_reasons(state.invalid_reasons, (REASON_RECONNECT,)),
            )
        return self.generation

    def register_subscription(self, exchange: str, token: str, symbol: str | None = None) -> None:
        """Record an intended subscription (sent; snapshot not yet seen)."""
        self.subscriptions[(exchange, token)] = symbol or ""

    def drop_subscription(self, exchange: str, token: str) -> None:
        self.subscriptions.pop((exchange, token), None)

    def awaiting_snapshot(self) -> list[tuple[str, str]]:
        """Subscribed keys without valid state in the current generation."""
        return sorted(
            key
            for key in self.subscriptions
            if key not in self.states or not self.states[key].state_valid
        )

    # -- the single update path ---------------------------------------------
    def apply(self, event: Any) -> QuoteState | None:
        """Apply one recorded event and return the new state.

        Returns None when the message carries no instrument (subscribe,
        unsubscribe, ack or connection frame); those are recorded as raw events
        and tracked by the recorder's health counters instead.
        """
        if event.message_type not in ("tk", "tf"):
            return None

        key = (str(event.exchange or ""), str(event.token or ""))
        payload = event.payload
        # t/e/tk identify the message; ft and ti are message metadata. None of them
        # are market fields, so none of them can change a quote value.
        present = tuple(sorted(k for k in payload if k not in MESSAGE_ONLY_FIELDS))
        previous = self.states.get(key)
        duplicate = self._is_duplicate(key, event.raw_payload_json)
        untrusted = previous is None or previous.subscription_generation != self.generation

        if event.message_type == "tk" or previous is None:
            # A snapshot is complete by definition: create or replace. Fields the
            # vendor does not send for this instrument type simply stay absent.
            state = self._fresh_state(event, key, present, payload)
            if previous is None and event.message_type == "tf":
                # An incremental update with no snapshot cannot be interpreted.
                self.connection_anomalies.append(
                    f"{key[0]}|{key[1]}: tf before tk ({event.event_id})"
                )
                state = replace(state, invalid_reasons=(REASON_NO_SNAPSHOT,))
        else:
            updates: dict[str, Any] = {}
            for raw_key, name in NUMERIC_FIELDS.items():
                if raw_key in payload:
                    updates[name] = _as_float(payload[raw_key])
            for raw_key, name in TEXT_FIELDS.items():
                if raw_key in payload:
                    updates[name] = payload[raw_key]
            # Only the fields present in this message change. Everything else,
            # including the previous last price, survives untouched.
            state = replace(
                previous,
                **updates,
                event_id=event.event_id,
                message_type=event.message_type,
                feed_timestamp_utc=event.feed_timestamp_utc,
                received_timestamp_utc=event.received_timestamp_utc,
                fields_present=present,
                subscription_generation=self.generation,
            )
            if untrusted:
                # State predates this connection: keep it readable but invalid
                # until a real snapshot arrives.
                state = replace(
                    state, invalid_reasons=merge_reasons(state.invalid_reasons, (REASON_RECONNECT,))
                )

        state = replace(state, invalid_reasons=self._validate(state, previous, duplicate))
        if state.state_valid:
            state = replace(state, last_valid_update_utc=event.received_timestamp_utc)
        self.states[key] = state
        return state

    # -- helpers ------------------------------------------------------------
    def _fresh_state(self, event: Any, key: tuple[str, str], present: tuple[str, ...],
                     payload: dict[str, Any]) -> QuoteState:
        values: dict[str, Any] = {name: None for name in NUMERIC_FIELDS.values()}
        values.update({name: None for name in TEXT_FIELDS.values()})
        for raw_key, name in NUMERIC_FIELDS.items():
            if raw_key in payload:
                values[name] = _as_float(payload[raw_key])
        for raw_key, name in TEXT_FIELDS.items():
            if raw_key in payload:
                values[name] = payload[raw_key]
        return QuoteState(
            exchange=key[0],
            token=key[1],
            symbol=event.symbol_as_received,
            subscription_generation=self.generation,
            event_id=event.event_id,
            message_type=event.message_type,
            feed_timestamp_utc=event.feed_timestamp_utc,
            received_timestamp_utc=event.received_timestamp_utc,
            last_valid_update_utc=event.received_timestamp_utc,
            fields_present=present,
            parser_version=event.parser_version,
            **values,
        )

    def _is_duplicate(self, key: tuple[str, str], payload_json: str) -> bool:
        recent = self._recent_payloads.setdefault(key, [])
        duplicate = payload_json in recent
        recent.append(payload_json)
        del recent[: max(0, len(recent) - self.duplicate_window)]
        return duplicate

    def _validate(
        self, state: QuoteState, previous: QuoteState | None, duplicate: bool
    ) -> tuple[str, ...]:
        """Collect every outstanding anomaly.

        A fresh ``tk`` snapshot is what repairs an invalid state, so it inherits
        nothing from the update it replaces; an incremental ``tf`` update keeps
        outstanding hard reasons. ``stale_quote`` never inherits because it is
        recomputed from the clock on every read. Reasons the caller already put
        on ``state`` (``awaiting_snapshot``) are always kept.
        """
        inherited: tuple[str, ...] = tuple(state.invalid_reasons)
        if state.message_type != "tk" and previous is not None:
            inherited = merge_reasons(
                inherited,
                tuple(
                    r for r in previous.invalid_reasons if r not in (REASON_STALE, REASON_RECONNECT,
                                                                    REASON_NO_SNAPSHOT)
                ),
            )
        found: list[str] = []
        bid, ask = state.bid_price_1, state.ask_price_1

        if bid is not None or ask is not None:
            if (bid is not None and bid <= 0) or (ask is not None and ask <= 0):
                found.append(REASON_NON_POSITIVE)
            if bid is None or ask is None:
                found.append(REASON_MISSING_PRICE)
            elif bid > ask:
                found.append(REASON_CROSSED)
            elif bid == ask:
                found.append(REASON_LOCKED)
        if bid is not None and (state.bid_qty_1 is None or state.bid_qty_1 <= 0):
            found.append(REASON_MISSING_SIZE)
        if ask is not None and (state.ask_qty_1 is None or state.ask_qty_1 <= 0):
            found.append(REASON_MISSING_SIZE)

        tick = self.tick_sizes.get(state.key)
        if tick and any(
            not on_tick(getattr(state, name), tick)
            for name in _PRICE_FIELDS
            if getattr(state, name) is not None
        ):
            found.append(REASON_OUT_OF_TICK)

        if (
            state.feed_timestamp_utc is not None
            and previous is not None
            and previous.feed_timestamp_utc is not None
            and state.feed_timestamp_utc < previous.feed_timestamp_utc
        ):
            found.append(REASON_TIMESTAMP_REGRESSION)
        if duplicate:
            found.append(REASON_DUPLICATE)

        return merge_reasons(inherited, found)


def merge_reasons(existing: Sequence[str], added: Sequence[str]) -> tuple[str, ...]:
    """Existing reasons first, then new ones, de-duplicated and order-stable."""
    out = list(existing)
    for reason in added:
        if reason not in out:
            out.append(reason)
    return tuple(out)


def replay_raw_events(
    rows: Iterable[Any], tick_sizes: dict[tuple[str, str], float] | None = None
) -> tuple[QuoteStateBook, list[QuoteState]]:
    """Rebuild state from recorded raw events, in recorded order.

    This is the *same* update path the online recorder uses: each row is turned
    back into its ``RawEvent`` and handed to :meth:`QuoteStateBook.apply`. There
    is deliberately no second parser, so replay parity (plan 5.6.10) is a
    property of the design rather than something two implementations must
    coincidentally agree on.
    """
    book = QuoteStateBook(tick_sizes=tick_sizes)
    states: list[QuoteState] = []
    for row in rows:
        event = row if isinstance(row, RawEvent) else RawEvent.from_row(dict(row))
        # Reconnect generations are part of the recorded sequence, not a replay
        # artifact: a session that reconnected must replay the same invalidation.
        generation = event.subscription_generation
        while book.generation < generation:
            book.begin_generation()
        state = book.apply(event)
        if state is not None:
            states.append(state)
    return book, states


def stale_states(
    book: QuoteStateBook, as_of: datetime, max_age_ms: float
) -> dict[tuple[str, str], float]:
    """Keys whose last valid update is older than ``max_age_ms`` at ``as_of``."""
    return {
        key: state.quote_age_ms(as_of)
        for key, state in book.states.items()
        if state.quote_age_ms(as_of) > max_age_ms
    }
