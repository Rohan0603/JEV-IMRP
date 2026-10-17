"""Fill model tests (plan 8.3): side conventions, latency, quote age, unfillable.

Synthetic quotes only - every price in this file is invented for the test.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from jev_imrp.common.config import load_execution_policy
from jev_imrp.common.timeutils import IST
from jev_imrp.execution.fills import (
    Fill,
    FillPolicy,
    FillStatus,
    Leg,
    Quote,
    Side,
    UnfillableReason,
    order_side,
    simulate_fill,
)

DAY = datetime(2026, 10, 5, 9, 30, tzinfo=IST)
POLICY = FillPolicy(entry_latency_ms=300, max_quote_age_ms=2000)


def q(offset_s: float, bid: str | None, ask: str | None) -> Quote:
    return Quote(
        bid1=Decimal(bid) if bid is not None else None,
        ask1=Decimal(ask) if ask is not None else None,
        quote_ts=DAY + timedelta(seconds=offset_s),
    )


def test_marketable_side_conventions_per_side() -> None:
    """Long entry asks, long exit bids, short entry bids, short exit asks."""
    assert order_side("long", Leg.ENTRY) is Side.BUY
    assert order_side("long", Leg.EXIT) is Side.SELL
    assert order_side("short", Leg.ENTRY) is Side.SELL
    assert order_side("short", Leg.EXIT) is Side.BUY

    quotes = [q(0, "99.90", "100.00")]
    long_entry = simulate_fill(
        decision_time=DAY, position_side="long", leg=Leg.ENTRY, quantity=10,
        quotes=quotes, policy=POLICY,
    )
    long_exit = simulate_fill(
        decision_time=DAY, position_side="long", leg=Leg.EXIT, quantity=10,
        quotes=quotes, policy=POLICY,
    )
    short_entry = simulate_fill(
        decision_time=DAY, position_side="short", leg=Leg.ENTRY, quantity=10,
        quotes=quotes, policy=POLICY,
    )
    short_exit = simulate_fill(
        decision_time=DAY, position_side="short", leg=Leg.EXIT, quantity=10,
        quotes=quotes, policy=POLICY,
    )
    assert long_entry.price == Decimal("100.00")  # ask1
    assert long_exit.price == Decimal("99.90")  # bid1
    assert short_entry.price == Decimal("99.90")  # bid1
    assert short_exit.price == Decimal("100.00")  # ask1
    assert long_entry.touchline == "ask1"
    assert short_entry.touchline == "bid1"


def test_latency_moves_arrival_and_picks_the_point_in_time_quote() -> None:
    """A quote newer than the modelled arrival must never be used."""
    quotes = [q(0, "99.90", "100.00"), q(10, "100.50", "100.60")]
    fill = simulate_fill(
        decision_time=DAY, position_side="long", leg=Leg.ENTRY, quantity=1,
        quotes=quotes, policy=POLICY,
    )
    assert fill.arrival_time == DAY + timedelta(milliseconds=300)
    assert fill.quote_ts == DAY
    assert fill.quote_age_ms == 300
    assert fill.price == Decimal("100.00")

    slow = FillPolicy(entry_latency_ms=11_000, max_quote_age_ms=2000)
    late = simulate_fill(
        decision_time=DAY, position_side="long", leg=Leg.ENTRY, quantity=1,
        quotes=quotes, policy=slow,
    )
    assert late.quote_ts == DAY + timedelta(seconds=10)
    assert late.price == Decimal("100.60")
    assert late.quote_age_ms == 1000


def test_stale_quote_is_unfillable_and_never_invents_a_price() -> None:
    quotes = [q(0, "99.90", "100.00")]
    fill = simulate_fill(
        decision_time=DAY, position_side="long", leg=Leg.ENTRY, quantity=1,
        quotes=quotes, policy=FillPolicy(entry_latency_ms=300, max_quote_age_ms=200),
    )
    assert fill.status is FillStatus.UNFILLABLE
    assert fill.reason is UnfillableReason.STALE_QUOTE
    assert fill.price is None
    assert fill.quote_age_ms == 300
    with pytest.raises(ValueError):
        _ = fill.turnover_inr


def test_missing_touchline_is_unfillable() -> None:
    quotes = [q(0, "99.90", None)]  # no ask: a long entry cannot be priced
    fill = simulate_fill(
        decision_time=DAY, position_side="long", leg=Leg.ENTRY, quantity=1,
        quotes=quotes, policy=POLICY,
    )
    assert fill.status is FillStatus.UNFILLABLE
    assert fill.reason is UnfillableReason.MISSING_TOUCHLINE
    assert fill.price is None


def test_no_quote_at_or_before_arrival_is_unfillable() -> None:
    quotes = [q(5, "99.90", "100.00")]  # entirely in the future of the arrival
    fill = simulate_fill(
        decision_time=DAY, position_side="long", leg=Leg.ENTRY, quantity=1,
        quotes=quotes, policy=POLICY,
    )
    assert fill.status is FillStatus.UNFILLABLE
    assert fill.reason is UnfillableReason.NO_QUOTE_AT_OR_BEFORE_ARRIVAL
    assert fill.price is None


def test_empty_quote_stream_is_unfillable() -> None:
    fill = simulate_fill(
        decision_time=DAY, position_side="long", leg=Leg.ENTRY, quantity=1,
        quotes=[], policy=POLICY,
    )
    assert fill.reason is UnfillableReason.MISSING_QUOTE


def test_invalid_quotes_are_unfillable() -> None:
    non_positive = simulate_fill(
        decision_time=DAY, position_side="long", leg=Leg.ENTRY, quantity=1,
        quotes=[q(0, "0.00", "0.00")], policy=POLICY,
    )
    assert non_positive.reason is UnfillableReason.NON_POSITIVE_PRICE
    assert non_positive.price is None

    crossed = simulate_fill(
        decision_time=DAY, position_side="long", leg=Leg.ENTRY, quantity=1,
        quotes=[q(0, "101.00", "100.00")], policy=POLICY,
    )
    assert crossed.reason is UnfillableReason.CROSSED_QUOTE
    assert crossed.price is None


def test_fill_records_decision_arrival_quote_timestamps_and_age() -> None:
    fill = simulate_fill(
        decision_time=DAY, position_side="short", leg=Leg.ENTRY, quantity=7,
        quotes=[q(0, "99.90", "100.00")], policy=POLICY,
    )
    assert isinstance(fill, Fill)
    assert fill.decision_time == DAY
    assert fill.arrival_time - fill.decision_time == timedelta(milliseconds=300)
    assert fill.quote_ts == DAY
    assert fill.quote_age_ms == 300
    assert fill.quantity == 7
    assert fill.position_side == "short"
    assert fill.order_side is Side.SELL
    assert fill.turnover_inr == Decimal("99.90") * 7


def test_fill_policy_refuses_the_still_unresolved_config() -> None:
    """Latency and max quote age are TBD in the frozen policy; never guess them."""
    with pytest.raises(ValueError, match="entry_latency_ms"):
        FillPolicy.from_config(load_execution_policy())


def test_policy_is_rejected_when_a_touchline_age_window_is_empty() -> None:
    with pytest.raises(ValueError):
        FillPolicy(entry_latency_ms=300, max_quote_age_ms=0)


def test_bad_arguments_are_rejected() -> None:
    with pytest.raises(ValueError, match="position_side"):
        simulate_fill(
            decision_time=DAY, position_side="sideways", leg=Leg.ENTRY, quantity=1,
            quotes=[q(0, "1", "2")], policy=POLICY,
        )
    with pytest.raises(ValueError, match="quantity"):
        simulate_fill(
            decision_time=DAY, position_side="long", leg=Leg.ENTRY, quantity=0,
            quotes=[q(0, "1", "2")], policy=POLICY,
        )
    with pytest.raises(ValueError, match="naive datetime"):
        simulate_fill(
            decision_time=datetime(2026, 10, 5, 9, 30), position_side="long", leg=Leg.ENTRY,
            quantity=1, quotes=[q(0, "1", "2")], policy=POLICY,
        )