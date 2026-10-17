"""Per-feature unit tests for the point-in-time feature pipeline (plan 7.2/7.5).

All inputs are deterministic SYNTHETIC fixtures. No real market data is used or
implied; every expected value below is hand-computed in the test.
"""

from __future__ import annotations

import math
from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest

from jev_imrp.common.timeutils import (
    SESSION_CLOSE,
    SESSION_OPEN,
    from_ist,
    minutes_since_open,
    to_utc,
)
from jev_imrp.features import (
    TRAINING_FEATURES,
    FeatureEngine,
    FeatureParams,
    assert_feature_set,
    assert_no_leaks,
)
from jev_imrp.features.causal_checks import FeatureLeakError
from jev_imrp.features.definitions import ATR_LOOKBACK_BARS, REL_VOLUME_PRIOR_SESSIONS

SYNTH_SYMBOL = "SYNTH1"  # synthetic instrument, no exchange listing
PRIOR_DAY = date(2026, 10, 14)
TODAY = date(2026, 10, 15)

PARAMS = FeatureParams()  # frozen defaults from definitions.py


# --------------------------------------------------------------------------
# Synthetic fixture builders
# --------------------------------------------------------------------------


def bar(
    day: date,
    minute_index: int,
    *,
    open_price: float = 100.0,
    high_price: float = 101.0,
    low_price: float = 99.0,
    close_price: float = 100.0,
    volume: float = 100.0,
    is_complete: bool = True,
    has_gap: bool = False,
    availability_delay_s: float = 0.0,
    symbol: str = SYNTH_SYMBOL,
) -> dict:
    """One synthetic minute bar. Availability is when the final values are known."""
    start = to_utc(from_ist(day, SESSION_OPEN)) + timedelta(minutes=minute_index)
    end = start + timedelta(minutes=1)
    return {
        "symbol": symbol,
        "bar_start_utc": start,
        "bar_end_utc": end,
        "open_price": open_price,
        "high_price": high_price,
        "low_price": low_price,
        "close_price": close_price,
        "volume": volume,
        "is_complete": is_complete,
        "has_gap": has_gap,
        "available_timestamp_utc": end + timedelta(seconds=availability_delay_s),
    }


def bars(day: date, count: int, *, start_minute: int = 0, **kwargs) -> pd.DataFrame:
    return pd.DataFrame([bar(day, start_minute + i, **kwargs) for i in range(count)])


def quote(
    event_id: str,
    ts_utc: datetime,
    *,
    bid_price: float = 99.5,
    bid_qty: float = 300.0,
    ask_price: float = 100.5,
    ask_qty: float = 100.0,
    last_traded_price: float = 100.0,
    last_traded_volume: float = 0.0,
    quote_age_ms: float = 0.0,
    state_valid: bool = True,
    availability_delay_s: float = 0.0,
    symbol: str = SYNTH_SYMBOL,
) -> dict:
    """One synthetic L1 quote-state row. Level 1 touchline only (plan 7.3)."""
    return {
        "event_id": event_id,
        "symbol": symbol,
        "feed_timestamp_utc": ts_utc,
        "received_timestamp_utc": ts_utc + timedelta(seconds=availability_delay_s),
        "bid_price_1": bid_price,
        "bid_qty_1": bid_qty,
        "ask_price_1": ask_price,
        "ask_qty_1": ask_qty,
        "last_traded_price": last_traded_price,
        "last_traded_volume": last_traded_volume,
        "quote_age_ms": quote_age_ms,
        "state_valid": state_valid,
    }


def context_row(
    series: str, ts_utc: datetime, value: float, *, availability_delay_s: float = 0.0
) -> dict:
    return {
        "series": series,
        "value": value,
        "source_timestamp_utc": ts_utc,
        "available_timestamp_utc": ts_utc + timedelta(seconds=availability_delay_s),
    }


def at(day: date, minute_index: int, *, seconds: int = 0) -> datetime:
    """UTC timestamp ``minute_index`` minutes after the IST session open."""
    return to_utc(from_ist(day, SESSION_OPEN)) + timedelta(minutes=minute_index, seconds=seconds)


def engine_with(
    quotes: pd.DataFrame | None = None,
    bars_frame: pd.DataFrame | None = None,
    context: pd.DataFrame | None = None,
) -> FeatureEngine:
    return FeatureEngine(
        quotes if quotes is not None else pd.DataFrame(),
        bars_frame if bars_frame is not None else pd.DataFrame(),
        context if context is not None else pd.DataFrame(),
        params=PARAMS,
    )


def default_engine(**kwargs) -> FeatureEngine:
    """Prior session + 30 current-session bars + one fresh quote."""
    return engine_with(
        quotes=pd.DataFrame([quote("q0", at(TODAY, 29))]),
        bars_frame=pd.concat([bars(PRIOR_DAY, 375), bars(TODAY, 30)], ignore_index=True),
        **kwargs,
    )


# --------------------------------------------------------------------------
# spread_bps / touch_size_imbalance (L1 touchline)
# --------------------------------------------------------------------------


def test_spread_bps_matches_hand_computation() -> None:
    # bid 99.5, ask 100.5 -> midpoint 100.0, spread 1.0 -> 10000 * 1.0 / 100.0 = 100 bps.
    record = default_engine().compute(SYNTH_SYMBOL, at(TODAY, 29))
    assert record.features["spread_bps"].value == pytest.approx(100.0)
    assert record.features["spread_bps"].valid


def test_spread_bps_and_imbalance_are_null_for_a_stale_quote() -> None:
    engine = engine_with(quotes=pd.DataFrame([quote("q0", at(TODAY, 29))]))
    record = engine.compute(SYNTH_SYMBOL, at(TODAY, 29, seconds=60))
    assert record.features["spread_bps"].quality == "stale_quote"
    assert record.features["spread_bps"].value is None
    assert record.features["spread_bps"].valid is False


def test_spread_bps_is_null_when_the_feed_flags_the_state_invalid() -> None:
    engine = engine_with(quotes=pd.DataFrame([quote("q0", at(TODAY, 29), state_valid=False)]))
    record = engine.compute(SYNTH_SYMBOL, at(TODAY, 29))
    assert record.features["spread_bps"].quality == "invalid_quote_state"
    assert record.features["spread_bps"].value is None


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"bid_price": 0.0}, "invalid_quote_state"),
        ({"ask_price": 0.0}, "invalid_quote_state"),
        ({"bid_price": 101.0, "ask_price": 100.0}, "invalid_quote_state"),  # crossed
    ],
)
def test_non_positive_or_crossed_touchline_is_never_a_valid_observation(kwargs, reason) -> None:
    engine = engine_with(quotes=pd.DataFrame([quote("q0", at(TODAY, 29), **kwargs)]))
    record = engine.compute(SYNTH_SYMBOL, at(TODAY, 29))
    assert record.features["spread_bps"].quality == reason
    assert record.features["spread_bps"].value is None


def test_touch_size_imbalance_matches_hand_computation() -> None:
    # (300 - 100) / (300 + 100) = 0.5
    record = default_engine().compute(SYNTH_SYMBOL, at(TODAY, 29))
    assert record.features["touch_size_imbalance"].value == pytest.approx(0.5)


def test_touch_size_imbalance_is_null_flagged_when_denominator_is_zero() -> None:
    engine = engine_with(
        quotes=pd.DataFrame([quote("q0", at(TODAY, 29), bid_qty=0.0, ask_qty=0.0)])
    )
    record = engine.compute(SYNTH_SYMBOL, at(TODAY, 29))
    assert record.features["touch_size_imbalance"].value is None
    assert record.features["touch_size_imbalance"].valid is False
    assert record.features["touch_size_imbalance"].quality == "zero_denominator"


def test_l1_feature_is_not_named_as_depth() -> None:
    # plan 7.3: the feed is a touchline; nothing may claim multi-level depth.
    assert "touch_size_imbalance" in TRAINING_FEATURES
    assert not any("depth" in name for name in TRAINING_FEATURES)


# --------------------------------------------------------------------------
# atr_pct
# --------------------------------------------------------------------------


def test_atr_pct_matches_hand_computation_on_constant_ranges() -> None:
    # Each bar: high 101, low 99, close 100 and previous close 100
    # -> TR = max(101-99, |101-100|, |99-100|) = 2 for every bar.
    # 14 TRs -> ATR = 2; ATR / close = 2 / 100 = 2%.
    engine = default_engine()
    record = engine.compute(SYNTH_SYMBOL, at(TODAY, 29))
    assert record.features["atr_pct"].value == pytest.approx(2.0)


def test_atr_pct_is_in_warmup_before_lookback_plus_one_completed_bars() -> None:
    engine = engine_with(bars_frame=bars(TODAY, ATR_LOOKBACK_BARS))
    record = engine.compute(SYNTH_SYMBOL, at(TODAY, ATR_LOOKBACK_BARS - 1))
    assert record.features["atr_pct"].quality == "warmup"
    assert record.features["atr_pct"].value is None


def test_atr_pct_ignores_incomplete_bars() -> None:
    frame = pd.concat(
        [bars(TODAY, 10), bars(TODAY, 5, start_minute=15, is_complete=False)], ignore_index=True
    )
    engine = engine_with(bars_frame=frame)
    record = engine.compute(SYNTH_SYMBOL, at(TODAY, 19))
    # Only 10 of the 15 bars are complete, so ATR is still warming up.
    assert record.features["atr_pct"].quality == "warmup"


def test_atr_pct_uses_the_frozen_lookback_window_only() -> None:
    # A wild bar older than the frozen 14-bar window must not move ATR.
    quiet = bars(TODAY, 30)
    wild = pd.concat(
        [bars(TODAY, 1, high_price=500.0, low_price=1.0, close_price=100.0), quiet.iloc[1:]],
        ignore_index=True,
    )
    baseline = engine_with(bars_frame=quiet).compute(SYNTH_SYMBOL, at(TODAY, 30))
    with_wild = engine_with(bars_frame=wild).compute(SYNTH_SYMBOL, at(TODAY, 30))
    assert with_wild.features["atr_pct"].value == pytest.approx(baseline.features["atr_pct"].value)


# --------------------------------------------------------------------------
# relative_volume
# --------------------------------------------------------------------------


def test_relative_volume_is_current_over_point_in_time_reference() -> None:
    # Prior session: 375 bars of 10 qty = 3750 by minute 29.
    # Current session: 30 bars of 20 qty = 600 by minute 29.
    # Reference at 30 minutes into the prior session = 30 * 10 = 300.
    # 600 / 300 = 2.0
    engine = engine_with(
        bars_frame=pd.concat(
            [bars(PRIOR_DAY, 375, volume=10.0), bars(TODAY, 30, volume=20.0)], ignore_index=True
        )
    )
    record = engine.compute(SYNTH_SYMBOL, at(TODAY, 30))
    assert record.features["relative_volume"].value == pytest.approx(2.0)


def test_relative_volume_reference_never_uses_the_final_session_volume() -> None:
    """A huge amount of volume later in the prior session must not change the
    reference used at 10:45 on the current session."""
    early_prior = bars(PRIOR_DAY, 45, volume=10.0)
    late_prior = bars(PRIOR_DAY, 330, start_minute=45, volume=1000.0)
    current = bars(TODAY, 30, volume=20.0)
    without_late = engine_with(
        bars_frame=pd.concat([early_prior, current], ignore_index=True)
    ).compute(SYNTH_SYMBOL, at(TODAY, 30))
    with_late = engine_with(
        bars_frame=pd.concat([early_prior, late_prior, current], ignore_index=True)
    ).compute(SYNTH_SYMBOL, at(TODAY, 30))
    assert without_late.features["relative_volume"].value == pytest.approx(
        with_late.features["relative_volume"].value
    )


def test_relative_volume_is_in_warmup_without_a_prior_session() -> None:
    engine = engine_with(bars_frame=bars(TODAY, 30, volume=20.0))
    record = engine.compute(SYNTH_SYMBOL, at(TODAY, 30))
    assert record.features["relative_volume"].quality == "warmup"
    assert record.features["relative_volume"].value is None


# --------------------------------------------------------------------------
# dist_vwap_pct
# --------------------------------------------------------------------------


def test_dist_vwap_pct_uses_only_trades_available_by_decision_time() -> None:
    trades = pd.DataFrame(
        [
            quote("t1", at(TODAY, 10), last_traded_price=100.0, last_traded_volume=10.0),
            quote("t2", at(TODAY, 20), last_traded_price=102.0, last_traded_volume=10.0),
            quote("t3", at(TODAY, 40), last_traded_price=500.0, last_traded_volume=10.0),
        ]
    )
    record = engine_with(quotes=trades).compute(SYNTH_SYMBOL, at(TODAY, 20))
    # vwap = (100*10 + 102*10) / 20 = 101 ; last price 102 -> 100 * 1 / 101
    assert record.features["dist_vwap_pct"].value == pytest.approx(100.0 / 101.0)
    assert record.features["dist_vwap_pct"].valid


def test_dist_vwap_pct_resets_each_session() -> None:
    trades = pd.DataFrame(
        [
            quote("t1", at(PRIOR_DAY, 10), last_traded_price=10.0, last_traded_volume=10.0),
            quote("t2", at(TODAY, 10), last_traded_price=100.0, last_traded_volume=10.0),
        ]
    )
    record = engine_with(quotes=trades).compute(SYNTH_SYMBOL, at(TODAY, 10))
    # Yesterday's trade is not in today's VWAP: vwap == 100, distance == 0.
    assert record.features["dist_vwap_pct"].value == pytest.approx(0.0)
    assert "session" in record.lineage["vwap"]


def test_dist_vwap_pct_is_in_warmup_without_a_trade() -> None:
    record = default_engine().compute(SYNTH_SYMBOL, at(TODAY, 29))
    assert record.features["dist_vwap_pct"].quality == "warmup"
    assert record.features["dist_vwap_pct"].value is None


# --------------------------------------------------------------------------
# dist_pdh_pct / dist_pdl_pct
# --------------------------------------------------------------------------


def test_dist_pdh_pdl_uses_the_prior_completed_session() -> None:
    quotes = pd.DataFrame([quote("q0", at(TODAY, 29), last_traded_price=100.0)])
    engine = engine_with(quotes=quotes, bars_frame=bars(PRIOR_DAY, 375))
    record = engine.compute(SYNTH_SYMBOL, at(TODAY, 29))
    # prior high 101, low 99, price 100
    assert record.features["dist_pdh_pct"].value == pytest.approx(100.0 * (100.0 - 101.0) / 101.0)
    assert record.features["dist_pdl_pct"].value == pytest.approx(100.0 * (100.0 - 99.0) / 99.0)


def test_dist_pdh_pdl_ignores_todays_own_high_and_low() -> None:
    quotes = pd.DataFrame([quote("q0", at(TODAY, 29), last_traded_price=100.0)])
    engine = engine_with(
        quotes=quotes,
        bars_frame=pd.concat(
            [bars(PRIOR_DAY, 375), bars(TODAY, 30, high_price=200.0, low_price=1.0)],
            ignore_index=True,
        ),
    )
    record = engine.compute(SYNTH_SYMBOL, at(TODAY, 29))
    assert record.features["dist_pdh_pct"].value == pytest.approx(100.0 * (100.0 - 101.0) / 101.0)


def test_dist_pdh_pdl_is_in_warmup_on_the_first_session() -> None:
    quotes = pd.DataFrame([quote("q0", at(TODAY, 29), last_traded_price=100.0)])
    record = engine_with(quotes=quotes, bars_frame=bars(TODAY, 30)).compute(
        SYNTH_SYMBOL, at(TODAY, 29)
    )
    assert record.features["dist_pdh_pct"].quality == "warmup"
    assert record.features["dist_pdh_pct"].value is None


def test_dist_pdh_pdl_is_null_when_the_prior_session_is_too_incomplete() -> None:
    quotes = pd.DataFrame([quote("q0", at(TODAY, 29), last_traded_price=100.0)])
    record = engine_with(quotes=quotes, bars_frame=bars(PRIOR_DAY, 10)).compute(
        SYNTH_SYMBOL, at(TODAY, 29)
    )
    assert record.features["dist_pdh_pct"].quality == "insufficient_prior_session"
    assert record.features["dist_pdh_pct"].value is None


def test_dist_pdh_pdl_flags_a_gap_in_the_prior_session() -> None:
    quotes = pd.DataFrame([quote("q0", at(TODAY, 29), last_traded_price=100.0)])
    frame = pd.concat(
        [bars(PRIOR_DAY, 375), bars(PRIOR_DAY, 1, start_minute=375, has_gap=True)],
        ignore_index=True,
    )
    record = engine_with(quotes=quotes, bars_frame=frame).compute(SYNTH_SYMBOL, at(TODAY, 29))
    assert record.features["dist_pdh_pct"].quality == "gap_in_prior_session"
    assert record.features["dist_pdh_pct"].valid is True


# --------------------------------------------------------------------------
# India VIX
# --------------------------------------------------------------------------


def test_india_vix_uses_the_latest_value_available_by_the_signal_time() -> None:
    context = pd.DataFrame(
        [
            context_row("india_vix", at(TODAY, 10), 14.0),
            context_row("india_vix", at(TODAY, 20), 15.0),
            context_row("india_vix", at(TODAY, 40), 99.0),
        ]
    )
    record = engine_with(context=context).compute(SYNTH_SYMBOL, at(TODAY, 20))
    assert record.features["india_vix"].value == pytest.approx(15.0)
    assert record.features["india_vix_change_pct"].value == pytest.approx(
        100.0 * (15.0 - 14.0) / 14.0
    )


def test_india_vix_change_is_in_warmup_with_a_single_point() -> None:
    context = pd.DataFrame([context_row("india_vix", at(TODAY, 10), 14.0)])
    record = engine_with(context=context).compute(SYNTH_SYMBOL, at(TODAY, 10))
    assert record.features["india_vix"].value == pytest.approx(14.0)
    assert record.features["india_vix_change_pct"].quality == "warmup"


def test_india_vix_is_null_when_the_latest_value_is_too_stale() -> None:
    limit = PARAMS.india_vix_max_staleness_seconds
    context = pd.DataFrame([context_row("india_vix", at(TODAY, 10), 14.0)])
    record = engine_with(context=context).compute(SYNTH_SYMBOL, at(TODAY, 10, seconds=limit + 1))
    assert record.features["india_vix"].value is None
    assert record.features["india_vix"].quality == "stale_context"


def test_india_vix_is_null_when_nothing_was_published() -> None:
    record = engine_with().compute(SYNTH_SYMBOL, at(TODAY, 29))
    assert record.features["india_vix"].value is None
    assert record.features["india_vix"].quality == "missing_observation"


# --------------------------------------------------------------------------
# Nifty returns
# --------------------------------------------------------------------------


def test_nifty_returns_are_causal_multi_horizon() -> None:
    # Levels at minutes 5, 10, 20 with the decision at minute 20 -> 100.0.
    # ret_1 uses the newest point available at least 1 minute before: 100.0.
    # ret_5 uses the newest point available at least 5 minutes before: 101.0.
    # ret_15 uses the newest point available at least 15 minutes before: 110.0.
    context = pd.DataFrame(
        [
            context_row("nifty_index", at(TODAY, 5), 110.0),
            context_row("nifty_index", at(TODAY, 10), 101.0),
            context_row("nifty_index", at(TODAY, 20), 100.0),
            context_row("nifty_index", at(TODAY, 30), 1.0),  # future: must not be used
        ]
    )
    record = engine_with(context=context).compute(SYNTH_SYMBOL, at(TODAY, 20))
    assert record.features["nifty_ret_1_pct"].value == pytest.approx(
        100.0 * (100.0 - 101.0) / 101.0
    )
    assert record.features["nifty_ret_5_pct"].value == pytest.approx(
        100.0 * (100.0 - 101.0) / 101.0
    )
    assert record.features["nifty_ret_15_pct"].value == pytest.approx(
        100.0 * (100.0 - 110.0) / 110.0
    )


def test_nifty_return_is_in_warmup_when_no_point_predates_the_horizon() -> None:
    context = pd.DataFrame([context_row("nifty_index", at(TODAY, 10), 100.0)])
    record = engine_with(context=context).compute(SYNTH_SYMBOL, at(TODAY, 11))
    assert record.features["nifty_ret_15_pct"].quality == "warmup"
    assert record.features["nifty_ret_15_pct"].value is None
    assert record.features["nifty_ret_1_pct"].valid


# --------------------------------------------------------------------------
# Time of day
# --------------------------------------------------------------------------


def test_time_of_day_minutes_since_open() -> None:
    record = default_engine().compute(SYNTH_SYMBOL, at(TODAY, 0))
    assert record.features["tod_minutes_since_open"].value == pytest.approx(0.0)
    record = default_engine().compute(SYNTH_SYMBOL, at(TODAY, 90, seconds=30))
    assert record.features["tod_minutes_since_open"].value == pytest.approx(90.5)
    assert minutes_since_open(at(TODAY, 90)) == pytest.approx(90.0)


@pytest.mark.parametrize(
    ("minute_index", "bucket"),
    [(0, "open"), (59, "open"), (60, "early"), (149, "early"), (150, "mid"), (374, "close")],
)
def test_time_of_day_buckets_are_predeclared_and_half_open(minute_index, bucket) -> None:
    record = default_engine().compute(SYNTH_SYMBOL, at(TODAY, minute_index))
    assert record.features["tod_bucket"].value == bucket


def test_time_of_day_is_invalid_outside_the_declared_session() -> None:
    after_close = to_utc(from_ist(TODAY, SESSION_CLOSE)) + timedelta(minutes=1)
    record = default_engine().compute(SYNTH_SYMBOL, after_close)
    assert record.features["tod_bucket"].quality == "outside_session"
    assert record.features["tod_bucket"].value is None


# --------------------------------------------------------------------------
# Missing inputs, record contract, units, allowlist
# --------------------------------------------------------------------------


def test_missing_inputs_produce_missingness_not_invented_values() -> None:
    record = engine_with().compute(SYNTH_SYMBOL, at(TODAY, 29))
    assert record.features["spread_bps"].value is None
    assert record.features["atr_pct"].value is None
    assert record.features["dist_vwap_pct"].value is None
    assert record.features["india_vix"].value is None
    assert record.quality_state.startswith("partial:")


def test_record_carries_versions_as_of_and_lineage() -> None:
    record = default_engine().compute(SYNTH_SYMBOL, at(TODAY, 29))
    row = record.to_row()
    assert row["as_of_timestamp_utc"] == at(TODAY, 29)
    assert row["feature_timestamp_utc"] == at(TODAY, 29)
    assert row["feature_config_version"] == "v1"
    assert row["feature_set_version"] == "fs_v1"
    assert row["lineage_quote"].startswith("event_id=q0")
    assert row["lineage_bars"] == "29 completed bars of session 2026-10-15"
    for name in TRAINING_FEATURES:
        assert f"{name}_valid" in row
        assert f"{name}_quality" in row


def test_feature_names_state_their_units() -> None:
    from jev_imrp.features import FEATURE_UNITS

    assert set(FEATURE_UNITS) == set(TRAINING_FEATURES)
    for name in (
        "spread_bps",
        "atr_pct",
        "dist_vwap_pct",
        "nifty_ret_5_pct",
        "india_vix_change_pct",
    ):
        assert name.endswith(("_bps", "_pct"))
    for name in TRAINING_FEATURES:
        assert FEATURE_UNITS[name], f"{name} must declare a unit"


def test_record_quality_state_is_ok_when_every_feature_is_valid() -> None:
    quotes = pd.DataFrame(
        [quote("q0", at(TODAY, 30), last_traded_price=100.0, last_traded_volume=10.0)]
    )
    context = pd.DataFrame(
        [
            context_row("india_vix", at(TODAY, 10), 14.0),
            context_row("india_vix", at(TODAY, 20), 15.0),
            context_row("nifty_index", at(TODAY, 5), 110.0),
            context_row("nifty_index", at(TODAY, 10), 101.0),
            context_row("nifty_index", at(TODAY, 20), 100.0),
        ]
    )
    engine = engine_with(
        quotes=quotes,
        bars_frame=pd.concat(
            [bars(PRIOR_DAY, 375, volume=10.0), bars(TODAY, 30, volume=20.0)], ignore_index=True
        ),
        context=context,
    )
    record = engine.compute(SYNTH_SYMBOL, at(TODAY, 30))
    assert record.quality_state == "ok", record.features


def test_training_row_carries_only_allowlisted_features() -> None:
    record = default_engine().compute(SYNTH_SYMBOL, at(TODAY, 29))
    assert tuple(record.training_row()) == TRAINING_FEATURES
    assert_no_leaks(record.training_row())


def test_a_deliberately_leaked_future_column_is_rejected() -> None:
    with pytest.raises(FeatureLeakError) as excinfo:
        assert_no_leaks([*TRAINING_FEATURES, "future_return_30m"])
    assert "future_return_30m" in str(excinfo.value)


@pytest.mark.parametrize(
    "column",
    ["forward_net_pnl", "label_net_bps", "next_bar_close", "outcome_return", "close_price_lead_1"],
)
def test_leak_patterns_are_rejected_even_if_allowlisted_somehow(column) -> None:
    from jev_imrp.features.causal_checks import leaked_columns

    assert leaked_columns([column]) == [column]


def test_every_record_carries_exactly_the_allowlisted_feature_set() -> None:
    assert_feature_set([default_engine().compute(SYNTH_SYMBOL, at(TODAY, 29))])


def test_frozen_relative_volume_reference_uses_five_prior_sessions() -> None:
    assert REL_VOLUME_PRIOR_SESSIONS == 5
    # Six prior sessions; the oldest is absurdly loud and must be dropped by the
    # frozen five-session cap, otherwise the reference (and the value) explodes.
    days = [PRIOR_DAY - timedelta(days=n) for n in range(6, 0, -1)]
    volumes = [1_000_000.0, 160.0, 80.0, 40.0, 20.0, 10.0]
    frames = [bars(d, 375, volume=v) for d, v in zip(days, volumes, strict=True)]
    frames.append(bars(TODAY, 30, volume=20.0))
    record = engine_with(bars_frame=pd.concat(frames, ignore_index=True)).compute(
        SYNTH_SYMBOL, at(TODAY, 30)
    )
    # Five most recent prior sessions at 30 minutes: 300, 600, 1200, 2400, 4800
    # -> mean 1860; current session cumulative 600 -> 600 / 1860.
    assert record.features["relative_volume"].value == pytest.approx(600.0 / 1860.0)


def test_overnight_gap_does_not_break_session_features() -> None:
    """A decision in a later session sees the previous session's completed bars."""
    later_day = TODAY + timedelta(days=1)
    quotes = pd.DataFrame([quote("q0", at(later_day, 29), last_traded_price=100.0)])
    frame = pd.concat(
        [bars(PRIOR_DAY, 375), bars(TODAY, 375), bars(later_day, 30)], ignore_index=True
    )
    record = engine_with(quotes=quotes, bars_frame=frame).compute(SYNTH_SYMBOL, at(later_day, 29))
    assert record.features["dist_pdh_pct"].valid
    assert TODAY.isoformat() in record.lineage["pdh_pdl"]
    assert math.isclose(record.features["tod_minutes_since_open"].value or -1.0, 29.0)


def test_naive_datetimes_are_rejected() -> None:
    naive = datetime(2026, 10, 15, 4, 45, tzinfo=UTC).replace(tzinfo=None)
    with pytest.raises(ValueError):
        default_engine().compute(SYNTH_SYMBOL, naive)
