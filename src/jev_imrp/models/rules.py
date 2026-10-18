"""Rung 4: a small pre-registered set of mechanical rules (plan 9.1).

Three filter families, fixed here in code and NOT grid-searched:

* spread: reject when the L1 spread exceeds a maximum;
* volatility: reject when the session's ATR percentage is outside a band;
* time of day: reject outside the permitted minutes-after-open window (which
  excludes the opening auction and the closing ramp).

No grid search, no fitting, no seed sensitivity: the ruleset is a deterministic
function of the candidate's point-in-time features. Thresholds live in
:data:`PRE_REGISTERED_RULES` so a run can record exactly which rule set produced
its decisions.

Missing-data policy (shared with the other policies, plan 9.2): a rule whose
input feature is absent REJECTS the candidate with ``missing_feature:<name>``. It
never imputes and never treats an unknown value as passing.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from jev_imrp.candidates.schema import CandidateRecord
from jev_imrp.models.policy import Decision, PolicyDecision

RULES_VERSION = "rules_v1"

SPREAD_FEATURE = "spread_bps"
VOLATILITY_FEATURE = "atr_pct"
TIME_FEATURE = "tod_minutes_since_open"


@dataclass(frozen=True, slots=True)
class RuleSpec:
    """Pre-registered thresholds. Bumping ``version`` is a trial, not a tweak."""

    max_spread_bps: float
    min_atr_pct: float
    max_atr_pct: float
    allowed_minutes: tuple[tuple[float, float], ...]
    version: str = RULES_VERSION

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "max_spread_bps": self.max_spread_bps,
            "min_atr_pct": self.min_atr_pct,
            "max_atr_pct": self.max_atr_pct,
            "allowed_minutes": [list(window) for window in self.allowed_minutes],
        }


#: The pre-registered rule set. Frozen before any result was inspected.
PRE_REGISTERED_RULES = RuleSpec(
    max_spread_bps=8.0,
    min_atr_pct=0.05,
    max_atr_pct=5.0,
    # 15..330 minutes after the open: skip the opening auction and the last hour.
    allowed_minutes=((15.0, 330.0),),
)


@dataclass(frozen=True, slots=True)
class RulePolicy:
    """Deterministic mechanical filter over the shared candidate stream."""

    spec: RuleSpec = PRE_REGISTERED_RULES
    seed: int = 0
    name: str = "mechanical_rules"

    @property
    def version(self) -> str:
        return self.spec.version

    def config(self) -> dict[str, Any]:
        return self.spec.as_dict()

    def _within_tod(self, minutes: float) -> bool:
        return any(start <= minutes <= end for start, end in self.spec.allowed_minutes)

    def decide(self, candidate: CandidateRecord) -> PolicyDecision:
        spread = candidate.feature(SPREAD_FEATURE)
        atr = candidate.feature(VOLATILITY_FEATURE)
        minutes = candidate.feature(TIME_FEATURE)
        for name, value in (
            (SPREAD_FEATURE, spread),
            (VOLATILITY_FEATURE, atr),
            (TIME_FEATURE, minutes),
        ):
            if value is None:
                return PolicyDecision(
                    candidate.signal_id, Decision.REJECT, f"missing_feature:{name}"
                )

        if float(spread) > self.spec.max_spread_bps:  # type: ignore[arg-type]
            return PolicyDecision(candidate.signal_id, Decision.REJECT, "spread_above_max")
        if float(atr) < self.spec.min_atr_pct:  # type: ignore[arg-type]
            return PolicyDecision(candidate.signal_id, Decision.REJECT, "volatility_below_min")
        if float(atr) > self.spec.max_atr_pct:  # type: ignore[arg-type]
            return PolicyDecision(candidate.signal_id, Decision.REJECT, "volatility_above_max")
        if not self._within_tod(float(minutes)):  # type: ignore[arg-type]
            return PolicyDecision(candidate.signal_id, Decision.REJECT, "time_of_day_excluded")
        return PolicyDecision(candidate.signal_id, Decision.ACCEPT, "rules_passed")

    def decide_batch(self, candidates: Sequence[CandidateRecord]) -> tuple[PolicyDecision, ...]:
        return tuple(self.decide(candidate) for candidate in candidates)


__all__: tuple[str, ...] = (
    "PRE_REGISTERED_RULES",
    "RULES_VERSION",
    "SPREAD_FEATURE",
    "TIME_FEATURE",
    "VOLATILITY_FEATURE",
    "RulePolicy",
    "RuleSpec",
)
