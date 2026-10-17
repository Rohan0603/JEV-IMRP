"""Feature parity between replay and live/shadow computation (plan 7.1 / 7.4).

Both paths must call the same library. These tests feed identical ordered
observations through:

* the replay path -- bulk ingest, then compute at every decision time;
* the live/shadow path -- ingest rows one at a time, computing at each decision
  time from only what had arrived.

and require byte-identical feature rows. If a second implementation ever creeps
in, or if the live path forgets an availability gate, this fails.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from jev_imrp.features import (
    TRAINING_FEATURES,
    FeatureEngine,
    FeatureParams,
    compute_feature_series,
)
from test_features import (
    PRIOR_DAY,
    SYNTH_SYMBOL,
    TODAY,
    at,
    bar,
    bars,
    context_row,
    quote,
)
from test_features_causal import full_dataset

PARAMS = FeatureParams()


def decisions() -> list[datetime]:
    return [at(TODAY, minute) for minute in (5, 10, 15, 20, 30, 45)]


def replay_records(engine: FeatureEngine):
    return compute_feature_series(engine, SYNTH_SYMBOL, decisions())


def live_records(quotes: pd.DataFrame, bars_frame: pd.DataFrame, context: pd.DataFrame):
    """Shadow path: rows arrive in availability order and are computed on the spot."""
    engine = FeatureEngine(params=PARAMS)
    pending: list[tuple[datetime, str, int, pd.DataFrame]] = []
    for order, frame in enumerate((quotes, bars_frame, context)):
        for index in range(len(frame)):
            row = frame.iloc[[index]]
            pending.append((_available(row), str(order), index, row))
    pending.sort(key=lambda item: (item[0], item[1], item[2]))

    records = []
    for as_of in decisions():
        while pending and pending[0][0] <= as_of:
            _, order, _, row = pending.pop(0)
            if order == "0":
                engine.observe_quote(row)
            elif order == "1":
                engine.observe_bars(row)
            else:
                engine.observe_context(row)
        records.append(engine.compute(SYNTH_SYMBOL, as_of))
    return records


def _available(row: pd.DataFrame) -> datetime:
    for column in ("received_timestamp_utc", "available_timestamp_utc"):
        if column in row.columns:
            return row.iloc[0][column].to_pydatetime()
    raise AssertionError("row has no availability timestamp")


def test_replay_and_live_computation_are_identical() -> None:
    quotes, bars_frame, context = full_dataset()
    replay = replay_records(FeatureEngine(quotes, bars_frame, context, params=PARAMS))
    live = live_records(quotes, bars_frame, context)
    assert len(replay) == len(live)
    for before, after in zip(replay, live, strict=True):
        assert after.to_row() == before.to_row()


def test_replay_and_live_agree_on_the_training_row_for_every_model() -> None:
    quotes, bars_frame, context = full_dataset()
    replay = replay_records(FeatureEngine(quotes, bars_frame, context, params=PARAMS))
    live = live_records(quotes, bars_frame, context)
    for before, after in zip(replay, live, strict=True):
        assert after.training_row() == before.training_row()
        assert tuple(before.training_row()) == TRAINING_FEATURES


def test_replay_and_live_agree_when_observations_arrive_out_of_order() -> None:
    """Availability order, not file order, decides what a decision time can see."""
    quotes, bars_frame, context = full_dataset()
    shuffled = quotes.sample(frac=1.0, random_state=7).reset_index(drop=True)
    ordered = replay_records(FeatureEngine(quotes, bars_frame, context, params=PARAMS))
    shuffled_records = live_records(shuffled, bars_frame, context)
    for before, after in zip(ordered, shuffled_records, strict=True):
        assert after.to_row() == before.to_row()


def test_one_decision_time_computed_twice_is_stable() -> None:
    quotes, bars_frame, context = full_dataset()
    engine = FeatureEngine(quotes, bars_frame, context, params=PARAMS)
    first = engine.compute(SYNTH_SYMBOL, at(TODAY, 30))
    second = engine.compute(SYNTH_SYMBOL, at(TODAY, 30))
    assert first.to_row() == second.to_row()


def test_replay_and_live_agree_on_a_gap_and_incomplete_bar_day() -> None:
    quotes = pd.DataFrame(
        [
            quote("q1", at(TODAY, 10), last_traded_price=100.0, last_traded_volume=10.0),
            quote("q2", at(TODAY, 25), last_traded_price=105.0, last_traded_volume=10.0),
        ]
    )
    bars_frame = pd.concat(
        [
            bars(PRIOR_DAY, 375, volume=10.0),
            bars(PRIOR_DAY, 1, start_minute=375, has_gap=True),
            bars(TODAY, 30, volume=20.0),
            pd.DataFrame([bar(TODAY, 30, is_complete=False)]),
        ],
        ignore_index=True,
    )
    context = pd.DataFrame(
        [
            context_row("india_vix", at(TODAY, 5), 14.0),
            context_row("india_vix", at(TODAY, 26), 18.0),
            context_row("nifty_index", at(TODAY, 5), 100.0),
            context_row("nifty_index", at(TODAY, 26), 102.0),
        ]
    )
    engine = FeatureEngine(quotes, bars_frame, context, params=PARAMS)
    replay = [engine.compute(SYNTH_SYMBOL, moment) for moment in (at(TODAY, 30), at(TODAY, 45))]
    live = live_records(quotes, bars_frame, context)
    live = [
        record for record in live if record.as_of_timestamp_utc in {at(TODAY, 30), at(TODAY, 45)}
    ]
    for before, after in zip(replay, live, strict=True):
        assert after.to_row() == before.to_row()
    assert replay[0].features["dist_pdh_pct"].quality == "gap_in_prior_session"
    assert replay[0].features["dist_vwap_pct"].valid


def test_live_path_consumes_every_row_available_by_the_last_decision_time() -> None:
    """After the final decision time, the live loop has ingested all rows it could."""
    quotes, _, _ = full_dataset()
    final = decisions()[-1]
    engine = FeatureEngine(params=PARAMS)
    available = quotes[quotes["received_timestamp_utc"] <= final]
    for index in range(len(available)):
        engine.observe_quote(available.iloc[[index]])
    assert len(engine.quotes) == len(available)


@pytest.mark.parametrize("moment_index", range(6))
def test_every_decision_time_produces_a_full_record(moment_index) -> None:
    quotes, bars_frame, context = full_dataset()
    engine = FeatureEngine(quotes, bars_frame, context, params=PARAMS)
    record = engine.compute(SYNTH_SYMBOL, decisions()[moment_index])
    assert record.as_of_timestamp_utc == decisions()[moment_index]
    assert set(record.features) == set(TRAINING_FEATURES)
