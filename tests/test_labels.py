"""Forward-label tests (plan 8.6 / gate 11.2).

Hand-calculated fixtures in tests/fixtures/handcalculated/label_cases.json.
Synthetic market data only; nothing here reproduces a real market.
"""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from jev_imrp.common.timeutils import from_ist
from jev_imrp.execution.cost_model import FeeSchedule, SlippageScenario, load_cost_schedule
from jev_imrp.execution.fills import FillPolicy, Quote
from jev_imrp.labels.forward_returns import (
    LABEL_VERSION,
    LabelerConfig,
    LabelStatus,
    UnlabelledReason,
    label_candidate,
)

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "handcalculated" / "label_cases.json").read_text("utf-8")
)


def _quotes(case: dict[str, Any] | None = None) -> list[Quote]:
    """Expand the fixture's synthetic 30-second L1 stream deterministically."""
    day = datetime.fromisoformat(FIXTURE["session_date"]).date()
    stop_at: time | None = None
    if case and case.get("stream_stop_at"):
        stop_at = time.fromisoformat(case["stream_stop_at"])
    quotes: list[Quote] = []
    for segment in FIXTURE["quote_segments"]:
        start = time.fromisoformat(segment["from"])
        end = time.fromisoformat(segment["to"])
        step = timedelta(seconds=segment["step_s"])
        cursor = start
        while cursor <= end:
            if stop_at is not None and cursor > stop_at:
                break
            quotes.append(
                Quote(
                    bid1=Decimal(segment["bid"]),
                    ask1=Decimal(segment["ask"]),
                    quote_ts=from_ist(day, cursor),
                )
            )
            cursor = (datetime.combine(day, cursor) + step).time()
    return quotes


@pytest.fixture(scope="module")
def config() -> LabelerConfig:
    return LabelerConfig(
        policy=FillPolicy(
            entry_latency_ms=FIXTURE["latency_ms"], max_quote_age_ms=FIXTURE["max_quote_age_ms"]
        ),
        schedule=load_cost_schedule(),
        policy_version="v1-test",
    )


def _label(case: dict[str, Any], config: LabelerConfig) -> Any:
    return label_candidate(
        symbol="SYNTHETIC",
        position_side=case["position_side"],
        quantity=case["quantity"],
        decision_time=datetime.fromisoformat(case["decision_time"]),
        horizon_minutes=case["horizon_minutes"],
        quotes=_quotes(case),
        config=config,
    )


def _cases() -> list[dict[str, Any]]:
    return FIXTURE["cases"]


@pytest.mark.parametrize("case", _cases(), ids=[c["name"] for c in _cases()])
def test_hand_calculated_label(case: dict[str, Any], config: LabelerConfig) -> None:
    record = _label(case, config)
    expected = case["expected"]
    assert str(record.status) == expected["status"]
    assert record.label_version == LABEL_VERSION
    assert record.cost_schedule_version == config.schedule.version

    if expected["status"] == "unlabelled":
        assert str(record.reason) == expected["reason"]
        assert record.gross_pnl_inr is None
        assert record.net_pnl_inr is None
        assert record.net_bps is None
        assert record.label_available_at is None
        entry_price = record.entry_fill.price if record.entry_fill else None
        assert entry_price == Decimal(expected["entry_price"])
        exit_price = record.exit_fill.price if record.exit_fill else None
        if "exit_price" in expected:
            wanted = expected["exit_price"]
            assert exit_price == (None if wanted is None else Decimal(wanted))
        if "exit_reason" in expected:
            assert record.exit_fill is not None
            assert str(record.exit_fill.reason) == expected["exit_reason"]
        return

    assert record.reason is None
    assert record.entry_fill is not None and record.exit_fill is not None
    assert record.entry_fill.price == Decimal(expected["entry_price"])
    assert record.exit_fill.price == Decimal(expected["exit_price"])
    assert record.gross_pnl_inr == Decimal(expected["gross_pnl_inr"])
    assert record.entry_fees is not None and record.exit_fees is not None
    assert record.entry_fees["total"] == Decimal(expected["entry_fees_total"])
    assert record.exit_fees["total"] == Decimal(expected["exit_fees_total"])
    assert record.fees_total_inr == Decimal(expected["fees_total_inr"])
    assert record.slippage_and_impact_inr == Decimal(expected["slippage_and_impact_inr"])
    assert record.net_pnl_inr == Decimal(expected["net_pnl_inr"])
    assert record.defined_entry_notional_inr == Decimal(expected["defined_entry_notional_inr"])
    assert record.label_interval_start == datetime.fromisoformat(expected["label_interval_start"])
    assert record.label_interval_end == datetime.fromisoformat(expected["label_interval_end"])
    assert record.label_available_at == datetime.fromisoformat(expected["label_available_at"])
    # net_return and net_bps are derived explicitly, never approximated.
    notional = record.defined_entry_notional_inr
    assert notional is not None and record.net_pnl_inr is not None
    assert record.net_return == record.net_pnl_inr / notional
    assert record.net_bps == Decimal(10_000) * record.net_pnl_inr / notional
    if "net_bps" in expected:
        assert record.net_bps == Decimal(expected["net_bps"])
    if "net_return" in expected:
        assert record.net_return == Decimal(expected["net_return"])
    # net_bps is not a one-sided cost subtraction: it equals the signed net
    # return of the round trip over the entry notional.
    assert record.net_bps == Decimal(10_000) * record.net_return


def test_primary_horizon_is_30_minutes_and_secondary_are_marked(config: LabelerConfig) -> None:
    case = next(c for c in _cases() if c["name"] == "long_30m_primary_horizon")
    assert _label(case, config).is_primary_horizon is True
    secondary = next(c for c in _cases() if c["name"] == "short_15m_secondary_horizon")
    assert _label(secondary, config).is_primary_horizon is False
    assert config.primary_horizon_minutes == 30
    assert set(config.horizons_minutes) >= {5, 15, 30, 60}


def test_every_secondary_horizon_labels_from_the_same_stream(config: LabelerConfig) -> None:
    day = datetime.fromisoformat(FIXTURE["session_date"]).date()
    quotes = _quotes()
    for horizon in config.horizons_minutes:
        record = label_candidate(
            symbol="SYNTHETIC",
            position_side="long",
            quantity=100,
            decision_time=from_ist(day, time(9, 30)),
            horizon_minutes=horizon,
            quotes=quotes,
            config=config,
        )
        assert record.status is LabelStatus.LABELLED, horizon
        span = record.label_interval_end - record.label_interval_start  # type: ignore[operator]
        assert span == timedelta(minutes=horizon, milliseconds=FIXTURE["latency_ms"])


def test_session_close_leaves_a_fixed_horizon_unlabelled(config: LabelerConfig) -> None:
    case = next(c for c in _cases() if c["name"] == "long_30m_horizon_leaves_session_no_label")
    record = _label(case, config)
    assert record.reason is UnlabelledReason.HORIZON_BEYOND_SESSION
    assert record.label_interval_end is None
    # the entry fill is still recorded for coverage reporting
    assert record.entry_fill is not None and record.entry_fill.price is not None


def test_data_gap_produces_no_label(config: LabelerConfig) -> None:
    case = next(c for c in _cases() if c["name"] == "missing_future_data_data_gap_no_label")
    record = _label(case, config)
    assert record.reason is UnlabelledReason.EXIT_UNFILLABLE
    assert record.exit_fill is not None and record.exit_fill.price is None
    assert record.net_pnl_inr is None


def test_missing_quote_at_entry_is_not_labelled(config: LabelerConfig) -> None:
    day = datetime.fromisoformat(FIXTURE["session_date"]).date()
    record = label_candidate(
        symbol="SYNTHETIC",
        position_side="long",
        quantity=100,
        decision_time=from_ist(day, time(9, 30)),
        horizon_minutes=30,
        quotes=[],
        config=config,
    )
    assert record.reason is UnlabelledReason.ENTRY_UNFILLABLE
    assert record.net_pnl_inr is None


def test_labels_are_deterministic_for_identical_inputs(config: LabelerConfig) -> None:
    """Same data + same config versions -> byte-identical labels (plan 12, Sprint 3 gate)."""
    for case in _cases():
        first, second = _label(case, config), _label(case, config)
        assert first.as_dict() == second.as_dict(), case["name"]
    # A different schedule version must be recorded, not silently reused.
    other = LabelerConfig(policy=config.policy, schedule=_renamed(config.schedule))
    assert _label(_cases()[0], other).cost_schedule_version == "v2-renamed"


def _renamed(_schedule: FeeSchedule) -> FeeSchedule:
    """Same rates, different declared version: labels must record the version."""
    raw = _raw_schedule()
    raw["cost_schedule_version"] = "v2-renamed"
    return FeeSchedule.from_mapping(raw)


def _raw_schedule() -> dict[str, Any]:
    from jev_imrp.common.config import CONFIG_DIR, load_yaml

    return load_yaml(CONFIG_DIR / "cost_schedule.yaml")


def test_stale_quote_policy_is_shared_by_labels_and_fills(config: LabelerConfig) -> None:
    """Labels must not fill when the fill model would refuse: no double policy."""
    strict = LabelerConfig(
        policy=FillPolicy(entry_latency_ms=FIXTURE["latency_ms"], max_quote_age_ms=100),
        schedule=config.schedule,
    )
    record = label_candidate(
        symbol="SYNTHETIC",
        position_side="long",
        quantity=100,
        decision_time=datetime.fromisoformat(FIXTURE["cases"][0]["decision_time"]),
        horizon_minutes=30,
        quotes=_quotes(),
        config=strict,
    )
    assert record.status is LabelStatus.UNLABELLED
    assert record.entry_fill is not None and record.entry_fill.price is None


def test_slippage_is_charged_separately_from_statutory_fees(config: LabelerConfig) -> None:
    case = _cases()[0]
    stressed = LabelerConfig(
        policy=config.policy,
        schedule=config.schedule,
        slippage=SlippageScenario(name="stress_1", slippage_bps_per_leg=Decimal(10)),
    )
    base_record = _label(case, config)
    stress_record = label_candidate(
        symbol="SYNTHETIC",
        position_side=case["position_side"],
        quantity=case["quantity"],
        decision_time=datetime.fromisoformat(case["decision_time"]),
        horizon_minutes=case["horizon_minutes"],
        quotes=_quotes(),
        config=stressed,
    )
    assert stress_record.entry_fees == base_record.entry_fees
    assert stress_record.exit_fees == base_record.exit_fees
    # 10 bps on each leg's own turnover: 10.00 (entry 10000.00) + 10.04 (exit 10040.00)
    assert stress_record.slippage_and_impact_inr == Decimal("20.04")
    assert stress_record.fees_total_inr == base_record.fees_total_inr
    assert stress_record.net_pnl_inr == base_record.net_pnl_inr - Decimal("20.04")
    assert stress_record.cost_scenario == "stress_1"


def test_short_label_does_not_subtract_an_index_return(config: LabelerConfig) -> None:
    """Short-market adjustment must not leak into the trade-level label (plan 8.6)."""
    case = next(c for c in _cases() if c["name"] == "short_15m_secondary_horizon")
    record = _label(case, config)
    assert record.gross_pnl_inr == Decimal("-10.00")  # pure side-signed price move
    assert record.net_pnl_inr < record.gross_pnl_inr < Decimal(0)
    assert "benchmark" not in json.dumps(record.as_dict(), default=str)


def test_as_dict_is_json_serialisable(config: LabelerConfig) -> None:
    payload = _label(_cases()[0], config).as_dict()
    assert json.loads(json.dumps(payload, default=str))["label_version"] == LABEL_VERSION
    assert payload["net_bps"] is not None


def test_session_bounds_use_ist(config: LabelerConfig) -> None:
    day = datetime.fromisoformat(FIXTURE["session_date"]).date()
    assert from_ist(day, time(9, 15)).utcoffset() == timedelta(hours=5, minutes=30)
    record = label_candidate(
        symbol="SYNTHETIC",
        position_side="long",
        quantity=100,
        decision_time=from_ist(day, time(15, 29)),
        horizon_minutes=5,
        quotes=_quotes(),
        config=config,
    )
    assert record.status is LabelStatus.UNLABELLED  # 15:29 + 5 minutes leaves the session
