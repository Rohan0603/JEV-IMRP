"""Execution simulator for a marketable L1 order (plan section 8.3).

Conventions, fixed by ``config/execution_policy.yaml``:

===================  ==========================================
leg                 reference touchline at modelled time
===================  ==========================================
long entry          valid ``ask1`` at modelled arrival
long exit           valid ``bid1`` at modelled exit
short entry         valid ``bid1`` at modelled arrival
short exit          valid ``ask1`` at modelled exit
===================  ==========================================

This is a *model* of execution, not proof of a fill. The order arrival time is
the decision time plus the declared latency assumption, and only quotes at or
before that arrival are visible (point-in-time, never the future).

If the required touchline is missing, non-positive, crossed, or older than the
maximum quote age, the candidate is marked ``UNFILLABLE`` with a reason. A price
is NEVER invented: no mid-price substitution, no last-price substitution, no
zero fill.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any, cast

from jev_imrp.common.config import is_unresolved
from jev_imrp.common.timeutils import ensure_aware, to_utc


class Side(StrEnum):
    """Order side from the broker's point of view."""

    BUY = "buy"
    SELL = "sell"


class Leg(StrEnum):
    """Which leg of the position this order closes or opens."""

    ENTRY = "entry"
    EXIT = "exit"


class FillStatus(StrEnum):
    FILLED = "filled"
    UNFILLABLE = "unfillable"


class UnfillableReason(StrEnum):
    """Predeclared reasons a candidate cannot be filled (policy: unfillable)."""

    MISSING_QUOTE = "missing_quote"
    MISSING_TOUCHLINE = "missing_touchline"
    NON_POSITIVE_PRICE = "non_positive_price"
    CROSSED_QUOTE = "crossed_quote"
    STALE_QUOTE = "stale_quote"
    NO_QUOTE_AT_OR_BEFORE_ARRIVAL = "no_quote_at_or_before_arrival"


def order_side(position_side: str, leg: Leg) -> Side:
    """Broker-side order for a position side and leg (plan 8.3 conventions)."""
    side = str(position_side).strip().lower()
    if side not in ("long", "short"):
        raise ValueError(f"position_side must be 'long' or 'short', got {position_side!r}")
    if leg is Leg.ENTRY:
        return Side.BUY if side == "long" else Side.SELL
    return Side.SELL if side == "long" else Side.BUY


def required_touchline(side: Side) -> str:
    """Which L1 touchline a marketable order of ``side`` needs."""
    return "ask1" if side is Side.BUY else "bid1"


@dataclass(frozen=True, slots=True)
class Quote:
    """One reconstructed L1 snapshot. ``None`` means the touchline was not valid."""

    bid1: Decimal | None
    ask1: Decimal | None
    quote_ts: datetime

    def __post_init__(self) -> None:
        ensure_aware(self.quote_ts)

    def touch(self, name: str) -> Decimal | None:
        return self.bid1 if name == "bid1" else self.ask1


@dataclass(frozen=True, slots=True)
class FillPolicy:
    """Latency and quote-validity assumptions (from config/execution_policy.yaml).

    ``entry_latency_ms`` and ``max_quote_age_ms`` are still ``TBD`` in the frozen
    policy file. They MUST be fixed by a dated amendment before data inspection,
    so this class refuses an unresolved value instead of guessing one.
    """

    entry_latency_ms: int
    max_quote_age_ms: int
    source: str = "<explicit>"

    def __post_init__(self) -> None:
        if self.entry_latency_ms < 0 or self.max_quote_age_ms < 0:
            raise ValueError("latency and max quote age must be non-negative")
        if self.max_quote_age_ms == 0:
            raise ValueError("max_quote_age_ms must be positive: a zero window would fill nothing")

    @classmethod
    def from_config(cls, policy: Mapping[str, Any]) -> FillPolicy:
        entry = policy.get("entry")
        if not isinstance(entry, dict):
            raise ValueError("execution policy has no 'entry' section")
        latency = entry.get("entry_latency_ms")
        max_age = entry.get("max_quote_age_ms")
        unresolved = sorted(
            name for name, value in (("entry_latency_ms", latency), ("max_quote_age_ms", max_age))
            if is_unresolved(value)
        )
        if unresolved:
            raise ValueError(
                f"execution policy has unresolved settings {unresolved}; freeze them by a dated "
                "amendment to PREREGISTRATION.md before data inspection"
            )
        return cls(
            entry_latency_ms=int(cast(int, latency)),
            max_quote_age_ms=int(cast(int, max_age)),
            source="config",
        )

    def arrival_time(self, decision_time: datetime) -> datetime:
        """Simulated order-arrival time: decision time plus declared latency."""
        return decision_time + timedelta(milliseconds=self.entry_latency_ms)


@dataclass(frozen=True, slots=True)
class Fill:
    """Result of simulating one order. ``price is None`` exactly when unfillable."""

    status: FillStatus
    leg: Leg
    position_side: str
    order_side: Side
    quantity: int
    decision_time: datetime
    arrival_time: datetime
    price: Decimal | None
    touchline: str
    quote_ts: datetime | None
    quote_age_ms: int | None
    reason: UnfillableReason | None

    @property
    def fillable(self) -> bool:
        return self.status is FillStatus.FILLED

    @property
    def turnover_inr(self) -> Decimal:
        if self.price is None:
            raise ValueError("unfillable fill has no turnover")
        return self.price * self.quantity

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": str(self.status),
            "leg": str(self.leg),
            "position_side": self.position_side,
            "order_side": str(self.order_side),
            "quantity": self.quantity,
            "decision_time": self.decision_time,
            "arrival_time": self.arrival_time,
            "price": self.price,
            "touchline": self.touchline,
            "quote_ts": self.quote_ts,
            "quote_age_ms": self.quote_age_ms,
            "reason": self.reason,
        }


def latest_quote_asof(quotes: Iterable[Quote], at: datetime) -> Quote | None:
    """The most recent quote with ``quote_ts <= at``; never a future quote."""
    best: Quote | None = None
    for quote in quotes:
        if quote.quote_ts <= at and (best is None or quote.quote_ts > best.quote_ts):
            best = quote
    return best


def simulate_fill(
    *,
    decision_time: datetime,
    position_side: str,
    leg: Leg,
    quantity: int,
    quotes: Sequence[Quote] | Mapping[datetime, Quote],
    policy: FillPolicy,
    exit_latency_ms: int | None = None,
) -> Fill:
    """Simulate one marketable L1 order under ``policy``.

    ``quotes`` is the reconstructed L1 stream available up to the decision
    point; only quotes at or before the modelled arrival are considered.
    ``exit_latency_ms`` defaults to the entry latency when not given.
    """
    ensure_aware(decision_time)
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    side = order_side(position_side, leg)
    touchline = required_touchline(side)
    latency = policy.entry_latency_ms if exit_latency_ms is None else exit_latency_ms
    if latency < 0:
        raise ValueError("latency must be non-negative")
    arrival = decision_time + timedelta(milliseconds=latency)

    def unfilled(reason: UnfillableReason, quote: Quote | None) -> Fill:
        age = int((arrival - quote.quote_ts).total_seconds() * 1000) if quote else None
        return Fill(
            status=FillStatus.UNFILLABLE,
            leg=leg,
            position_side=str(position_side).lower(),
            order_side=side,
            quantity=quantity,
            decision_time=decision_time,
            arrival_time=arrival,
            price=None,
            touchline=touchline,
            quote_ts=quote.quote_ts if quote else None,
            quote_age_ms=age,
            reason=reason,
        )

    stream = list(quotes.values()) if isinstance(quotes, Mapping) else list(quotes)
    if not stream:
        return unfilled(UnfillableReason.MISSING_QUOTE, None)

    quote = latest_quote_asof(stream, arrival)
    if quote is None:
        return unfilled(UnfillableReason.NO_QUOTE_AT_OR_BEFORE_ARRIVAL, None)

    age_ms = int((arrival - quote.quote_ts).total_seconds() * 1000)
    if age_ms > policy.max_quote_age_ms:
        return unfilled(UnfillableReason.STALE_QUOTE, quote)

    price = quote.touch(touchline)
    if price is None:
        return unfilled(UnfillableReason.MISSING_TOUCHLINE, quote)
    if price <= 0:
        return unfilled(UnfillableReason.NON_POSITIVE_PRICE, quote)
    if quote.bid1 is not None and quote.ask1 is not None and quote.bid1 > quote.ask1:
        return unfilled(UnfillableReason.CROSSED_QUOTE, quote)

    return Fill(
        status=FillStatus.FILLED,
        leg=leg,
        position_side=str(position_side).lower(),
        order_side=side,
        quantity=quantity,
        decision_time=to_utc(decision_time),
        arrival_time=to_utc(arrival),
        price=price,
        touchline=touchline,
        quote_ts=quote.quote_ts,
        quote_age_ms=age_ms,
        reason=None,
    )


__all__ = [
    "Fill",
    "FillPolicy",
    "FillStatus",
    "Leg",
    "Quote",
    "Side",
    "UnfillableReason",
    "latest_quote_asof",
    "order_side",
    "required_touchline",
    "simulate_fill",
]