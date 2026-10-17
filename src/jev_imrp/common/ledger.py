"""Trial ledger access (plan sections 3.3 / 9.4).

The CSV header is the schema; it lives in the file, not in this module, so the
schema is visible to a human reader of the repository.
"""

from __future__ import annotations

import csv
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
