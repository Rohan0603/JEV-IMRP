"""Trial ledger schema loads and round-trips (plan 3.3 / 9.4)."""

from __future__ import annotations

import shutil

import pytest

from jev_imrp.common.errors import LedgerError
from jev_imrp.common.ledger import (
    LEDGER_PATH,
    REQUIRED_COLUMNS,
    append_trial,
    ledger_columns,
    load_ledger,
)

ROW = {
    "trial_id": "0001",
    "experiment_id": "baseline_v1",
    "trial_type": "rules",
    "status": "completed",
    "registered_at_utc": "2026-10-17T09:30:00+00:00",
    "code_commit": "0000000",
    "data_manifest": "manifests/TBD",
    "universe_version": "universe_v1",
    "feature_set_version": "fs_v1",
    "candidate_strategy_version": "TBD",
    "horizon_minutes": 30,
    "target": "net_return_30m",
    "cost_scenario": "base",
    "model_family": "rules",
    "model_version": "rules_v0",
    "prompt_version": "",
    "threshold_or_config": "rule_set_v0",
    "train_dates": "TBD",
    "validation_dates": "TBD",
    "test_dates": "",
    "n_trades_primary_metric": 0,
    "primary_metric_value": "",
    "secondary_metrics_json": "{}",
    "notes": "synthetic test row",
}


@pytest.fixture()
def ledger(tmp_path):
    copy = tmp_path / "trial_ledger.csv"
    shutil.copy(LEDGER_PATH, copy)
    return copy


def test_shipped_ledger_is_empty_and_matches_schema() -> None:
    assert ledger_columns() == list(REQUIRED_COLUMNS)
    assert load_ledger() == []


def test_round_trip(ledger) -> None:
    written = append_trial(ROW, ledger)
    rows = load_ledger(ledger)

    assert len(rows) == 1
    assert rows[0] == written
    assert rows[0]["trial_id"] == "0001"
    assert rows[0]["horizon_minutes"] == "30"


def test_append_preserves_previous_rows(ledger) -> None:
    append_trial(ROW, ledger)
    append_trial({**ROW, "trial_id": "0002", "status": "abandoned"}, ledger)

    rows = load_ledger(ledger)
    assert [r["trial_id"] for r in rows] == ["0001", "0002"]
    assert rows[1]["status"] == "abandoned"  # failures/abandonments still count as trials


def test_unknown_column_is_rejected(ledger) -> None:
    with pytest.raises(LedgerError, match="unknown ledger column"):
        append_trial({**ROW, "leaked_holdout_metric": "0.1"}, ledger)


def test_missing_column_is_rejected(ledger) -> None:
    incomplete = {k: v for k, v in ROW.items() if k != "cost_scenario"}
    with pytest.raises(LedgerError, match="missing ledger column"):
        append_trial(incomplete, ledger)


def test_ledger_without_header_is_an_error(tmp_path) -> None:
    empty = tmp_path / "empty.csv"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(LedgerError, match="no header"):
        ledger_columns(empty)
