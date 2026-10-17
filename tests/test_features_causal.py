"""Causality tests required by plan section 7.5.

Each test proves one clause of the contract:

* appending future rows leaves previously computed features unchanged;
* changing a future bar's close/high/low/volume does not change earlier features;
* moving any source availability timestamp past the decision time makes the
  as-of join reject or exclude that value;
* a deliberately leaked future column is rejected by the training allowlist.

All observations are deterministic synthetic fixtures.
"""

from __future__ import annotations

from datetime import timedelta

import pandas as pd
import pytest

from jev_imrp.features import (
    TRAINING_FEATURES,
    FeatureEngine,
    as_of_frame,
    assert_unchanged,
    changed_features,
)
from jev_imrp.features.causal_checks import CausalViolation, FeatureLeakError, assert_no_leaks
from jev_imrp.features.compute import _session_of
from test_features import (
    PRIOR_DAY,
    SYNTH_SYMBOL,
    TODAY,
    at,
    bar,
    bars,
    context_row,
    default_engine,
    engine_with,
    quote,
)

DECISIONS = [at(TODAY, minute) for minute in (10, 20, 30, 40)]


def full_dataset() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Synthetic quotes, bars and context covering two sessions."""
    quotes = pd.DataFrame(
        [
            quote(
                f"q{index}",
                at(TODAY, index),
                last_traded_price=100.0 + index / 10.0,
                last_traded_volume=5.0,
            )
            for index in range(0, 61, 10)
        ]
    )
    bars_frame = pd.concat(
        [bars(PRIOR_DAY, 375, volume=10.0), bars(TODAY, 60, volume=20.0)], ignore_index=True
    )
    context = pd.DataFrame(
        [
            context_row("india_vix", at(TODAY, index), 14.0 + index / 10.0)
            for index in range(0, 61, 10)
        ]
        + [
            context_row("nifty_index", at(TODAY, index), 100.0 + index / 10.0)
            for index in range(0, 61, 5)
        ]
    )
    return quotes, bars_frame, context


def compute_all(engine: FeatureEngine, decisions=DECISIONS):
    return [engine.compute(SYNTH_SYMBOL, as_of) for as_of in decisions]


# --------------------------------------------------------------------------
# Clause 1: appending future rows changes nothing already computed
# --------------------------------------------------------------------------


def test_appending_future_rows_leaves_earlier_features_unchanged() -> None:
    quotes, bars_frame, context = full_dataset()
    baseline = compute_all(engine_with(quotes=quotes, bars_frame=bars_frame, context=context))

    extended = engine_with(
        quotes=pd.concat(
            [quotes, pd.DataFrame([quote("future", at(TODAY, 200))])], ignore_index=True
        ),
        bars_frame=pd.concat(
            [bars_frame, bars(TODAY, 40, start_minute=60, high_price=9_000.0)], ignore_index=True
        ),
        context=pd.concat(
            [context, pd.DataFrame([context_row("india_vix", at(TODAY, 200), 99.0)])],
            ignore_index=True,
        ),
    )
    after = compute_all(extended)

    assert [record.as_of_timestamp_utc for record in after] == [
        r.as_of_timestamp_utc for r in baseline
    ]
    for before, now in zip(baseline, after, strict=True):
        assert_unchanged(before, now, context="future rows appended")


def test_earlier_features_are_identical_when_computing_only_up_to_the_decision_time() -> None:
    quotes, bars_frame, context = full_dataset()
    engine = engine_with(quotes=quotes, bars_frame=bars_frame, context=context)
    early = engine.compute(SYNTH_SYMBOL, at(TODAY, 30))

    engine.observe_quote(pd.DataFrame([quote("later", at(TODAY, 45))]))
    engine.observe_bars(bars(TODAY, 10, start_minute=30))
    engine.observe_context(pd.DataFrame([context_row("india_vix", at(TODAY, 45), 42.0)]))
    assert_unchanged(
        early, engine.compute(SYNTH_SYMBOL, at(TODAY, 30)), context="streamed live rows"
    )


# --------------------------------------------------------------------------
# Clause 2: rewriting a future bar's values changes nothing earlier
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("close_price", 9_999.0),
        ("high_price", 8_888.0),
        ("low_price", 0.5),
        ("volume", 10_000_000.0),
    ],
)
def test_rewriting_a_future_bars_values_does_not_change_earlier_features(field, value) -> None:
    quotes, bars_frame, context = full_dataset()
    baseline = compute_all(engine_with(quotes=quotes, bars_frame=bars_frame, context=context))

    # Bar at minute 45 is only complete at 10:01, well after the 10:30 decision.
    rewritten = bars(TODAY, 1, start_minute=45, **{field: value})
    after = compute_all(
        engine_with(
            quotes=quotes,
            bars_frame=pd.concat([bars_frame, rewritten], ignore_index=True),
            context=context,
        )
    )
    for before, now in zip(baseline, after, strict=True):
        assert_unchanged(before, now, context=f"future bar {field} rewritten")


def test_a_future_bar_never_enters_the_completed_bar_window() -> None:
    quotes, bars_frame, context = full_dataset()
    record = engine_with(quotes=quotes, bars_frame=bars_frame, context=context).compute(
        SYNTH_SYMBOL, at(TODAY, 30)
    )
    assert record.features["atr_pct"].valid
    assert record.features["relative_volume"].valid
    assert "30 completed bars" in record.lineage["bars"]


# --------------------------------------------------------------------------
# Clause 3: availability after the decision time excludes the value
# --------------------------------------------------------------------------


def test_quote_available_after_the_decision_time_is_excluded() -> None:
    quotes, bars_frame, context = full_dataset()
    late = quote("late", at(TODAY, 30), availability_delay_s=10.0)  # available at 10:30:10
    engine = engine_with(
        quotes=pd.concat([quotes, pd.DataFrame([late])], ignore_index=True),
        bars_frame=bars_frame,
        context=context,
    )
    record = engine.compute(SYNTH_SYMBOL, at(TODAY, 30))
    # The newest usable quote is the one received at 10:30, not the late one.
    assert "event_id=q30" in record.lineage["quote"]
    assert record.features["spread_bps"].value == pytest.approx(100.0)


def test_bar_available_after_the_decision_time_is_excluded() -> None:
    quotes, bars_frame, context = full_dataset()
    late_bar = bar(TODAY, 30, availability_delay_s=30.0, volume=1_000_000.0)
    engine = engine_with(
        quotes=quotes,
        bars_frame=pd.concat([bars_frame, pd.DataFrame([late_bar])], ignore_index=True),
        context=context,
    )
    record = engine.compute(SYNTH_SYMBOL, at(TODAY, 30))
    assert "30 completed bars" in record.lineage["bars"]
    assert record.features["relative_volume"].valid


def test_context_available_after_the_decision_time_is_excluded() -> None:
    quotes, bars_frame, context = full_dataset()
    late = context_row("nifty_index", at(TODAY, 30), 50.0, availability_delay_s=5.0)
    engine = engine_with(
        quotes=quotes,
        bars_frame=bars_frame,
        context=pd.concat([context, pd.DataFrame([late])], ignore_index=True),
    )
    record = engine.compute(SYNTH_SYMBOL, at(TODAY, 30))
    # nifty_ret_1_pct must be measured against the newest point already available
    # at 10:29 (the 10:25 print, 102.5), not the late 50.0 print.
    assert record.features["nifty_ret_1_pct"].value == pytest.approx(
        100.0 * (103.0 - 102.5) / 102.5
    )


def test_shifting_every_source_timestamp_past_the_decision_time_excludes_everything() -> None:
    quotes, bars_frame, context = full_dataset()
    shifted_quotes = quotes.assign(
        received_timestamp_utc=quotes["received_timestamp_utc"] + timedelta(hours=2)
    )
    shifted_bars = bars_frame.assign(
        available_timestamp_utc=bars_frame["available_timestamp_utc"] + timedelta(hours=2)
    )
    shifted_context = context.assign(
        available_timestamp_utc=context["available_timestamp_utc"] + timedelta(hours=2)
    )
    record = engine_with(
        quotes=shifted_quotes, bars_frame=shifted_bars, context=shifted_context
    ).compute(SYNTH_SYMBOL, at(TODAY, 30))
    # Time-of-day features come from as_of itself, so they survive; every feature
    # that reads an observation must be excluded.
    derived_from_as_of = {"tod_minutes_since_open", "tod_bucket"}
    for name in TRAINING_FEATURES:
        if name in derived_from_as_of:
            continue
        assert record.features[name].value is None, (
            f"{name} should be excluded, got {record.features[name]}"
        )
    assert record.lineage["quote"] == "no quote available"


def test_as_of_frame_is_the_single_availability_gate() -> None:
    quotes, _, _ = full_dataset()
    view = as_of_frame(quotes, "received_timestamp_utc", at(TODAY, 30))
    assert len(view) == 4
    assert (view["received_timestamp_utc"] <= at(TODAY, 30)).all()
    assert len(as_of_frame(quotes, "received_timestamp_utc", at(TODAY, 20))) == 3


def test_session_view_never_reads_another_sessions_bars() -> None:
    _, bars_frame, _ = full_dataset()
    engine = engine_with(bars_frame=bars_frame)
    today_view = engine._bar_view(SYNTH_SYMBOL, at(TODAY, 30), TODAY)
    assert _session_of(today_view["bar_start_utc"]).unique().tolist() == [TODAY]
    prior = engine._prior_session_bars(SYNTH_SYMBOL, at(TODAY, 30), TODAY)
    assert _session_of(prior["bar_start_utc"]).unique().tolist() == [PRIOR_DAY]


# --------------------------------------------------------------------------
# Clause 4: the causality guard itself fails loudly
# --------------------------------------------------------------------------


def test_assert_unchanged_detects_a_violation() -> None:
    before = default_engine().compute(SYNTH_SYMBOL, at(TODAY, 29))
    polluted = default_engine().compute(SYNTH_SYMBOL, at(TODAY, 30))
    changed = changed_features(before, polluted)
    assert changed  # tod_minutes_since_open genuinely differs between the two times
    with pytest.raises(CausalViolation):
        assert_unchanged(before, polluted, context="synthetic violation")


# --------------------------------------------------------------------------
# Clause 5: leaked future columns are rejected by the allowlist
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "column",
    [
        "future_return_30m",
        "forward_net_bps",
        "label_net_pnl",
        "next_close_price",
        "outcome_net_return",
        "close_price_lead",
        "tod_target_bucket",
    ],
)
def test_leaked_future_columns_are_rejected(column) -> None:
    with pytest.raises(FeatureLeakError):
        assert_no_leaks([*TRAINING_FEATURES, column])


def test_the_real_training_row_is_always_allowlisted() -> None:
    for as_of in DECISIONS:
        record = engine_with(**_dataset_kwargs()).compute(SYNTH_SYMBOL, as_of)
        assert_no_leaks(record.training_row(), context=f"training row @ {as_of.isoformat()}")


def _dataset_kwargs():
    quotes, bars_frame, context = full_dataset()
    return {"quotes": quotes, "bars_frame": bars_frame, "context": context}
