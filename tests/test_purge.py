"""Purge and embargo tests, including an adversarial overlapping-label fixture.

The fixture is deliberately hostile: MULTIPLE symbols sharing the same
timestamps, 30-minute label intervals that OVERLAP the evaluation boundary, one
label interval that exactly TOUCHES the boundary, one that reaches far past it,
a grouped pair of simultaneous candidates for one symbol where only one member
violates, and rows outside the label interval that must survive.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from jev_imrp.common.timeutils import SESSION_CLOSE, SESSION_OPEN, from_ist, to_utc
from jev_imrp.validation.purge import (
    PurgeConfig,
    PurgeError,
    PurgeReason,
    embargo_window_start,
    purge_rows,
)
from jev_imrp.validation.temporal_splits import walk_forward_folds

#: Six consecutive sessions (synthetic fixture; contents are never market data).
SESSIONS: tuple[date, ...] = tuple(date(2026, 2, 2) + timedelta(days=offset) for offset in range(6))
BOUNDARY_INDEX = 3  # first evaluation session
EVAL_SESSION = SESSIONS[BOUNDARY_INDEX]
LAST_TRAIN_SESSION = SESSIONS[BOUNDARY_INDEX - 1]
SYMBOLS = ("AAA", "BBB", "CCC")
HORIZON = 30

#: Session-open boundary and a mid-session boundary. Both are legitimate fold
#: edges, and both must be honoured by the interval rule.
BOUNDARY_START = to_utc(from_ist(EVAL_SESSION, SESSION_OPEN))
INTRA_BOUNDARY = to_utc(from_ist(EVAL_SESSION, SESSION_OPEN) + timedelta(minutes=60))


def eval_minute(minute: int) -> object:
    return to_utc(from_ist(EVAL_SESSION, SESSION_OPEN) + timedelta(minutes=minute))


def train_minute(minute: int) -> object:
    return to_utc(from_ist(LAST_TRAIN_SESSION, SESSION_OPEN) + timedelta(minutes=minute))


def labelled_row(
    symbol: str,
    decision: object,
    *,
    span_minutes: int = HORIZON,
    ends_at_boundary: object | None = None,
) -> dict[str, object]:
    """A labelled row whose outcome interval is ``span_minutes`` long."""
    start = decision  # type: ignore[assignment]
    end = (
        ends_at_boundary
        if ends_at_boundary is not None
        else start + timedelta(minutes=span_minutes)
    )
    return {
        "symbol": symbol,
        "decision_timestamp_utc": decision,
        "label_interval_start": start,
        "label_interval_end": end,
        "label_available_at": end,
        "net_bps": 5.0,
    }


def adversarial_rows() -> list[dict[str, object]]:
    """Overlapping 30m labels, multi-symbol shared timestamps, a grouped pair."""
    rows: list[dict[str, object]] = []
    # (1) Comfortably clear of the boundary: three symbols, same timestamps.
    for minute in (5, 15):
        for symbol in SYMBOLS:
            rows.append(labelled_row(symbol, eval_minute(minute)))
    # (2) Overlapping 30m labels: decided 20 minutes before the mid-session
    #     boundary, so the outcome is realised 10 minutes INSIDE the evaluation
    #     period. One timestamp, three symbols.
    for symbol in SYMBOLS:
        rows.append(labelled_row(symbol, eval_minute(40)))
    # (3) A label interval that exactly TOUCHES the boundary instant.
    for symbol in SYMBOLS:
        rows.append(labelled_row(symbol, eval_minute(30), ends_at_boundary=INTRA_BOUNDARY))
    # (4) A grouped pair for one symbol and one timestamp where only the first
    #     member reaches into the boundary. The group must go together.
    rows.append(labelled_row("AAA", eval_minute(20), span_minutes=45))
    rows.append(labelled_row("AAA", eval_minute(20), span_minutes=5))
    # (5) A row whose interval ends 15 minutes before the boundary: survives.
    rows.append(labelled_row("BBB", eval_minute(15), span_minutes=30))
    # (6) An unlabelled row (no interval): carries no realised outcome.
    unlabelled = labelled_row("CCC", eval_minute(50))
    unlabelled["label_interval_end"] = None
    unlabelled["label_available_at"] = None
    rows.append(unlabelled)
    return rows


# -- overlap ----------------------------------------------------------------


def test_overlapping_label_intervals_are_purged() -> None:
    result = purge_rows(adversarial_rows(), boundary_start=INTRA_BOUNDARY)
    purged = {(p.symbol, p.decision_timestamp_utc): p.reason for p in result.purged}
    for symbol in SYMBOLS:
        assert purged[(symbol, eval_minute(40))] is PurgeReason.LABEL_INTERVAL_OVERLAPS_BOUNDARY


def test_multi_symbol_same_timestamp_rows_are_all_purged() -> None:
    result = purge_rows(adversarial_rows(), boundary_start=INTRA_BOUNDARY)
    at_timestamp = {p.symbol for p in result.purged if p.decision_timestamp_utc == eval_minute(40)}
    assert at_timestamp == set(SYMBOLS)


def test_touching_intervals_follow_the_declared_rule() -> None:
    strict = purge_rows(adversarial_rows(), boundary_start=INTRA_BOUNDARY)
    purged_ids = {(p.symbol, p.decision_timestamp_utc) for p in strict.purged}
    assert ("AAA", eval_minute(30)) in purged_ids

    lenient = purge_rows(
        adversarial_rows(),
        boundary_start=INTRA_BOUNDARY,
        config=PurgeConfig(touch_is_purge=False),
    )
    assert ("AAA", eval_minute(30)) in {
        (r["symbol"], r["decision_timestamp_utc"]) for r in lenient.kept
    }
    assert ("AAA", eval_minute(30)) in {
        (r["symbol"], r["decision_timestamp_utc"]) for r in lenient.boundary_touching
    }
    assert all(p.reason is not PurgeReason.LABEL_INTERVAL_REACHES_BOUNDARY for p in lenient.purged)


def test_grouped_simultaneous_candidates_stay_together() -> None:
    result = purge_rows(adversarial_rows(), boundary_start=INTRA_BOUNDARY)
    group = [p for p in result.purged if p.group_key == ("AAA", eval_minute(20))]
    assert len(group) == 2, "both simultaneous candidates must be purged together"
    assert {p.reason for p in group} == {PurgeReason.LABEL_INTERVAL_OVERLAPS_BOUNDARY}


def test_safe_and_unlabelled_rows_survive() -> None:
    result = purge_rows(adversarial_rows(), boundary_start=INTRA_BOUNDARY)
    kept = {(r["symbol"], r["decision_timestamp_utc"]) for r in result.kept}
    assert ("BBB", eval_minute(15)) in kept
    assert ("CCC", eval_minute(50)) in kept
    for minute in (5, 15):
        for symbol in SYMBOLS:
            assert (symbol, eval_minute(minute)) in kept


def test_purge_never_uses_a_fixed_row_count() -> None:
    """The purged volume follows the label INTERVALS, not a row budget.

    Doubling every interval start-to-end distance in the fixture pushes more rows
    across the boundary; nothing in the rule references a number of rows, and
    widening the horizon in the config does not by itself purge anything.
    """

    def build(span: int) -> list[dict[str, object]]:
        return [
            labelled_row("AAA", eval_minute(minute), span_minutes=span)
            for minute in (5, 20, 35, 55)
        ]

    short = purge_rows(build(5), boundary_start=INTRA_BOUNDARY)
    long = purge_rows(build(120), boundary_start=INTRA_BOUNDARY)
    assert short.n_purged == 1  # only the row decided at minute 55 reaches minute 60
    assert long.n_purged == 4
    assert short.summary()["n_input"] == long.summary()["n_input"]


def test_purge_reasons_are_recorded_per_row() -> None:
    result = purge_rows(adversarial_rows(), boundary_start=INTRA_BOUNDARY)
    assert sum(result.by_reason.values()) == result.n_purged
    for entry in result.purged:
        payload = entry.as_dict()
        assert payload["reason"]
        assert payload["session"] and payload["group_timestamp_utc"]


def test_session_open_boundary_purges_an_overnight_interval() -> None:
    """A label realised exactly at the next session open touches the boundary."""
    rows = [labelled_row("AAA", train_minute(300), ends_at_boundary=BOUNDARY_START)]
    strict = purge_rows(rows, boundary_start=BOUNDARY_START)
    assert [p.reason for p in strict.purged] == [PurgeReason.LABEL_INTERVAL_REACHES_BOUNDARY]

    lenient = purge_rows(
        rows, boundary_start=BOUNDARY_START, config=PurgeConfig(touch_is_purge=False)
    )
    assert lenient.n_kept == 1
    assert lenient.n_purged == 0


# -- embargo ----------------------------------------------------------------


def test_embargo_in_sessions_drops_whole_real_sessions() -> None:
    rows = [labelled_row(symbol, train_minute(30), span_minutes=5) for symbol in SYMBOLS]
    plain = purge_rows(rows, boundary_start=INTRA_BOUNDARY, sessions=SESSIONS)
    embargoed = purge_rows(
        rows,
        boundary_start=INTRA_BOUNDARY,
        sessions=SESSIONS,
        config=PurgeConfig(embargo_sessions=1),
    )
    assert plain.n_purged == 0
    assert embargoed.n_purged == len(SYMBOLS)
    assert {p.reason for p in embargoed.purged} == {PurgeReason.EMBARGO}
    assert {p.session for p in embargoed.purged} == {LAST_TRAIN_SESSION}


def test_embargo_in_minutes_is_wall_clock() -> None:
    rows = [labelled_row("AAA", eval_minute(minute), span_minutes=1) for minute in (10, 55, 58, 61)]
    embargoed = purge_rows(
        rows,
        boundary_start=INTRA_BOUNDARY,
        sessions=SESSIONS,
        config=PurgeConfig(embargo_minutes=60),
    )
    embargoed_ids = {
        p.decision_timestamp_utc for p in embargoed.purged if p.reason is PurgeReason.EMBARGO
    }
    # Only the two decisions inside the 60 minutes before the boundary open.
    assert embargoed_ids == {eval_minute(10), eval_minute(55), eval_minute(58)}


def test_embargo_window_start_is_real_time() -> None:
    sessions = embargo_window_start(BOUNDARY_START, embargo_sessions=2, sessions=SESSIONS)
    assert sessions == to_utc(from_ist(SESSIONS[BOUNDARY_INDEX - 2], SESSION_OPEN))
    minutes = embargo_window_start(BOUNDARY_START, embargo_minutes=45, sessions=SESSIONS)
    assert minutes == BOUNDARY_START - timedelta(minutes=45)
    assert embargo_window_start(BOUNDARY_START) is None


def test_embargo_larger_than_history_is_rejected() -> None:
    with pytest.raises(PurgeError):
        embargo_window_start(BOUNDARY_START, embargo_sessions=99, sessions=SESSIONS)


def test_embargo_does_not_purge_rows_before_its_window() -> None:
    rows = [
        labelled_row(
            "AAA",
            to_utc(from_ist(SESSIONS[0], SESSION_OPEN) + timedelta(minutes=30)),
            span_minutes=1,
        )
    ]
    result = purge_rows(
        rows,
        boundary_start=INTRA_BOUNDARY,
        sessions=SESSIONS,
        config=PurgeConfig(embargo_sessions=1),
    )
    assert result.n_kept == 1
    assert result.n_purged == 0


# -- integration with the walk-forward folds ---------------------------------


def test_fold_training_rows_are_purged_at_the_validation_boundary() -> None:
    fold = walk_forward_folds(SESSIONS, n_folds=1, validation_sessions=2, min_train_sessions=3)[0]
    boundary = to_utc(from_ist(fold.validation_sessions[0], SESSION_OPEN))
    last_train = fold.train_sessions[-1]
    safe = to_utc(from_ist(last_train, SESSION_OPEN) + timedelta(minutes=300))
    # A long-horizon label decided before the close whose outcome is realised
    # after the validation session has opened.
    late = to_utc(from_ist(last_train, SESSION_CLOSE) - timedelta(minutes=5))
    rows = [
        labelled_row("AAA", safe, span_minutes=30),
        labelled_row("AAA", late, ends_at_boundary=boundary + timedelta(minutes=10)),
    ]
    result = fold.training_rows(rows)
    assert result.n_kept == 1
    assert [p.reason for p in result.purged] == [PurgeReason.LABEL_INTERVAL_OVERLAPS_BOUNDARY]


def test_fold_embargo_sessions_are_honoured() -> None:
    fold = walk_forward_folds(
        SESSIONS,
        n_folds=1,
        validation_sessions=2,
        min_train_sessions=3,
        embargo_sessions=1,
    )[0]
    second_to_last = fold.train_sessions[-1]
    rows = [
        labelled_row(
            "AAA",
            to_utc(from_ist(second_to_last, SESSION_OPEN) + timedelta(minutes=30)),
            span_minutes=1,
        )
    ]
    result = fold.training_rows(rows)
    assert result.n_purged == 1
    assert result.purged[0].reason is PurgeReason.EMBARGO
