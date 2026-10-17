"""Candidate-signal stream shared by every model in the ladder (plan 8.1)."""

from __future__ import annotations

from jev_imrp.candidates.schema import (
    CANDIDATE_SCHEMA_VERSION,
    REQUIRED_FIELDS,
    SYNTHETIC_STRATEGY_VERSION,
    CandidateError,
    CandidateRecord,
    EligibilityStatus,
    Side,
    validate_candidate_stream,
)
from jev_imrp.candidates.synthetic import (
    SYNTHETIC_GENERATOR_VERSION,
    SyntheticGeneratorConfig,
    feature_hash,
    synthetic_candidates,
    synthetic_quote_rows,
)

__all__: tuple[str, ...] = (
    "CANDIDATE_SCHEMA_VERSION",
    "REQUIRED_FIELDS",
    "SYNTHETIC_GENERATOR_VERSION",
    "SYNTHETIC_STRATEGY_VERSION",
    "CandidateError",
    "CandidateRecord",
    "EligibilityStatus",
    "Side",
    "SyntheticGeneratorConfig",
    "feature_hash",
    "synthetic_candidates",
    "synthetic_quote_rows",
    "validate_candidate_stream",
)
