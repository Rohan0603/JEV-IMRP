"""The one shared accept/reject policy interface (plan 8.1, 9.1, 9.2).

Every rung of the model ladder - unfiltered base, matched random, cost-aware
deterministic, fixed mechanical rules, and later the linear baseline, LightGBM
and JEV - implements :class:`Policy`: a per-candidate ``ACCEPT``/``REJECT``
decision plus a batch API over the SAME candidate stream. A filter never chooses
a direction and never sizes a position; it only selects.

Fairness (plan 9.2). The shared conventions every policy inherits:

* same candidates and side;
* same labels, horizon, execution and fee model;
* same test sessions and same missing-data policy (a policy that needs a feature
  the candidate does not carry REJECTS with an explicit ``missing_feature``
  reason; it never imputes and never invents a value);
* explicit acceptance-rate convention:

      acceptance_rate = accepted / candidates presented

  where ``candidates presented`` is every candidate handed to the batch API,
  including candidates the candidate strategy itself marked ineligible. The
  denominator is therefore fixed across policies and comparable by construction.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from jev_imrp.candidates.schema import CandidateRecord
from jev_imrp.common.ledger import append_trial
from jev_imrp.common.timeutils import utc_now


class PolicyError(Exception):
    """A policy was configured or used in a way that would break fairness."""


class Decision(StrEnum):
    """The only two things a policy may say about a candidate."""

    ACCEPT = "ACCEPT"
    REJECT = "REJECT"


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """One policy verdict, with a reason code and an optional score.

    ``score`` is a policy-internal ranking value (e.g. the matched-random draw
    rank). It is NOT a probability of profit and must not be read as one (plan
    9.3).
    """

    signal_id: str
    decision: Decision
    reason: str
    score: float | None = None

    @property
    def accepted(self) -> bool:
        return self.decision is Decision.ACCEPT

    def as_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "decision": str(self.decision),
            "reason": self.reason,
            "score": self.score,
        }


@runtime_checkable
class Policy(Protocol):
    """Shared interface: per-candidate and batch accept/reject."""

    name: str
    version: str
    seed: int

    def decide(self, candidate: CandidateRecord) -> PolicyDecision: ...

    def decide_batch(self, candidates: Sequence[CandidateRecord]) -> tuple[PolicyDecision, ...]: ...


@dataclass(frozen=True, slots=True)
class PolicyResult:
    """Batch outcome plus the explicit acceptance-rate convention of plan 9.2."""

    policy_name: str
    policy_version: str
    seed: int
    decisions: tuple[PolicyDecision, ...]

    @property
    def accepted(self) -> tuple[PolicyDecision, ...]:
        return tuple(d for d in self.decisions if d.accepted)

    @property
    def n_presented(self) -> int:
        return len(self.decisions)

    @property
    def n_accepted(self) -> int:
        return len(self.accepted)

    @property
    def acceptance_rate(self) -> float:
        """accepted / candidates presented (see module docstring)."""
        return self.n_accepted / self.n_presented if self.n_presented else 0.0

    def accept_ids(self) -> tuple[str, ...]:
        return tuple(d.signal_id for d in self.accepted)

    def summary(self) -> dict[str, Any]:
        return {
            "policy": self.policy_name,
            "version": self.policy_version,
            "seed": self.seed,
            "n_presented": self.n_presented,
            "n_accepted": self.n_accepted,
            "acceptance_rate": self.acceptance_rate,
        }


def apply_policy(policy: Policy, candidates: Sequence[CandidateRecord]) -> PolicyResult:
    """Run a policy over the shared candidate stream."""
    decisions = policy.decide_batch(tuple(candidates))
    if len(decisions) != len(candidates):
        raise PolicyError(
            f"{policy.name}: returned {len(decisions)} decisions for {len(candidates)} candidates"
        )
    expected = [c.signal_id for c in candidates]
    if [d.signal_id for d in decisions] != expected:
        raise PolicyError(f"{policy.name}: decisions do not match the presented candidates")
    return PolicyResult(
        policy_name=policy.name,
        policy_version=policy.version,
        seed=policy.seed,
        decisions=tuple(decisions),
    )


@dataclass(frozen=True, slots=True)
class UnfilteredPolicy:
    """Rung 1: accept every candidate.

    It exists to reveal whether the candidate strategy has a net opportunity at
    all, so it must not filter anything - not even ineligibility it did not
    create.
    """

    seed: int = 0
    name: str = "unfiltered"
    version: str = "unfiltered_v1"

    def decide(self, candidate: CandidateRecord) -> PolicyDecision:
        return PolicyDecision(candidate.signal_id, Decision.ACCEPT, "rung1_accept_all")

    def decide_batch(self, candidates: Sequence[CandidateRecord]) -> tuple[PolicyDecision, ...]:
        return tuple(self.decide(candidate) for candidate in candidates)

    def config(self) -> dict[str, Any]:
        return {}


# -- reproducibility: every configuration registers one ledger line ----------


def config_digest(config: Mapping[str, Any]) -> str:
    """Short, stable digest of a policy configuration for the trial ledger."""
    payload = json.dumps(dict(sorted(config.items())), sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def register_policy_trial(
    policy: Policy,
    *,
    experiment_id: str,
    trial_type: str = "baseline_filter",
    data_manifest: str = "unknown",
    universe_version: str = "unknown",
    feature_set_version: str = "unknown",
    candidate_strategy_version: str = "unknown",
    code_commit: str = "unknown",
    horizon_minutes: int = 30,
    cost_scenario: str = "base",
    train_dates: str = "",
    validation_dates: str = "",
    test_dates: str = "",
    n_trades_primary_metric: int | str = "",
    primary_metric_value: float | str = "",
    secondary_metrics: Mapping[str, Any] | None = None,
    notes: str = "",
    ledger_path: str | Path | None = None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Append exactly one line to ``experiments/trial_ledger.csv`` via the helper.

    The CSV header is the schema, so the row is built from the ledger's own
    columns; nothing here writes the CSV by hand.
    """
    config_map = dict(config) if config is not None else dict(getattr(policy, "config", dict)())
    digest = config_digest(config_map)
    trial_id = f"{policy.name}-{digest}"
    row: dict[str, Any] = {
        "trial_id": trial_id,
        "experiment_id": experiment_id,
        "trial_type": trial_type,
        "status": "registered",
        "registered_at_utc": utc_now().isoformat(),
        "code_commit": code_commit,
        "data_manifest": data_manifest,
        "universe_version": universe_version,
        "feature_set_version": feature_set_version,
        "candidate_strategy_version": candidate_strategy_version,
        "horizon_minutes": horizon_minutes,
        "target": "net_return_30m",
        "cost_scenario": cost_scenario,
        "model_family": "baseline_policy",
        "model_version": policy.version,
        "prompt_version": "",
        "threshold_or_config": json.dumps(
            {"seed": policy.seed, **config_map}, sort_keys=True, default=str
        ),
        "train_dates": train_dates,
        "validation_dates": validation_dates,
        "test_dates": test_dates,
        "n_trades_primary_metric": n_trades_primary_metric,
        "primary_metric_value": primary_metric_value,
        "secondary_metrics_json": json.dumps(
            dict(sorted((secondary_metrics or {}).items())), default=str
        ),
        "notes": notes or f"SYNTHETIC-FREE policy trial for {policy.name}; digest {digest}",
    }
    return append_trial(row, Path(ledger_path) if ledger_path else None)


__all__: tuple[str, ...] = (
    "Decision",
    "Policy",
    "PolicyDecision",
    "PolicyError",
    "PolicyResult",
    "UnfilteredPolicy",
    "apply_policy",
    "config_digest",
    "register_policy_trial",
)
