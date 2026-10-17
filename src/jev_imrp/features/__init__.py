"""Point-in-time feature pipeline (plan section 7).

One shared computation library for historical replay and live/shadow mode:
:mod:`jev_imrp.features.compute`. Causality and feature-allowlist enforcement
live in :mod:`jev_imrp.features.causal_checks`.
"""

from __future__ import annotations

from jev_imrp.features.causal_checks import (
    CausalViolation,
    FeatureLeakError,
    assert_feature_set,
    assert_no_leaks,
    assert_unchanged,
    changed_features,
)
from jev_imrp.features.compute import (
    FeatureEngine,
    FeatureRecord,
    FeatureValue,
    as_of_frame,
    compute_feature_series,
)
from jev_imrp.features.definitions import (
    FEATURE_UNITS,
    QUALITY_CODES,
    TRAINING_FEATURES,
    FeatureParams,
    feature_versions,
    params_from_config,
)

__all__ = [
    "FEATURE_UNITS",
    "QUALITY_CODES",
    "TRAINING_FEATURES",
    "CausalViolation",
    "FeatureEngine",
    "FeatureLeakError",
    "FeatureParams",
    "FeatureRecord",
    "FeatureValue",
    "as_of_frame",
    "assert_feature_set",
    "assert_no_leaks",
    "assert_unchanged",
    "changed_features",
    "compute_feature_series",
    "feature_versions",
    "params_from_config",
]
