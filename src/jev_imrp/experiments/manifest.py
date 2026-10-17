"""Experiment run manifest (plan 6.1 ``experiment_runs``, 12, 14.3).

A result is only interpretable if you can name the code, data, config, model,
prompt, splits and seeds that produced it. This manifest carries all of those
plus the run's outcome, and is hashed so two runs can be compared for
repeatability.

Metric outputs are declared as fields but left unset here: the evaluation worker
owns the numbers (plan 10.5, 11.4). Filling them in is a separate, later write
into the same manifest -- never a re-run with different assumptions.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from jev_imrp.common.config import REPO_ROOT
from jev_imrp.models.jev_client import ExecutionMode, FailurePolicy, canonical_hash

MANIFEST_SCHEMA_VERSION = "jev_manifest_v1"


class RunStatus(StrEnum):
    """Completion status. ``partial`` means the run finished with failed attempts."""

    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PARTIAL = "partial"


def git_commit(repo_root: Path | None = None) -> str:
    """Current commit hash, or ``unknown`` outside a checkout (never fabricated)."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_root or REPO_ROOT),
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return out.stdout.strip() or "unknown"


@dataclass(frozen=True)
class TrialConfig:
    """Model parameters, seeds and thresholds -- everything a rerun must repeat."""

    model_id: str
    prompt_version: str
    prompt_hash: str
    question_ids: tuple[str, ...]
    accept_threshold: float
    confirmatory: bool = True
    max_concurrency: int = 4
    timeout_s: float = 30.0
    max_retries: int = 3
    seeds: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class SplitBounds:
    """Chronological split boundaries as dates (plan 10.1)."""

    train: tuple[str, ...] = ()
    validation: tuple[str, ...] = ()
    test: tuple[str, ...] = ()

    def to_record(self) -> dict[str, list[str]]:
        return {
            "train": list(self.train),
            "validation": list(self.validation),
            "test": list(self.test),
        }


@dataclass(frozen=True)
class ExperimentManifest:
    """The full run record. One instance per experiment run, written once."""

    experiment_id: str
    code_commit: str
    config_snapshot: dict[str, Any]
    config_hash: str
    dataset_id: str
    dataset_manifest: str
    universe_version: str
    sample_period: tuple[str, str]
    feature_version: str
    label_version: str
    cost_version: str
    splits: SplitBounds
    trial: TrialConfig
    failure_policy: FailurePolicy
    execution_mode: ExecutionMode
    status: RunStatus = RunStatus.PARTIAL
    warnings: tuple[str, ...] = ()
    failures: tuple[str, ...] = ()
    artifact_paths: dict[str, str] = field(default_factory=dict)
    metric_outputs: dict[str, Any] = field(default_factory=dict)
    coverage_outputs: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def config_hash_of(snapshot: dict[str, Any]) -> str:
        return canonical_hash(snapshot)

    @staticmethod
    def from_config(
        *,
        experiment_id: str,
        config_snapshot: dict[str, Any],
        dataset_id: str,
        dataset_manifest: str,
        universe_version: str,
        sample_period: tuple[str, str],
        feature_version: str,
        label_version: str,
        cost_version: str,
        splits: SplitBounds,
        trial: TrialConfig,
        failure_policy: FailurePolicy,
        execution_mode: ExecutionMode,
        code_commit: str | None = None,
    ) -> ExperimentManifest:
        """Build a manifest from declared inputs; the hash is derived, not typed."""
        return ExperimentManifest(
            experiment_id=experiment_id,
            code_commit=code_commit or git_commit(),
            config_snapshot=config_snapshot,
            config_hash=ExperimentManifest.config_hash_of(config_snapshot),
            dataset_id=dataset_id,
            dataset_manifest=dataset_manifest,
            universe_version=universe_version,
            sample_period=sample_period,
            feature_version=feature_version,
            label_version=label_version,
            cost_version=cost_version,
            splits=splits,
            trial=trial,
            failure_policy=failure_policy,
            execution_mode=execution_mode,
        )

    def with_run_outcome(
        self,
        *,
        status: RunStatus,
        coverage_outputs: dict[str, Any],
        artifact_paths: dict[str, str],
        warnings: tuple[str, ...] = (),
        failures: tuple[str, ...] = (),
    ) -> ExperimentManifest:
        """Return the finished manifest (immutable dataclass, so this copies)."""
        return ExperimentManifest(
            **{
                **asdict(self),
                "status": status,
                "coverage_outputs": coverage_outputs,
                "artifact_paths": artifact_paths,
                "warnings": warnings,
                "failures": failures,
            }
        )

    def to_record(self) -> dict[str, Any]:
        """Flat, JSON-serialisable record -- what gets written to disk."""
        return {
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "experiment_id": self.experiment_id,
            "code_commit": self.code_commit,
            "config_hash": self.config_hash,
            "config_snapshot": self.config_snapshot,
            "dataset_id": self.dataset_id,
            "dataset_manifest": self.dataset_manifest,
            "universe_version": self.universe_version,
            "sample_period": list(self.sample_period),
            "feature_version": self.feature_version,
            "label_version": self.label_version,
            "cost_version": self.cost_version,
            "splits": self.splits.to_record(),
            "trial": {
                **asdict(self.trial),
                "question_ids": list(self.trial.question_ids),
            },
            "failure_policy": self.failure_policy,
            "execution_mode": self.execution_mode,
            "status": str(self.status),
            "warnings": list(self.warnings),
            "failures": list(self.failures),
            "artifact_paths": dict(self.artifact_paths),
            "metric_outputs": dict(self.metric_outputs),
            "coverage_outputs": dict(self.coverage_outputs),
        }

    @property
    def is_reproducible(self) -> bool:
        """True when the manifest names every input a rerun must not change."""
        required = (
            self.code_commit,
            self.config_hash,
            self.dataset_manifest,
            self.feature_version,
            self.label_version,
            self.cost_version,
            self.trial.model_id,
            self.trial.prompt_hash,
        )
        return all(v and v != "unknown" for v in required)

    def write(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_record(), indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        return target


def register_trial(
    manifest: ExperimentManifest,
    *,
    trial_id: str,
    status: str,
    registered_at_utc: str,
    data_manifest: str,
    threshold_or_config: str,
    horizon_minutes: int,
    target: str,
    cost_scenario: str,
    candidate_strategy_version: str,
    train_dates: str = "",
    validation_dates: str = "",
    test_dates: str = "",
    n_trades_primary_metric: int = 0,
    primary_metric_value: str = "",
    secondary_metrics_json: str = "{}",
    notes: str = "",
    path: Path | None = None,
) -> dict[str, str]:
    """Append this configuration to the trial ledger through the existing helper.

    Every JEV configuration is a trial (plan 3.3), including one that later fails;
    prompt variants count too (plan 9.5).
    """
    from jev_imrp.common.ledger import append_trial

    return append_trial(
        {
            "trial_id": trial_id,
            "experiment_id": manifest.experiment_id,
            "trial_type": "jev",
            "status": status,
            "registered_at_utc": registered_at_utc,
            "code_commit": manifest.code_commit,
            "data_manifest": data_manifest,
            "universe_version": manifest.universe_version,
            "feature_set_version": manifest.feature_version,
            "candidate_strategy_version": candidate_strategy_version,
            "horizon_minutes": horizon_minutes,
            "target": target,
            "cost_scenario": cost_scenario,
            "model_family": "jev",
            "model_version": manifest.trial.model_id,
            "prompt_version": manifest.trial.prompt_version,
            "threshold_or_config": threshold_or_config,
            "train_dates": train_dates,
            "validation_dates": validation_dates,
            "test_dates": test_dates,
            "n_trades_primary_metric": n_trades_primary_metric,
            "primary_metric_value": primary_metric_value,
            "secondary_metrics_json": secondary_metrics_json,
            "notes": notes,
        },
        path,
    )


Terminal = Literal["accept", "reject", "skip", "fallback_accept", "fallback_reject"]
