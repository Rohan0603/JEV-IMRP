"""Trial ledger access (plan sections 3.3 / 9.4).

The CSV header is the schema; it lives in the file, not in this module, so the
schema is visible to a human reader of the repository.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jev_imrp.common.config import REPO_ROOT
from jev_imrp.common.errors import LedgerError

LEDGER_PATH: Path = REPO_ROOT / "experiments" / "trial_ledger.csv"

REQUIRED_COLUMNS: tuple[str, ...] = (
    "trial_id",
    "experiment_id",
    "trial_type",
    "status",
    "registered_at_utc",
    "code_commit",
    "data_manifest",
    "universe_version",
    "feature_set_version",
    "candidate_strategy_version",
    "horizon_minutes",
    "target",
    "cost_scenario",
    "model_family",
    "model_version",
    "prompt_version",
    "threshold_or_config",
    "train_dates",
    "validation_dates",
    "test_dates",
    "n_trades_primary_metric",
    "primary_metric_value",
    "secondary_metrics_json",
    "notes",
)

AUDIT_PROVENANCE_COLUMNS: tuple[str, ...] = (
    "code_commit",
    "data_manifest",
    "universe_version",
    "feature_set_version",
    "candidate_strategy_version",
)
VALID_STATUSES = frozenset({"registered", "running", "completed", "failed", "abandoned"})
PLACEHOLDER_VALUES = frozenset({"", "unknown", "tbd", "not_verified", "unverified"})


def ledger_columns(path: Path | None = None) -> list[str]:
    """Column names from the ledger header."""
    resolved = path or LEDGER_PATH
    try:
        with resolved.open(newline="", encoding="utf-8") as handle:
            header = next(csv.reader(handle), [])
    except OSError as exc:
        raise LedgerError(f"cannot read trial ledger: {resolved}") from exc
    if not header:
        raise LedgerError(f"trial ledger has no header: {resolved}")
    return header


def load_ledger(path: Path | None = None) -> list[dict[str, str]]:
    """All ledger rows as dicts. The empty ledger yields an empty list."""
    resolved = path or LEDGER_PATH
    with resolved.open(newline="", encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def append_trial(row: Mapping[str, Any], path: Path | None = None) -> dict[str, str]:
    """Append one trial row, refusing unknown or missing columns."""
    resolved = path or LEDGER_PATH
    columns = ledger_columns(resolved)
    unknown = set(row) - set(columns)
    missing = [c for c in REQUIRED_COLUMNS if c not in row]
    if unknown:
        raise LedgerError(f"unknown ledger column(s): {sorted(unknown)}")
    if missing:
        raise LedgerError(f"missing ledger column(s): {sorted(missing)}")
    written = {c: "" if row[c] is None else str(row[c]) for c in columns}
    with resolved.open("a", newline="", encoding="utf-8") as handle:
        csv.DictWriter(handle, fieldnames=columns).writerow(written)
    return written


def audit_ledger(path: Path | None = None) -> list[str]:
    """Return reproducible integrity findings for an append-only trial ledger.

    The CSV cannot prove historical file edits without an external snapshot, so
    append order is treated as authoritative and trial IDs must never repeat.
    Placeholder provenance values are findings rather than accepted metadata;
    this keeps an unresolved baseline visible to callers and reviewers.
    """
    resolved = path or LEDGER_PATH
    try:
        columns = ledger_columns(resolved)
        rows = load_ledger(resolved)
    except (LedgerError, OSError) as exc:
        return [str(exc)]

    findings: list[str] = []
    if columns != list(REQUIRED_COLUMNS):
        findings.append(
            "ledger header does not match the required schema: "
            f"expected {list(REQUIRED_COLUMNS)!r}, got {columns!r}"
        )

    seen_trial_ids: set[str] = set()
    for row_number, row in enumerate(rows, start=2):
        trial_id = row.get("trial_id", "").strip()
        if not trial_id:
            findings.append(f"row {row_number}: missing trial_id")
        elif trial_id in seen_trial_ids:
            findings.append(f"row {row_number}: duplicate trial_id {trial_id!r}")
        else:
            seen_trial_ids.add(trial_id)

        status = row.get("status", "").strip().lower()
        if status not in VALID_STATUSES:
            findings.append(f"row {row_number}: invalid status {status!r}")

        for column in AUDIT_PROVENANCE_COLUMNS:
            value = row.get(column, "").strip()
            if value.lower() in PLACEHOLDER_VALUES or "tbd" in value.lower():
                findings.append(f"row {row_number}: {column} contains a placeholder")

        metrics = row.get("secondary_metrics_json", "").strip()
        try:
            parsed_metrics = json.loads(metrics)
        except json.JSONDecodeError:
            findings.append(f"row {row_number}: secondary_metrics_json is not valid JSON")
        else:
            if not isinstance(parsed_metrics, dict):
                findings.append(f"row {row_number}: secondary_metrics_json must be a JSON object")

    return findings
