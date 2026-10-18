"""Rung 2: matched random filter with repeated seeded draws (plan 9.1).

The random filter is the honest control for any selective policy: it accepts at
the target coverage rate, matched within predeclared strata (a trading SESSION by
default, so a coverage-matching random policy faces the same daily context as the
model it controls), and it ranks nothing. A model that cannot beat this has not
shown selection skill.

Matching is exact per stratum: a stratum of *n* candidates accepts
``floor(n * target_coverage)`` of them, drawn without replacement by a seeded
shuffle. So the realised coverage matches the target up to one candidate per
session, not up to a global average that could concentrate in a quiet day.

Repeated draws: :meth:`RandomPolicy.draws` produces a family of policies with
consecutive seeds, so a comparison uses the DISTRIBUTION of random results
rather than one lucky draw (plan 9.1).
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from jev_imrp.candidates.schema import CandidateRecord
from jev_imrp.models.policy import Decision, PolicyDecision, PolicyError

RANDOM_POLICY_VERSION = "random_v1"

#: Predeclared strata. ``session`` is the plan's day/session stratum.
DEFAULT_STRATUM = "session"


@dataclass(frozen=True, slots=True)
class RandomPolicy:
    """Accept ``target_coverage`` of candidates, matched within strata."""

    target_coverage: float
    seed: int
    stratum: str = DEFAULT_STRATUM
    name: str = "matched_random"
    version: str = RANDOM_POLICY_VERSION

    def __post_init__(self) -> None:
        if not 0.0 <= self.target_coverage <= 1.0:
            raise PolicyError("target_coverage must lie in [0, 1]")

    def config(self) -> dict[str, Any]:
        return {"target_coverage": self.target_coverage, "stratum": self.stratum, "seed": self.seed}

    def _stratum_of(self, candidate: CandidateRecord) -> str:
        if self.stratum == "session":
            return candidate.session.isoformat()
        if self.stratum == "symbol":
            return candidate.symbol
        if self.stratum == "symbol_session":
            return f"{candidate.symbol}|{candidate.session.isoformat()}"
        raise PolicyError(f"unknown stratum: {self.stratum}")

    def accepted_ids(self, candidates: Sequence[CandidateRecord]) -> frozenset[str]:
        """The accepted signal ids for this seed. Pure function of the inputs."""
        strata: dict[str, list[CandidateRecord]] = {}
        for candidate in candidates:
            strata.setdefault(self._stratum_of(candidate), []).append(candidate)

        accepted: set[str] = set()
        for key in sorted(strata):
            members = strata[key]
            k = int(len(members) * self.target_coverage)
            if k <= 0:
                continue
            order = sorted(members, key=lambda c: c.signal_id)
            rng = random.Random(f"{self.seed}|{self.stratum}|{key}")
            for chosen in rng.sample(order, k):
                accepted.add(chosen.signal_id)
        return frozenset(accepted)

    def decide(self, candidate: CandidateRecord) -> PolicyDecision:
        """Per-candidate verdict against the batch draw.

        Callers should use :meth:`decide_batch`: coverage matching is a property
        of the batch, so a single-candidate call cannot honour it.
        """
        accepted = self.accepted_ids((candidate,))
        return self._verdict(candidate, candidate.signal_id in accepted)

    def _verdict(self, candidate: CandidateRecord, accepted: bool) -> PolicyDecision:
        if accepted:
            return PolicyDecision(candidate.signal_id, Decision.ACCEPT, "random_matched_draw")
        return PolicyDecision(candidate.signal_id, Decision.REJECT, "random_not_drawn", score=0.0)

    def decide_batch(self, candidates: Sequence[CandidateRecord]) -> tuple[PolicyDecision, ...]:
        accepted = self.accepted_ids(candidates)
        return tuple(
            self._verdict(candidate, candidate.signal_id in accepted) for candidate in candidates
        )

    def stratum_coverage(self, candidates: Sequence[CandidateRecord]) -> dict[str, float]:
        """Realised coverage per stratum - the matching that plan 9.2 requires."""
        accepted = self.accepted_ids(candidates)
        counts: dict[str, list[int]] = {}
        for candidate in candidates:
            bucket = counts.setdefault(self._stratum_of(candidate), [0, 0])
            bucket[0] += 1
            bucket[1] += int(candidate.signal_id in accepted)
        return {key: taken / total for key, (total, taken) in sorted(counts.items())}

    @staticmethod
    def draws(
        target_coverage: float,
        *,
        n_draws: int,
        base_seed: int,
        stratum: str = DEFAULT_STRATUM,
    ) -> tuple[RandomPolicy, ...]:
        """``n_draws`` independent seeded policies - use the whole family."""
        if n_draws < 1:
            raise PolicyError("n_draws must be positive")
        return tuple(
            RandomPolicy(target_coverage=target_coverage, seed=base_seed + index, stratum=stratum)
            for index in range(n_draws)
        )


__all__ = (
    "DEFAULT_STRATUM",
    "RANDOM_POLICY_VERSION",
    "RandomPolicy",
)
