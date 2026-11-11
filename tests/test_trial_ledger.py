"""Trial ledger schema loads and round-trips (plan 3.3 / 9.4)."""

from __future__ import annotations

import shutil

import pytest

from jev_imrp.common.errors import LedgerError
from jev_imrp.common.ledger import (
    LEDGER_PATH,
    REQUIRED_COLUMNS,
    append_trial,
    audit_ledger,
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


def test_shipped_ledger_matches_schema() -> None:
    assert ledger_columns() == list(REQUIRED_COLUMNS)


def test_round_trip(ledger) -> None:
    initialCount = len(load_ledger(ledger))
    written = append_trial(ROW, ledger)
    rows = load_ledger(ledger)

    assert len(rows) == initialCount + 1
    assert rows[-1] == written
    assert rows[-1]["trial_id"] == "0001"
    assert rows[-1]["horizon_minutes"] == "30"


def test_append_preserves_previousRows(ledger) -> None:
    initialCount = len(load_ledger(ledger))
    append_trial(ROW, ledger)
    append_trial({**ROW, "trial_id": "0002", "status": "abandoned"}, ledger)

    rows = load_ledger(ledger)
    assert len(rows) == initialCount + 2
    assert rows[-1]["trial_id"] == "0002"
    assert rows[-1]["status"] == "abandoned"  # failures/abandonments still count as trials


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


def test_audit_accepts_complete_ledger(tmp_path) -> None:
    path = tmp_path / "trial_ledger.csv"
    path.write_text(",".join(REQUIRED_COLUMNS) + "\n", encoding="utf-8")
    complete = {
        **ROW,
        "trial_id": "trial-0001",
        "code_commit": "0123456789abcdef",
        "data_manifest": "sha256:manifest-1",
        "universe_version": "sha256:universe-1",
        "feature_set_version": "sha256:features-1",
        "candidate_strategy_version": "sha256:candidates-1",
        "secondary_metrics_json": '{"hit_rate": 0.5}',
    }
    append_trial(complete, path)

    assert audit_ledger(path) == []


def test_audit_reports_duplicate_ids_and_unresolved_metadata(tmp_path) -> None:
    path = tmp_path / "trial_ledger.csv"
    path.write_text(",".join(REQUIRED_COLUMNS) + "\n", encoding="utf-8")
    incomplete = {
        **ROW,
        "trial_id": "trial-0001",
        "code_commit": "unknown",
        "data_manifest": "manifests/TBD",
        "secondary_metrics_json": "not-json",
    }
    append_trial(incomplete, path)
    append_trial(incomplete, path)

    findings = audit_ledger(path)

    assert any("duplicate trial_id" in finding for finding in findings)
    assert any("code_commit" in finding and "placeholder" in finding for finding in findings)
    assert any("data_manifest" in finding and "placeholder" in finding for finding in findings)
    assert any("secondary_metrics_json" in finding for finding in findings)


def test_shipped_ledger_audit_exposes_unresolved_baseline() -> None:
    findings = audit_ledger()

    assert findings
    assert any("placeholder" in finding for finding in findings)
