"""Rung 3: cost-aware deterministic filter (plan 9.1).

Reject a candidate when its TRAINING-ONLY expected gross opportunity is not
sufficient versus estimated cost plus a pre-registered safety margin.

The causality rule that matters: the expected move must NEVER be estimated from
future returns of the candidate being decided. So the policy holds a frozen
:class:`TrainingEstimates` table built from training-fold rows only, keyed by a
predeclared point-in-time bucket. Deciding a candidate is a pure function of

    (that bucket's training means, the safety margin, the candidate's bucket)

The candidate's own realised return is not an input, and neither is any
validation or holdout row: :meth:`TrainingEstimates.fit` refuses rows outside
``training_sessions``, so estimates cannot be quietly refitted on data the model
must not see. Injecting future rows into the candidate stream therefore cannot
change a single decision.

A bucket with no training support is REJECTED with ``no_training_estimate`` -
not imputed, not defaulted to an optimistic value. That is also the missing-data
policy shared with the other policies (plan 9.2).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from jev_imrp.candidates.schema import CandidateRecord
from jev_imrp.common.timeutils import ensure_aware, ist_session_date
from jev_imrp.models.policy import Decision, PolicyDecision, PolicyError

COST_AWARE_POLICY_VERSION = "cost_aware_v1"

#: Pre-registered safety margin over estimated cost, in basis points.
DEFAULT_SAFETY_MARGIN_BPS = 2.0

DEFAULT_BUCKET_KEY = "tod_minutes_since_open"
DEFAULT_GROSS_KEY = "gross_bps"
DEFAULT_COST_KEY = "cost_bps"


@dataclass(frozen=True, slots=True)
class TrainingEstimates:
    """Mean gross opportunity and mean cost in bps, per predeclared bucket.

    Built from training-fold rows only. ``training_sessions`` is recorded so the
    table's provenance is visible in a manifest or a ledger note.
    """

    gross_bps_by_bucket: Mapping[Any, float]
    cost_bps_by_bucket: Mapping[Any, float]
    training_sessions: frozenset[date]
    n_training_rows: int
    horizon_minutes: int = 30
    version: str = COST_AWARE_POLICY_VERSION

    @staticmethod
    def fit(
        training_rows: Iterable[Mapping[str, Any]],
        *,
        training_sessions: Iterable[date] | None = None,
        bucket_key: str = DEFAULT_BUCKET_KEY,
        gross_key: str = DEFAULT_GROSS_KEY,
        cost_key: str = DEFAULT_COST_KEY,
        horizon_minutes: int = 30,
        timestamp_key: str = "decision_timestamp_utc",
    ) -> TrainingEstimates:
        """Mean per-bucket estimates from training rows.

        ``training_sessions`` is the structural guard: when given, any row from
        another session (validation, locked holdout, later data) raises
        :class:`~jev_imrp.validation.temporal_splits.SplitLeakError` instead of
        silently widening the estimate.
        """
        from jev_imrp.validation.temporal_splits import SplitLeakError  # local: avoid cycle

        allowed = set(training_sessions) if training_sessions is not None else None
        gross: dict[Any, list[float]] = {}
        cost: dict[Any, list[float]] = {}
        sessions: set[date] = set()
        count = 0
        for row in training_rows:
            stamp = row.get(timestamp_key)
            if not isinstance(stamp, datetime):
                raise PolicyError(f"training row has no usable {timestamp_key}")
            session = ist_session_date(ensure_aware(stamp))
            if allowed is not None and session not in allowed:
                raise SplitLeakError(
                    f"cost-aware estimates: row from session {session.isoformat()} is not a "
                    "training session; expected move may not be estimated from it"
                )
            bucket = row.get(bucket_key)
            gross_value = row.get(gross_key)
            cost_value = row.get(cost_key)
            if bucket is None or gross_value is None or cost_value is None:
                continue
            gross.setdefault(bucket, []).append(float(gross_value))
            cost.setdefault(bucket, []).append(float(cost_value))
            sessions.add(session)
            count += 1
        if not gross:
            raise PolicyError("no usable training rows for cost-aware estimates")
        return TrainingEstimates(
            gross_bps_by_bucket={key: sum(values) / len(values) for key, values in gross.items()},
            cost_bps_by_bucket={key: sum(values) / len(values) for key, values in cost.items()},
            training_sessions=frozenset(sessions),
            n_training_rows=count,
            horizon_minutes=horizon_minutes,
        )

    def estimate_for(self, bucket: Any) -> tuple[float, float] | None:
        """(expected gross bps, estimated cost bps) for a bucket, or ``None``."""
        if bucket not in self.gross_bps_by_bucket:
            return None
        return self.gross_bps_by_bucket[bucket], self.cost_bps_by_bucket.get(bucket, 0.0)

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "horizon_minutes": self.horizon_minutes,
            "n_training_rows": self.n_training_rows,
            "n_training_sessions": len(self.training_sessions),
            "buckets": {
                str(key): [self.gross_bps_by_bucket[key], self.cost_bps_by_bucket.get(key, 0.0)]
                for key in sorted(self.gross_bps_by_bucket, key=str)
            },
        }


@dataclass(frozen=True, slots=True)
class CostAwarePolicy:
    """Accept when training-only expected gross beats cost plus the margin."""

    estimates: TrainingEstimates
    safety_margin_bps: float = DEFAULT_SAFETY_MARGIN_BPS
    bucket_key: str = DEFAULT_BUCKET_KEY
    seed: int = 0
    name: str = "cost_aware"
    version: str = COST_AWARE_POLICY_VERSION

    def config(self) -> dict[str, Any]:
        return {
            "safety_margin_bps": self.safety_margin_bps,
            "bucket_key": self.bucket_key,
            "horizon_minutes": self.estimates.horizon_minutes,
            "n_training_rows": self.estimates.n_training_rows,
            "n_training_sessions": len(self.estimates.training_sessions),
        }

    def decide(self, candidate: CandidateRecord) -> PolicyDecision:
        bucket = candidate.feature(self.bucket_key)
        if bucket is None:
            return PolicyDecision(
                candidate.signal_id, Decision.REJECT, f"missing_feature:{self.bucket_key}"
            )
        estimate = self.estimates.estimate_for(bucket)
        if estimate is None:
            return PolicyDecision(
                candidate.signal_id, Decision.REJECT, "no_training_estimate_for_bucket"
            )
        gross, cost = estimate
        net = gross - cost - self.safety_margin_bps
        if net <= 0.0:
            return PolicyDecision(
                candidate.signal_id,
                Decision.REJECT,
                "expected_gross_insufficient_vs_cost",
                score=net,
            )
        return PolicyDecision(
            candidate.signal_id,
            Decision.ACCEPT,
            "expected_gross_exceeds_cost_and_margin",
            score=net,
        )

    def decide_batch(self, candidates: Sequence[CandidateRecord]) -> tuple[PolicyDecision, ...]:
        return tuple(self.decide(candidate) for candidate in candidates)


__all__: tuple[str, ...] = (
    "COST_AWARE_POLICY_VERSION",
    "DEFAULT_BUCKET_KEY",
    "DEFAULT_COST_KEY",
    "DEFAULT_GROSS_KEY",
    "DEFAULT_SAFETY_MARGIN_BPS",
    "CostAwarePolicy",
    "TrainingEstimates",
)
