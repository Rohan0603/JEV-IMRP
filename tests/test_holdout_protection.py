"""Locked-holdout protection tests (plan 10.2).

Proves the three properties Sprint 4 must deliver:

1. a fit/tuning path handed holdout observations RAISES;
2. the automated protection check passes on a legitimate pipeline and fails on a
   leaky one;
3. opening the holdout is logged (who/when/why) and a second open is flagged as a
   protocol violation rather than silently permitted.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from jev_imrp.common.timeutils import (
    SESSION_OPEN,
    ensure_aware,
    from_ist,
    ist_session_date,
    to_utc,
)
from jev_imrp.validation.holdout import (
    HoldoutAccessError,
    HoldoutError,
    HoldoutGuard,
    HoldoutProtocolViolation,
    assert_holdout_not_fitted,
)
from jev_imrp.validation.temporal_splits import (
    Partition,
    SplitLeakError,
    build_split_manifest,
    make_session_partitions,
    walk_forward_folds,
)

SESSIONS: tuple[date, ...] = tuple(
    date(2026, 3, 2) + timedelta(days=offset) for offset in range(10)
)
HOLDOUT_SESSIONS: tuple[date, ...] = SESSIONS[-2:]
DEVELOPMENT_SESSIONS: tuple[date, ...] = SESSIONS[:-2]


def row(session: date, symbol: str = "AAA") -> dict[str, object]:
    return {
        "symbol": symbol,
        "decision_timestamp_utc": to_utc(from_ist(session, SESSION_OPEN) + timedelta(minutes=30)),
    }


def development_rows() -> list[dict[str, object]]:
    return [row(session, symbol) for session in DEVELOPMENT_SESSIONS for symbol in ("AAA", "BBB")]


def holdout_rows() -> list[dict[str, object]]:
    return [row(session) for session in HOLDOUT_SESSIONS]


def all_rows() -> list[dict[str, object]]:
    return [*development_rows(), *holdout_rows()]


def guard(tmp_path: Path, **kwargs: object) -> HoldoutGuard:
    return HoldoutGuard(HOLDOUT_SESSIONS, log_path=tmp_path / "holdout_access_log.jsonl", **kwargs)  # type: ignore[arg-type]


# -- fit-time protection ----------------------------------------------------


def test_fitting_on_holdout_rows_raises(tmp_path: Path) -> None:
    protection = guard(tmp_path)
    with pytest.raises(HoldoutAccessError):
        protection.training_view(all_rows())


def test_tuning_and_threshold_selection_on_holdout_raises(tmp_path: Path) -> None:
    protection = guard(tmp_path)
    with pytest.raises(HoldoutAccessError):
        protection.tuning_view(all_rows())


def test_standalone_protection_check_fails_on_leaky_pipeline(tmp_path: Path) -> None:
    protection = guard(tmp_path)
    assert_holdout_not_fitted(protection, development_rows())
    with pytest.raises(HoldoutAccessError):
        assert_holdout_not_fitted(protection, all_rows(), context="unit test fit")


def test_automated_protection_check_passes_on_a_clean_pipeline(tmp_path: Path) -> None:
    """The full path: fit inside each fold, never on the holdout, no opening."""
    protection = guard(tmp_path)
    parts = make_session_partitions(
        DEVELOPMENT_SESSIONS, train_fraction=0.6, validation_fraction=0.2, test_fraction=0.2
    )
    manifest = build_split_manifest(parts, development_rows())
    assert manifest.observation_counts[Partition.TRAIN] > 0

    fitted_on: list[date] = []
    for fold in walk_forward_folds(
        DEVELOPMENT_SESSIONS, n_folds=2, validation_sessions=2, min_train_sessions=3
    ):
        rows_in_window = [
            record
            for record in development_rows()
            if ist_session_date(ensure_aware(record["decision_timestamp_utc"]))  # type: ignore[arg-type]
            in fold.all_sessions
        ]
        # Layer 1: the holdout guard refuses protected rows.
        with pytest.raises(HoldoutAccessError):
            protection.training_view([*rows_in_window, *holdout_rows()])
        # Layer 2: the fold refuses its own validation window.
        with pytest.raises(SplitLeakError):
            fold.fit_view(protection.training_view(rows_in_window))

        training = fold.fit_view(
            protection.training_view(rows_in_window[: len(rows_in_window) // 2])
        )
        assert training
        for record in training:
            assert protection.holdout_rows([record]) == ()
            fitted_on.append(ist_session_date(ensure_aware(record["decision_timestamp_utc"])))  # type: ignore[arg-type]
        assert fold.fit_view(training) == training

    assert not set(fitted_on) & set(HOLDOUT_SESSIONS)
    assert not protection.was_opened()
    assert protection.describe()["n_opens"] == 0


def test_holdout_membership_is_by_session_not_by_row_count(tmp_path: Path) -> None:
    protection = guard(tmp_path)
    assert protection.is_holdout_row(holdout_rows()[0])
    assert not protection.is_holdout_row(development_rows()[0])
    assert len(protection.holdout_rows(all_rows())) == len(HOLDOUT_SESSIONS)
    assert protection.describe()["n_holdout_sessions"] == len(HOLDOUT_SESSIONS)


def test_a_closed_holdout_cannot_be_read(tmp_path: Path) -> None:
    protection = guard(tmp_path)
    with pytest.raises(HoldoutAccessError):
        protection.holdout_view(holdout_rows())


# -- opening, logging, protocol violations ----------------------------------


def test_open_is_logged_with_who_when_and_why(tmp_path: Path) -> None:
    protection = guard(tmp_path)
    with protection.open_holdout(
        run_id="confirm-001", who="jev-imrp-8", why="frozen confirmatory run"
    ):
        assert protection.holdout_view(holdout_rows())

    openings = protection.openings()
    assert [entry.kind for entry in openings] == ["open"]
    entry = openings[0]
    assert entry.run_id == "confirm-001"
    assert entry.who == "jev-imrp-8"
    assert entry.why == "frozen confirmatory run"
    assert entry.opened_at_utc.endswith("+00:00")
    assert entry.n_sessions == len(HOLDOUT_SESSIONS)
    assert protection.was_opened()


def test_lease_does_not_persist_after_the_block(tmp_path: Path) -> None:
    protection = guard(tmp_path)
    with protection.open_holdout(run_id="confirm-001", who="tester", why="one frozen run"):
        pass
    with pytest.raises(HoldoutAccessError):
        protection.holdout_view(holdout_rows())


def test_second_open_is_a_visible_protocol_violation(tmp_path: Path) -> None:
    protection = guard(tmp_path)
    with protection.open_holdout(run_id="confirm-001", who="tester", why="first frozen run"):
        pass
    with pytest.raises(HoldoutProtocolViolation):
        protection.open_holdout(run_id="confirm-002", who="tester", why="second look")

    kinds = [entry.kind for entry in protection.openings()]
    assert kinds == ["open", "violation"]
    violations = protection.protocol_violations()
    assert {entry.run_id for entry in violations} == {"confirm-002"}
    assert protection.describe()["n_violations"] == 1


def test_violation_is_logged_even_when_the_second_open_is_refused(tmp_path: Path) -> None:
    log_path = tmp_path / "access.jsonl"
    protection = HoldoutGuard(HOLDOUT_SESSIONS, log_path=log_path)
    with protection.open_holdout(run_id="r1", who="a", why="w"):
        pass
    with pytest.raises(HoldoutProtocolViolation):
        protection.open_holdout(run_id="r2", who="b", why="w2")
    lines = [line for line in log_path.read_text(encoding="utf-8").splitlines() if line]
    assert len(lines) == 2


def test_open_requires_run_id_who_and_why(tmp_path: Path) -> None:
    protection = guard(tmp_path)
    with pytest.raises(HoldoutError):
        protection.open_holdout(run_id="", who="tester", why="reason")


def test_an_open_lease_cannot_be_used_to_read_non_holdout_rows(tmp_path: Path) -> None:
    protection = guard(tmp_path)
    with (
        protection.open_holdout(run_id="confirm-001", who="tester", why="frozen run"),
        pytest.raises(HoldoutAccessError),
    ):
        protection.holdout_view(development_rows())


def test_holdout_cannot_receive_a_development_row_in_a_fit_view(tmp_path: Path) -> None:
    protection = guard(tmp_path)
    # Mixing the two sides into one fit view fails loudly.
    with pytest.raises(HoldoutAccessError):
        protection.training_view([*development_rows(), *holdout_rows()])


def test_split_manifest_never_places_holdout_sessions_in_train() -> None:
    parts = make_session_partitions(
        DEVELOPMENT_SESSIONS, train_fraction=0.6, validation_fraction=0.2, test_fraction=0.2
    )
    manifest = build_split_manifest(parts, development_rows())
    assert not set(manifest.sessions(Partition.TRAIN)) & set(HOLDOUT_SESSIONS)
    assert not set(manifest.sessions(Partition.VALIDATION)) & set(HOLDOUT_SESSIONS)


def test_guard_requires_a_non_empty_holdout(tmp_path: Path) -> None:
    with pytest.raises(HoldoutError):
        HoldoutGuard((), log_path=tmp_path / "log.jsonl")
