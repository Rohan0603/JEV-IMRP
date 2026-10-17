"""Validation framework: leakage-safe splits, label-interval purge, holdout guard.

Plan sections 10.1 (split by session/time, purge by label interval, embargo in
real time) and 10.2 (locked holdout). Nothing in this package ever decides a
result; it only decides which observations a fit may see.
"""

from __future__ import annotations

from jev_imrp.validation.holdout import (
    HoldoutAccessError,
    HoldoutError,
    HoldoutGuard,
    HoldoutLease,
    HoldoutOpening,
    HoldoutProtocolViolation,
    assert_holdout_not_fitted,
)
from jev_imrp.validation.purge import (
    PurgeConfig,
    PurgedRow,
    PurgeError,
    PurgeReason,
    PurgeResult,
    purge_rows,
    purge_training_rows,
)
from jev_imrp.validation.temporal_splits import (
    DatasetBoundaries,
    ExclusionRow,
    Fold,
    Partition,
    SessionSplitter,
    SplitError,
    SplitLeakError,
    SplitManifest,
    assert_fit_partition,
    audit_split,
    build_split_manifest,
    make_session_partitions,
    walk_forward_folds,
)

__all__ = [
    "DatasetBoundaries",
    "ExclusionRow",
    "Fold",
    "HoldoutAccessError",
    "HoldoutError",
    "HoldoutGuard",
    "HoldoutLease",
    "HoldoutOpening",
    "HoldoutProtocolViolation",
    "Partition",
    "PurgeConfig",
    "PurgeError",
    "PurgeReason",
    "PurgeResult",
    "PurgedRow",
    "SessionSplitter",
    "SplitError",
    "SplitLeakError",
    "SplitManifest",
    "assert_fit_partition",
    "assert_holdout_not_fitted",
    "audit_split",
    "build_split_manifest",
    "make_session_partitions",
    "purge_rows",
    "purge_training_rows",
    "walk_forward_folds",
]
