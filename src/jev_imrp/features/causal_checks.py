"""Causality helpers (plan 7.5) and the training feature allowlist (plan 7.4).

The checks live in the library, not only in the test suite, so that a replay job,
a shadow job and CI all enforce the same invariants.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from jev_imrp.common.errors import JevError
from jev_imrp.features.compute import FeatureRecord
from jev_imrp.features.definitions import LEAK_PATTERNS, TRAINING_FEATURES


class CausalViolation(JevError):
    """A causality invariant was violated."""


class FeatureLeakError(CausalViolation):
    """A non-allowlisted or plainly future-looking column reached model inputs."""


def changed_features(before: FeatureRecord, after: FeatureRecord) -> list[str]:
    """Training features whose value or validity differs between two records."""
    changed: list[str] = []
    for name in TRAINING_FEATURES:
        old, new = before.features[name], after.features[name]
        if old.value != new.value or old.valid != new.valid:
            changed.append(name)
    return changed


def assert_unchanged(before: FeatureRecord, after: FeatureRecord, context: str = "") -> None:
    """Fail if recomputing with more data changed an earlier feature record."""
    changed = changed_features(before, after)
    if changed:
        where = f" ({context})" if context else ""
        raise CausalViolation(
            "future data changed previously computed features"
            f"{where}: {', '.join(changed)}. A feature read an observation that was "
            "not available at its as_of timestamp."
        )


def assert_feature_set(records: Sequence[FeatureRecord]) -> None:
    """Fail unless every record carries exactly the allowlisted feature set."""
    expected = set(TRAINING_FEATURES)
    for record in records:
        if set(record.features) != expected:
            unexpected = sorted(set(record.features) - expected)
            absent = sorted(expected - set(record.features))
            raise CausalViolation(
                f"feature set mismatch for {record.symbol} @ {record.as_of_timestamp_utc}: "
                f"unexpected={unexpected} missing={absent}"
            )


def leaked_columns(names: Iterable[str]) -> list[str]:
    """Names that must never be model inputs: outside the allowlist, or leaking."""
    return [
        name
        for name in names
        if name not in TRAINING_FEATURES
        or any(pattern.search(name.lower()) for pattern in LEAK_PATTERNS)
    ]


def assert_no_leaks(names: Iterable[str], context: str = "training feature allowlist") -> None:
    """Reject any leaked or non-allowlisted column handed to a model."""
    leaked = leaked_columns(names)
    if leaked:
        raise FeatureLeakError(
            f"{context}: rejected column(s) {sorted(leaked)}. Only "
            f"{list(TRAINING_FEATURES)} are permitted."
        )
