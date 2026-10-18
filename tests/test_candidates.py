"""Candidate-stream schema tests and the deterministic SYNTHETIC generator."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from jev_imrp.candidates import (
    CANDIDATE_SCHEMA_VERSION,
    SYNTHETIC_STRATEGY_VERSION,
    CandidateError,
    CandidateRecord,
    EligibilityStatus,
    Side,
    SyntheticGeneratorConfig,
    synthetic_candidates,
    synthetic_quote_rows,
    validate_candidate_stream,
)
from jev_imrp.common.timeutils import SESSION_OPEN, from_ist, to_utc
from jev_imrp.features import TRAINING_FEATURES
from jev_imrp.features.causal_checks import FeatureLeakError

SESSIONS: tuple[date, ...] = tuple(date(2026, 4, 6) + timedelta(days=offset) for offset in range(5))
SYMBOLS = ("AAA", "BBB")
SEED = 20260406


def quote_inputs() -> list[dict[str, object]]:
    return synthetic_quote_rows(SYMBOLS, SESSIONS, seed=SEED)


def stream(**kwargs: object) -> list[CandidateRecord]:
    config = SyntheticGeneratorConfig(seed=SEED, **kwargs)  # type: ignore[arg-type]
    return synthetic_candidates(quote_inputs(), config=config)


def test_generator_is_deterministic_per_seed() -> None:
    first = stream(candidate_probability=1.0)
    second = stream(candidate_probability=1.0)
    assert [c.signal_id for c in first] == [c.signal_id for c in second]
    assert [c.as_dict() for c in first] == [c.as_dict() for c in second]


def test_different_seeds_give_different_streams() -> None:
    a = synthetic_candidates(
        quote_inputs(), config=SyntheticGeneratorConfig(seed=1, candidate_probability=1.0)
    )
    b = synthetic_candidates(
        quote_inputs(), config=SyntheticGeneratorConfig(seed=2, candidate_probability=1.0)
    )
    assert [c.side for c in a] != [c.side for c in b]
    assert {c.signal_id for c in a} == {c.signal_id for c in b}


def test_emitted_rows_are_schema_valid() -> None:
    candidates = stream(candidate_probability=1.0)
    assert candidates
    validate_candidate_stream(candidates)
    for candidate in candidates:
        assert candidate.schema_version == CANDIDATE_SCHEMA_VERSION
        assert candidate.strategy_version == SYNTHETIC_STRATEGY_VERSION
        assert candidate.is_synthetic is True, "synthetic rows must be labelled synthetic"
        assert candidate.side in (Side.BUY, Side.SELL)
        assert candidate.eligibility_status in tuple(EligibilityStatus)
        assert candidate.intended_horizon_minutes == 30
        assert candidate.source_event_id
        assert candidate.feature_snapshot_id.startswith("fs_")
        assert candidate.feature_hash
        assert candidate.session in SESSIONS


def test_emitted_features_stay_inside_the_training_allowlist() -> None:
    for candidate in stream(candidate_probability=1.0):
        assert set(candidate.features) <= set(TRAINING_FEATURES)


def test_row_round_trip_preserves_the_record() -> None:
    candidate = stream(candidate_probability=1.0)[0]
    restored = CandidateRecord.from_row(candidate.to_row())
    assert restored.as_dict() == candidate.as_dict()


def test_ineligible_candidates_carry_a_reason() -> None:
    candidates = stream(candidate_probability=1.0, max_spread_bps_for_eligible=0.0)
    assert all(not candidate.is_eligible for candidate in candidates)
    assert all(
        candidate.eligibility_reason.startswith("synthetic_spread_too_wide")
        for candidate in candidates
    )


def test_duplicate_signal_ids_are_rejected() -> None:
    candidate = stream(candidate_probability=1.0)[0]
    with pytest.raises(CandidateError):
        validate_candidate_stream([candidate, candidate])


def test_missing_required_field_is_rejected() -> None:
    kwargs = {
        "signal_id": "x",
        "strategy_version": SYNTHETIC_STRATEGY_VERSION,
        "symbol": "AAA",
        "side": Side.BUY,
        "decision_timestamp_utc": to_utc(from_ist(SESSIONS[0], SESSION_OPEN)),
        "source_event_id": "e",
        "intended_horizon_minutes": 30,
        "feature_snapshot_id": "fs_x",
        "feature_hash": "h",
        "eligibility_status": EligibilityStatus.ELIGIBLE,
        "eligibility_reason": "ok",
    }
    with pytest.raises(CandidateError):
        CandidateRecord(**{**kwargs, "feature_hash": ""})
    with pytest.raises(CandidateError):
        CandidateRecord(**{**kwargs, "symbol": ""})


def test_candidate_cannot_carry_a_future_looking_feature() -> None:
    kwargs = {
        "signal_id": "x",
        "strategy_version": SYNTHETIC_STRATEGY_VERSION,
        "symbol": "AAA",
        "side": Side.BUY,
        "decision_timestamp_utc": to_utc(from_ist(SESSIONS[0], SESSION_OPEN)),
        "source_event_id": "e",
        "intended_horizon_minutes": 30,
        "feature_snapshot_id": "fs_x",
        "feature_hash": "h",
        "eligibility_status": EligibilityStatus.ELIGIBLE,
        "eligibility_reason": "ok",
    }
    with pytest.raises(FeatureLeakError):
        CandidateRecord(**kwargs, features={"net_bps_future": 1.0})


def test_naive_decision_timestamp_is_rejected() -> None:
    with pytest.raises(ValueError):
        CandidateRecord(
            signal_id="x",
            strategy_version=SYNTHETIC_STRATEGY_VERSION,
            symbol="AAA",
            side=Side.BUY,
            decision_timestamp_utc=from_ist(SESSIONS[0], SESSION_OPEN).replace(tzinfo=None),
            source_event_id="e",
            intended_horizon_minutes=30,
            feature_snapshot_id="fs_x",
            feature_hash="h",
            eligibility_status=EligibilityStatus.ELIGIBLE,
            eligibility_reason="ok",
        )


def test_group_key_keeps_simultaneous_symbol_rows_together() -> None:
    candidate = stream(candidate_probability=1.0)[0]
    assert candidate.group_key == (candidate.symbol, candidate.decision_timestamp_utc)


def test_generator_respects_the_per_symbol_session_cap() -> None:
    candidates = stream(candidate_probability=1.0, max_candidates_per_symbol_session=2)
    counts: dict[tuple[str, date], int] = {}
    for candidate in candidates:
        key = (candidate.symbol, candidate.session)
        counts[key] = counts.get(key, 0) + 1
    assert all(count <= 2 for count in counts.values())


def test_synthetic_input_rows_are_themselves_labelled_synthetic() -> None:
    rows = synthetic_quote_rows(SYMBOLS, SESSIONS, seed=SEED)
    assert rows
    assert all(str(row["event_id"]).startswith("synthetic|") for row in rows)
    assert synthetic_quote_rows(SYMBOLS, SESSIONS, seed=SEED) == rows


def test_input_rows_missing_keys_are_rejected() -> None:
    with pytest.raises(ValueError):
        synthetic_candidates([{"symbol": "AAA"}])
