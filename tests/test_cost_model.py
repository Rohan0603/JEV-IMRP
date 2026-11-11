"""Gate 11.2: every statutory fee component reconciles EXACTLY to hand calculations.

Fixtures in tests/fixtures/handcalculated/cost_cases.json were worked out by hand
from the dated broker schedule. Reconciliation is exact Decimal equality - no
tolerances, because "close enough" is how a cost model silently drifts.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from jev_imrp.execution.cost_model import (
    CRORE,
    CostScheduleError,
    FeeSchedule,
    LegFees,
    SlippageScenario,
    load_cost_schedule,
)
from jev_imrp.execution.fills import Fill, FillStatus, Leg, Side
from jev_imrp.execution.portfolio import realise_trade

FIXTURES = json.loads(
    (Path(__file__).parent / "fixtures" / "handcalculated" / "cost_cases.json").read_text("utf-8")
)


@pytest.fixture(scope="module")
def schedule() -> FeeSchedule:
    return load_cost_schedule()


def _fees_for(schedule: FeeSchedule, case: dict[str, Any]) -> LegFees:
    return schedule.leg_fees(
        turnover_inr=Decimal(case["turnover_inr"]),
        side=case["side"],
        on=date.fromisoformat(case["on"]),
        include_ipft=case.get("include_ipft"),
    )


def _cases() -> list[dict[str, Any]]:
    return FIXTURES["cases"]


@pytest.mark.parametrize("case", _cases(), ids=[c["name"] for c in _cases()])
def test_hand_calculated_leg_fees(schedule: FeeSchedule, case: dict[str, Any]) -> None:
    actual = _fees_for(schedule, case).as_dict()
    expected = case["expected"]
    assert set(actual) == set(expected), "fee component set drifted from the fixture"
    for component, value in expected.items():
        assert actual[component] == Decimal(value), f"{case['name']}/{component}"


def test_every_case_total_equals_sum_of_components(schedule: FeeSchedule) -> None:
    for case in _cases():
        fees = _fees_for(schedule, case)
        components = [v for k, v in fees.as_dict().items() if k != "total"]
        assert fees.total == sum(components, Decimal(0)), case["name"]


def test_brokerage_is_not_a_constant_rate(schedule: FeeSchedule) -> None:
    """The FYERS Rs 20 cap makes the effective rate notional-dependent."""
    on = date(2026, 11, 10)
    below = schedule.leg_fees(turnover_inr=Decimal(10000), side="buy", on=on)
    above = schedule.leg_fees(turnover_inr=Decimal(100000), side="buy", on=on)
    assert below.brokerage == Decimal("3.00")  # 0.03% of 10000
    assert above.brokerage == Decimal("20.00")  # capped, not 30.00
    below_rate_bps = below.brokerage / Decimal(10000) * Decimal(10_000)
    above_rate_bps = above.brokerage / Decimal(100000) * Decimal(10_000)
    assert below_rate_bps == Decimal("3.0")  # 0.03% == 3 bps
    assert above_rate_bps == Decimal("2.0")  # capped at Rs 20 -> 0.02% == 2 bps
    assert below_rate_bps != above_rate_bps

    # Crossing point: 0.03% * T == Rs 20 at T = 20 / 0.0003.
    crossing = Decimal(20) / (Decimal("0.03") / Decimal(100))
    for turnover in (crossing - Decimal(1), crossing + Decimal(1)):
        fees = schedule.leg_fees(turnover_inr=turnover, side="buy", on=on)
        assert fees.brokerage <= Decimal(20)


def test_stt_is_sell_side_only(schedule: FeeSchedule) -> None:
    on = date(2026, 11, 10)
    buy = schedule.leg_fees(turnover_inr=Decimal(50000), side="buy", on=on)
    sell = schedule.leg_fees(turnover_inr=Decimal(50000), side="sell", on=on)
    assert buy.stt == Decimal(0)
    assert sell.stt == Decimal(50000) * Decimal("0.025") / Decimal(100)


def test_stamp_duty_is_buy_side_lower_of_form(schedule: FeeSchedule) -> None:
    on = date(2026, 11, 10)
    buy = schedule.leg_fees(turnover_inr=Decimal(123456), side="buy", on=on)
    sell = schedule.leg_fees(turnover_inr=Decimal(123456), side="sell", on=on)
    assert sell.stamp_duty == Decimal(0)
    by_percent = Decimal(123456) * Decimal("0.003") / Decimal(100)
    by_absolute = Decimal(123456) * Decimal(300) / CRORE
    assert buy.stamp_duty == min(by_percent, by_absolute)
    # The configured branches coincide exactly (Rs 300 per crore == 0.003%).
    assert by_percent == by_absolute


def test_gst_base_includes_public_fyers_ipft(schedule: FeeSchedule) -> None:
    on = date(2026, 11, 10)
    turnover = Decimal(75000)
    fees = schedule.leg_fees(turnover_inr=turnover, side="sell", on=on)
    base = (
        fees.brokerage
        + fees.sebi_turnover_fee
        + fees.exchange_transaction_fee
        + fees.ipft
    )
    assert fees.gst == base * Decimal(18) / Decimal(100)
    # STT and stamp duty are not in the base: adding them would change the GST.
    with_stt = base + fees.stt
    assert with_stt * Decimal("0.18") != fees.gst


def test_ipft_is_present_by_default_under_the_fyers_schedule(schedule: FeeSchedule) -> None:
    on = date(2026, 11, 10)
    off = schedule.leg_fees(turnover_inr=Decimal(1000000), side="buy", on=on)
    no_ipft = schedule.leg_fees(
        turnover_inr=Decimal(1000000), side="buy", on=on, include_ipft=False
    )
    expected_ipft = Decimal(1000000) * Decimal("0.01") / CRORE
    assert off.ipft == expected_ipft
    assert no_ipft.ipft == Decimal(0)
    assert off.gst == (no_ipft.gst + expected_ipft * Decimal("0.18")).quantize(
        Decimal("1E-12")
    )


def test_exchange_rate_is_versioned_by_effective_date(schedule: FeeSchedule) -> None:
    turnover = Decimal(100000)
    before = schedule.leg_fees(turnover_inr=turnover, side="buy", on=date(2026, 11, 7))
    on_boundary = schedule.leg_fees(turnover_inr=turnover, side="buy", on=date(2026, 11, 8))
    after = schedule.leg_fees(turnover_inr=turnover, side="buy", on=date(2026, 11, 10))
    expected = turnover * Decimal("0.0030699") / Decimal(100)
    assert before.exchange_transaction_fee == expected
    assert on_boundary.exchange_transaction_fee == expected
    assert after.exchange_transaction_fee == expected


@pytest.mark.parametrize(
    "round_trip", FIXTURES["round_trips"], ids=[r["name"] for r in FIXTURES["round_trips"]]
)
def test_hand_calculated_round_trip(schedule: FeeSchedule, round_trip: dict[str, Any]) -> None:
    """Both legs reconciled into one round trip; fees are charged twice, once per leg."""
    on = date.fromisoformat(round_trip["on"])
    quantity = int(round_trip["quantity"])
    entry_price = Decimal(round_trip["entry_price"])
    exit_price = Decimal(round_trip["exit_price"])
    position_side = round_trip["position_side"]
    entry_side, exit_side = ("buy", "sell") if position_side == "long" else ("sell", "buy")
    entry_fees = schedule.leg_fees(turnover_inr=entry_price * quantity, side=entry_side, on=on)
    exit_fees = schedule.leg_fees(turnover_inr=exit_price * quantity, side=exit_side, on=on)

    result = realise_trade(
        position_side=position_side,
        quantity=quantity,
        entry_fill=_filled(position_side, "entry", entry_side, quantity, entry_price),
        exit_fill=_filled(position_side, "exit", exit_side, quantity, exit_price),
        entry_fees=entry_fees,
        exit_fees=exit_fees,
    )
    expected = round_trip["expected"]
    assert result.fees_total_inr == Decimal(expected["fees_total_inr"])
    assert result.gross_pnl_inr == Decimal(expected["gross_pnl_inr"])
    assert result.net_pnl_inr == Decimal(expected["net_pnl_inr"])
    assert result.defined_entry_notional == Decimal(expected["defined_entry_notional_inr"])
    assert result.net_bps == Decimal(expected["net_bps"])


def _filled(position_side: str, leg: str, side: str, quantity: int, price: Decimal) -> Fill:
    stamp = datetime(2026, 10, 5, 4, 0, tzinfo=UTC)
    return Fill(
        status=FillStatus.FILLED,
        leg=Leg(leg),
        position_side=position_side,
        order_side=Side(side),
        quantity=quantity,
        decision_time=stamp,
        arrival_time=stamp,
        price=price,
        touchline="ask1" if side == "buy" else "bid1",
        quote_ts=stamp,
        quote_age_ms=0,
        reason=None,
    )


def test_slippage_scenario_refuses_unresolved_config() -> None:
    with pytest.raises(CostScheduleError):
        SlippageScenario.from_config({"name": "stress_1", "slippage_bps_per_leg": None})
    scenario = SlippageScenario.from_config(
        {"name": "base", "slippage_bps_per_leg": "5", "impact_bps_per_leg": "0"}
    )
    assert scenario.amount_for_leg(Decimal(10000)) == Decimal("5.00")


def test_negative_turnover_is_rejected(schedule: FeeSchedule) -> None:
    with pytest.raises(CostScheduleError):
        schedule.leg_fees(turnover_inr=Decimal(-1), side="buy", on=date(2026, 11, 10))
