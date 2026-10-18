"""Versioned candidate-signal record (plan 8.1).

Every model in the ladder - the unfiltered base, the random matcher, the
cost-aware filter, the rule set, the linear baseline, LightGBM and JEV - must
consume the SAME candidate stream. That is what isolates selection quality
(plan 8.1), so the record is versioned and carries its lineage: the strategy
version that produced it, the source event id, the intended horizon, and the
feature snapshot id plus feature hash actually shown to a filter.

The REAL candidate strategy does not exist yet; it is a pre-registered open item
(PREREGISTRATION.md). Nothing here pretends to be it.
:mod:`jev_imrp.candidates.synthetic` provides a clearly labelled SYNTHETIC
generator for tests and the end-to-end demo only.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from jev_imrp.common.errors import JevError
from jev_imrp.common.timeutils import ensure_aware, ist_session_date
from jev_imrp.features.causal_checks import assert_no_leaks

#: Bumped whenever the candidate record's meaning changes.
CANDIDATE_SCHEMA_VERSION = "cand_v1"

#: Strategy version string of the synthetic generator. Never a real strategy.
SYNTHETIC_STRATEGY_VERSION = "synthetic_v1"

REQUIRED_FIELDS: tuple[str, ...] = (
    "signal_id",
    "strategy_version",
    "symbol",
    "side",
    "decision_timestamp_utc",
    "source_event_id",
    "intended_horizon_minutes",
    "feature_snapshot_id",
    "feature_hash",
    "eligibility_status",
    "eligibility_reason",
)


class CandidateError(JevError):
    """A candidate row is not schema-valid."""


class Side(StrEnum):
    """Direction supplied by the candidate strategy, never by a filter."""

    BUY = "BUY"
    SELL = "SELL"


class EligibilityStatus(StrEnum):
    """Whether the candidate strategy itself considers the symbol tradable."""

    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"


@dataclass(frozen=True, slots=True)
class CandidateRecord:
    """One candidate signal, exactly as the shared stream delivers it."""

    signal_id: str
    strategy_version: str
    symbol: str
    side: Side
    decision_timestamp_utc: datetime
    source_event_id: str
    intended_horizon_minutes: int
    feature_snapshot_id: str
    feature_hash: str
    eligibility_status: EligibilityStatus
    eligibility_reason: str
    features: Mapping[str, float | str | None] = field(default_factory=dict)
    is_synthetic: bool = False
    schema_version: str = CANDIDATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in REQUIRED_FIELDS:
            if getattr(self, name) in (None, ""):
                raise CandidateError(f"candidate is missing required field: {name}")
        ensure_aware(self.decision_timestamp_utc)
        if not isinstance(self.side, Side):
            raise CandidateError("side must be a Side (BUY or SELL)")
        if not isinstance(self.eligibility_status, EligibilityStatus):
            raise CandidateError("eligibility_status must be an EligibilityStatus")
        if self.intended_horizon_minutes <= 0:
            raise CandidateError("intended_horizon_minutes must be positive")
        # Same allowlist enforcement as the training features (plan 7.4): a
        # candidate may not smuggle a future-looking column past a filter.
        assert_no_leaks(self.features.keys(), context=f"candidate {self.signal_id} features")

    @property
    def session(self) -> date:
        """IST trading-session date, the split unit of plan 10.1."""
        return ist_session_date(self.decision_timestamp_utc)

    @property
    def group_key(self) -> tuple[str, datetime]:
        """Rows that a split, purge or policy must never separate."""
        return (self.symbol, self.decision_timestamp_utc)

    @property
    def is_eligible(self) -> bool:
        return self.eligibility_status is EligibilityStatus.ELIGIBLE

    def feature(self, name: str) -> float | str | None:
        return self.features.get(name)

    def to_row(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "signal_id": self.signal_id,
            "strategy_version": self.strategy_version,
            "symbol": self.symbol,
            "side": str(self.side),
            "decision_timestamp_utc": self.decision_timestamp_utc,
            "source_event_id": self.source_event_id,
            "intended_horizon_minutes": self.intended_horizon_minutes,
            "feature_snapshot_id": self.feature_snapshot_id,
            "feature_hash": self.feature_hash,
            "eligibility_status": str(self.eligibility_status),
            "eligibility_reason": self.eligibility_reason,
            "is_synthetic": self.is_synthetic,
            "schema_version": self.schema_version,
        }
        for name, value in sorted(self.features.items()):
            row[f"feature_{name}"] = value
        return row

    @staticmethod
    def from_row(row: Mapping[str, Any]) -> CandidateRecord:
        schema_version = str(row.get("schema_version") or CANDIDATE_SCHEMA_VERSION)
        if schema_version != CANDIDATE_SCHEMA_VERSION:
            raise CandidateError(f"unsupported candidate schema version: {schema_version}")
        # ``feature_snapshot_id``/``feature_hash`` are lineage metadata, not features.
        metadata = {"feature_snapshot_id", "feature_hash"}
        features = {
            key[len("feature_") :]: value
            for key, value in row.items()
            if key.startswith("feature_") and key not in metadata
        }
        return CandidateRecord(
            signal_id=str(row["signal_id"]),
            strategy_version=str(row["strategy_version"]),
            symbol=str(row["symbol"]),
            side=Side(str(row["side"])),
            decision_timestamp_utc=ensure_aware(row["decision_timestamp_utc"]),
            source_event_id=str(row["source_event_id"]),
            intended_horizon_minutes=int(row["intended_horizon_minutes"]),
            feature_snapshot_id=str(row["feature_snapshot_id"]),
            feature_hash=str(row["feature_hash"]),
            eligibility_status=EligibilityStatus(str(row["eligibility_status"])),
            eligibility_reason=str(row["eligibility_reason"]),
            features=features,
            is_synthetic=bool(row.get("is_synthetic", False)),
            schema_version=schema_version,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "strategy_version": self.strategy_version,
            "symbol": self.symbol,
            "side": str(self.side),
            "decision_timestamp_utc": self.decision_timestamp_utc.isoformat(),
            "source_event_id": self.source_event_id,
            "intended_horizon_minutes": self.intended_horizon_minutes,
            "feature_snapshot_id": self.feature_snapshot_id,
            "feature_hash": self.feature_hash,
            "eligibility_status": str(self.eligibility_status),
            "eligibility_reason": self.eligibility_reason,
            "features": dict(sorted(self.features.items())),
            "is_synthetic": self.is_synthetic,
            "schema_version": self.schema_version,
        }


def validate_candidate_stream(candidates: list[CandidateRecord]) -> None:
    """Fail unless every signal_id is unique (construction already validated rows)."""
    seen: set[str] = set()
    for candidate in candidates:
        if candidate.signal_id in seen:
            raise CandidateError(f"duplicate signal_id: {candidate.signal_id}")
        seen.add(candidate.signal_id)


__all__: tuple[str, ...] = (
    "CANDIDATE_SCHEMA_VERSION",
    "REQUIRED_FIELDS",
    "SYNTHETIC_STRATEGY_VERSION",
    "CandidateError",
    "CandidateRecord",
    "EligibilityStatus",
    "Side",
    "validate_candidate_stream",
)
