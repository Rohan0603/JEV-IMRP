"""Experiment orchestration for the JEV challenger.

Three pieces, one per research-validity requirement in plan section 9.4:

* :mod:`~jev_imrp.experiments.attempt_store` -- append-only, idempotent durable
  persistence of **every** attempt, successful or failed;
* :mod:`~jev_imrp.experiments.manifest` -- the run manifest that makes a result
  reproducible and auditable (plan 6.1, 12, 14.3);
* :mod:`~jev_imrp.experiments.runner` -- the candidate-stream run: bounded
  concurrency, bounded retries on 429/529 only, the predeclared failure policy
  and the coverage/failure-rate outputs.
"""

from __future__ import annotations

from jev_imrp.experiments.attempt_store import ATTEMPT_STORE_SCHEMA_VERSION, AttemptStore
from jev_imrp.experiments.manifest import (
    MANIFEST_SCHEMA_VERSION,
    ExperimentManifest,
    RunStatus,
    SplitBounds,
    TrialConfig,
    git_commit,
)
from jev_imrp.experiments.runner import (
    COVERAGE_OUTPUTS,
    Decision,
    JevRunner,
    JevRunResult,
    apply_failure_policy,
)

__all__ = [
    "ATTEMPT_STORE_SCHEMA_VERSION",
    "COVERAGE_OUTPUTS",
    "MANIFEST_SCHEMA_VERSION",
    "AttemptStore",
    "Decision",
    "ExperimentManifest",
    "JevRunResult",
    "JevRunner",
    "RunStatus",
    "SplitBounds",
    "TrialConfig",
    "apply_failure_policy",
    "git_commit",
]
