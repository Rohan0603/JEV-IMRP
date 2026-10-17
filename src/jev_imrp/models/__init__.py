"""Model contestants.

The ladder order is fixed by plan 9.1: unfiltered, matched random, cost-aware
filter, fixed rules, linear, LightGBM, then JEV. Each contestant receives the
same candidates and equivalent point-in-time feature information (plan 9.2).

Sprint 4 owns :mod:`~jev_imrp.models.random_filter` and
:mod:`~jev_imrp.models.rules`; this sprint adds the JEV client and its
deterministic mock.
"""

from __future__ import annotations

from jev_imrp.models.jev_client import (
    CONFIRMATORY_MODEL_ID,
    DEPRECATED_ENDPOINTS,
    ENDPOINT,
    MODEL_ALIASES,
    PRICING_PER_BTOK_USD,
    CallResult,
    Candidate,
    ExecutionMode,
    FailurePolicy,
    JevClient,
    JevContractError,
    LiveConfig,
    LiveJevClient,
    MissingCredentialsError,
    ParsedAnswer,
    ParsedResponse,
    Question,
    RetryPolicy,
    accept_reject_question,
    build_request,
    build_state,
    canonical_hash,
    validate_request,
    validate_response,
)
from jev_imrp.models.jev_mock import MockConfig, MockJevClient

__all__ = [
    "CONFIRMATORY_MODEL_ID",
    "DEPRECATED_ENDPOINTS",
    "ENDPOINT",
    "MODEL_ALIASES",
    "PRICING_PER_BTOK_USD",
    "CallResult",
    "Candidate",
    "ExecutionMode",
    "FailurePolicy",
    "JevClient",
    "JevContractError",
    "LiveConfig",
    "LiveJevClient",
    "MissingCredentialsError",
    "MockConfig",
    "MockJevClient",
    "ParsedAnswer",
    "ParsedResponse",
    "Question",
    "RetryPolicy",
    "accept_reject_question",
    "build_request",
    "build_state",
    "canonical_hash",
    "validate_request",
    "validate_response",
]
