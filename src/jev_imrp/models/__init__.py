"""Baseline selection policies on the shared candidate stream (plan 9.1).

Rungs 1-4 of the model ladder, all implementing the same
:class:`~jev_imrp.models.policy.Policy` interface: rung 1 unfiltered, rung 2
matched random with repeated draws, rung 3 cost-aware deterministic, rung 4 fixed
mechanical rules. Later rungs (linear baseline, LightGBM, JEV) are added by the
model workers and must use this same interface and the same fairness conventions.
"""

from __future__ import annotations

from jev_imrp.models.cost_aware_filter import (
    COST_AWARE_POLICY_VERSION,
    DEFAULT_SAFETY_MARGIN_BPS,
    CostAwarePolicy,
    TrainingEstimates,
)
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
from jev_imrp.models.policy import (
    Decision,
    Policy,
    PolicyDecision,
    PolicyError,
    PolicyResult,
    UnfilteredPolicy,
    apply_policy,
    config_digest,
    register_policy_trial,
)
from jev_imrp.models.linear_model import (
    LINEAR_FEATURES,
    LINEAR_POLICY_VERSION,
    LinearBaselinePolicy,
)
from jev_imrp.models.random_filter import RANDOM_POLICY_VERSION, RandomPolicy
from jev_imrp.models.rules import PRE_REGISTERED_RULES, RULES_VERSION, RulePolicy, RuleSpec

__all__: tuple[str, ...] = (
    "COST_AWARE_POLICY_VERSION",
    "DEFAULT_SAFETY_MARGIN_BPS",
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
    "LINEAR_FEATURES",
    "LINEAR_POLICY_VERSION",
    "LinearBaselinePolicy",
    "PRE_REGISTERED_RULES",
    "RANDOM_POLICY_VERSION",
    "RULES_VERSION",
    "CostAwarePolicy",
    "Decision",
    "Policy",
    "PolicyDecision",
    "PolicyError",
    "PolicyResult",
    "RandomPolicy",
    "RulePolicy",
    "RuleSpec",
    "TrainingEstimates",
    "UnfilteredPolicy",
    "accept_reject_question",
    "build_request",
    "build_state",
    "canonical_hash",
    "config_digest",
    "register_policy_trial",
    "validate_request",
    "validate_response",
)
