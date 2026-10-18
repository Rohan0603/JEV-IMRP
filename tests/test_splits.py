"""Temporal split tests: chronology, session grouping, manifest, walk-forward."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from jev_imrp.common.timeutils import (
    SESSION_CLOSE,
    SESSION_OPEN,
    ensure_aware,
    from_ist,
    ist_session_date,
    to_utc,
)
from jev_imrp.validation.temporal_splits import (
    Partition,
    SessionSplitter,
    SplitError,
    SplitLeakError,
    assert_fit_partition,
    build_split_manifest,
    make_session_partitions,
    walk_forward_folds,
)

#: Ten consecutive weekday-ish sessions; contents are synthetic fixtures.
SESSIONS: tuple[date, ...] = tuple(
    date(2026, 1, 5) + timedelta(days=offset) for offset in range(10)
)


def row(symbol: str, session: date, minute: int = 30) -> dict[str, object]:
    return {
        "symbol": symbol,
        "decision_timestamp_utc": from_ist(session, SESSION_OPEN) + timedelta(minutes=minute),
    }


def dataset(symbols: tuple[str, ...] = ("AAA", "BBB")) -> list[dict[str, object]]:
    """Rows for every session and symbol, several timestamps per session."""
    rows: list[dict[str, object]] = []
    for session in SESSIONS:
        for symbol in symbols:
            for minute in (10, 30, 50):
                rows.append(row(symbol, session, minute))
    return rows


def partitions() -> dict[Partition, tuple[date, ...]]:
    return make_session_partitions(
        SESSIONS, train_fraction=0.6, validation_fraction=0.2, test_fraction=0.2
    )


def test_sessions_are_assigned_exactly_once() -> None:
    assigned = [session for sessions in partitions().values() for session in sessions]
    assert sorted(assigned) == sorted(SESSIONS)
    assert len(assigned) == len(set(assigned))


def test_train_sessions_precede_validation_precede_test() -> None:
    parts = partitions()
    assert max(parts[Partition.TRAIN]) < min(parts[Partition.VALIDATION])
    assert max(parts[Partition.VALIDATION]) < min(parts[Partition.TEST])


def test_no_training_session_at_or_after_any_test_session() -> None:
    parts = partitions()
    train = parts[Partition.TRAIN]
    test = parts[Partition.TEST]
    for train_session in train:
        for test_session in test:
            assert train_session < test_session


def session_of(record: dict[str, object]) -> date:
    return ist_session_date(ensure_aware(record["decision_timestamp_utc"]))  # type: ignore[arg-type]


def test_all_symbols_and_timestamps_of_a_session_land_on_one_side() -> None:
    splitter = SessionSplitter(build_split_manifest(partitions(), dataset()))
    by_session: dict[date, set[Partition | None]] = {session: set() for session in SESSIONS}
    for record in dataset():
        by_session[session_of(record)].add(
            splitter.partition_for_timestamp(record["decision_timestamp_utc"])
        )  # type: ignore[arg-type]
    assert all(len(found) == 1 and None not in found for found in by_session.values())


def test_same_timestamp_rows_of_one_event_never_straddle_a_boundary() -> None:
    manifest = build_split_manifest(partitions(), dataset())
    splitter = SessionSplitter(manifest)
    previous = manifest.sessions(Partition.TRAIN)[-1]
    boundary_session = manifest.sessions(Partition.VALIDATION)[0]
    rows: list[dict[str, object]] = []
    for session in (previous, boundary_session):
        # Three symbols emitting on the SAME instant: one market event.
        instant = from_ist(session, SESSION_OPEN) + timedelta(minutes=45)
        for symbol in ("AAA", "BBB", "CCC"):
            rows.append({"symbol": symbol, "decision_timestamp_utc": instant})

    sides = {
        record["decision_timestamp_utc"]: splitter.partition_for_timestamp(
            record["decision_timestamp_utc"]
        )  # type: ignore[index]
        for record in rows
    }
    assert sides[from_ist(previous, SESSION_OPEN) + timedelta(minutes=45)] is Partition.TRAIN
    assert (
        sides[from_ist(boundary_session, SESSION_OPEN) + timedelta(minutes=45)]
        is Partition.VALIDATION
    )
    assert len(set(sides.values())) == 2


def test_manifest_records_boundaries_counts_and_exclusions() -> None:
    parts = partitions()
    rows = dataset()
    outside = {
        "symbol": "AAA",
        "decision_timestamp_utc": from_ist(date(2024, 1, 2), SESSION_OPEN),
    }
    manifest = build_split_manifest(parts, [*rows, outside])

    total = sum(manifest.observation_counts.values())
    assert total == len(rows)
    excluded_rows = sum(entry.n_rows for entry in manifest.excluded)
    assert excluded_rows == 1
    assert manifest.excluded[0].reason == "session_not_assigned_to_any_partition"
    assert manifest.excluded[0].session == date(2024, 1, 2)

    per_symbol = manifest.per_symbol_counts[Partition.TRAIN]
    assert set(per_symbol) == {"AAA", "BBB"}
    assert sum(per_symbol.values()) == manifest.observation_counts[Partition.TRAIN]

    payload = manifest.as_dict()
    assert payload["boundaries"]["test"]["last_session"] == parts[Partition.TEST][-1].isoformat()
    assert payload["observation_counts"]["test"] > 0


def test_manifest_boundaries_are_exact_session_bounds() -> None:
    manifest = build_split_manifest(partitions(), dataset())
    start, end = manifest.interval(Partition.VALIDATION)
    first = manifest.sessions(Partition.VALIDATION)[0]
    last = manifest.sessions(Partition.VALIDATION)[-1]
    assert start == to_utc(from_ist(first, SESSION_OPEN))
    assert end == to_utc(from_ist(last, SESSION_CLOSE))


def test_rows_for_returns_only_that_partition() -> None:
    splitter = SessionSplitter(build_split_manifest(partitions(), dataset()))
    rows = dataset()
    train_rows = splitter.rows_for(rows, Partition.TRAIN)
    assert train_rows
    for record in train_rows:
        assert splitter.partition_for_timestamp(record["decision_timestamp_utc"]) is Partition.TRAIN  # type: ignore[arg-type]


def test_unassigned_session_is_excluded_not_silently_dropped() -> None:
    manifest = build_split_manifest(partitions(), dataset())
    splitter = SessionSplitter(manifest)
    stray = {
        "symbol": "AAA",
        "decision_timestamp_utc": from_ist(date(2020, 5, 5), SESSION_OPEN),
    }
    assert splitter.partition_for_timestamp(stray["decision_timestamp_utc"]) is None  # type: ignore[arg-type]
    assert stray not in splitter.rows_for([stray], Partition.TRAIN)


def test_duplicate_session_assignment_is_rejected() -> None:
    parts = partitions()
    parts[Partition.TEST] = (parts[Partition.TRAIN][0], *parts[Partition.TEST])
    with pytest.raises(SplitLeakError):
        build_split_manifest(parts, dataset())


def test_inverted_boundaries_are_rejected() -> None:
    parts = partitions()
    parts[Partition.TRAIN], parts[Partition.VALIDATION] = (
        parts[Partition.VALIDATION],
        parts[Partition.TRAIN],
    )
    with pytest.raises(SplitLeakError):
        build_split_manifest(parts, dataset())


def test_too_few_sessions_is_rejected() -> None:
    with pytest.raises(SplitError):
        make_session_partitions(
            SESSIONS[:2], train_fraction=0.5, validation_fraction=0.25, test_fraction=0.25
        )


# -- walk-forward ---------------------------------------------------------


def folds():
    return walk_forward_folds(SESSIONS, n_folds=3, validation_sessions=2, min_train_sessions=2)


def test_walk_forward_windows_expand_and_stay_ordered() -> None:
    previous: tuple[date, ...] = ()
    for fold in folds():
        assert fold.train_sessions[: len(previous)] == previous
        assert len(fold.train_sessions) > len(previous)
        assert max(fold.train_sessions) < min(fold.validation_sessions)
        previous = fold.train_sessions


def test_fold_fit_view_refuses_validation_rows() -> None:
    fold = folds()[0]
    rows = dataset()
    validation_rows = fold.validation_rows(rows)
    assert validation_rows
    with pytest.raises(SplitLeakError):
        fold.fit_view(validation_rows)


def test_fold_fit_view_returns_training_rows_only() -> None:
    fold = folds()[-1]
    allowed = set(fold.train_sessions)
    train_input = [record for record in dataset() if session_of(record) in allowed]
    train = fold.fit_view(train_input)
    assert train
    assert {session_of(record) for record in train} <= allowed


def test_assert_fit_partition_blocks_holdout_rows() -> None:
    with pytest.raises(SplitLeakError):
        assert_fit_partition(dataset(), [SESSIONS[0]], context="unit test fit")
    assert_fit_partition([row("AAA", SESSIONS[0])], [SESSIONS[0]])
