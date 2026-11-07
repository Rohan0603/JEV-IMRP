"""Linear baseline policy for binary net profitability classification (plan 9.1).

This policy implements logistic regression for binary net-profitability
classification. It follows the plan 9.2 fairness constraints and shares the
baseline feature set.

Key design decisions:
- Binary classification task: predict `net_return > 0`.
- Logistic regression with fixed hyperparameters: `C=1.0`, `l1_ratio=0.0`
  (L2 penalty), `solver='lbfgs'`, and `max_iter=1000`.
- Preprocessing: Standardize features using `StandardScaler` fitted on training data only.
- Missing data policy: missing or non-finite features are rejected with a
  `missing_feature:<name>` reason.
- Deterministic: Same inputs → same decisions. Set `random_state` on the logistic regression.
- Feature encoding: use numeric `TRAINING_FEATURES` and skip categorical `tod_bucket`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sklearn.linear_model import LogisticRegression  # type: ignore[import-untyped]
from sklearn.pipeline import Pipeline  # type: ignore[import-untyped]
from sklearn.preprocessing import StandardScaler  # type: ignore[import-untyped]

from jev_imrp.candidates.schema import CandidateRecord
from jev_imrp.features.definitions import TRAINING_FEATURES
from jev_imrp.models.policy import Decision, PolicyDecision, PolicyError

LINEAR_POLICY_VERSION = "linear_v1"

#: Numeric features used by the linear baseline. ``tod_bucket`` is categorical
#: and is deliberately excluded so the scaler sees only continuous inputs.
LINEAR_FEATURES: tuple[str, ...] = tuple(name for name in TRAINING_FEATURES if name != "tod_bucket")


def _build_pipeline(
    *,
    C: float,
    l1_ratio: float,
    solver: str,
    max_iter: int,
    random_state: int,
) -> Pipeline:
    """Build a fresh StandardScaler + LogisticRegression pipeline."""
    return Pipeline(
        [
            ("scaler", StandardScaler()),
            (
                "classifier",
                LogisticRegression(
                    C=C,
                    l1_ratio=l1_ratio,
                    solver=solver,
                    max_iter=max_iter,
                    random_state=random_state,
                ),
            ),
        ]
    )


@dataclass(frozen=True, slots=True)
class LinearBaselinePolicy:
    """Logistic regression policy for binary net profitability classification.

    This policy implements a logistic regression model for binary classification
    of net profitability. It adheres to the fairness constraints (plan 9.2) and
    uses the same feature set as the other baselines.
    """

    seed: int = 0
    name: str = "linear_baseline"
    version: str = LINEAR_POLICY_VERSION

    # Fixed hyperparameters
    C: float = 1.0
    l1_ratio: float = 0.0
    solver: str = "lbfgs"
    max_iter: int = 1000
    random_state: int = 0

    # Pipeline: StandardScaler + LogisticRegression. Built in __post_init__
    # from the instance's hyperparameters so custom constructor values are
    # honoured (a default_factory lambda would capture class-level defaults).
    pipeline: Pipeline = field(default_factory=lambda: Pipeline([]))
    _fitted: bool = field(default=False, compare=False, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "pipeline",
            _build_pipeline(
                C=self.C,
                l1_ratio=self.l1_ratio,
                solver=self.solver,
                max_iter=self.max_iter,
                random_state=self.random_state,
            ),
        )

    def config(self) -> dict[str, Any]:
        """Return the configuration for the trial ledger."""
        return {
            "C": self.C,
            "l1_ratio": self.l1_ratio,
            "solver": self.solver,
            "max_iter": self.max_iter,
            "random_state": self.random_state,
            "version": self.version,
        }

    def fit(self, X: Sequence[Mapping[str, Any]], y: Sequence[bool]) -> None:
        """Fit the model on training data.

        Args:
            X: List of feature dictionaries for each candidate.
            y: List of binary labels (1 for net_return > 0, 0 otherwise).
        """
        import numpy as np

        rows: list[list[float]] = []
        for candidate in X:
            row: list[float] = []
            for feature in LINEAR_FEATURES:
                value = candidate.get(feature)
                if value is None:
                    raise PolicyError(f"missing_feature:{feature}")
                row.append(float(value))
            rows.append(row)
        X_array = np.array(rows)
        y_array = np.array(y)
        self.pipeline.fit(X_array, y_array)
        object.__setattr__(self, "_fitted", True)

    def _prepare_features(self, candidate: CandidateRecord) -> list[float]:
        """Prepare features for a candidate, handling missing values.

        Args:
            candidate: CandidateRecord to process.

        Returns:
            List of numeric features, skipping ``tod_bucket``.

        Raises:
            PolicyError: If a required feature is missing or non-finite.
        """
        features = []
        for feature in LINEAR_FEATURES:
            value = candidate.feature(feature)
            if value is None:
                raise PolicyError(f"missing_feature:{feature}")
            try:
                numeric_value = float(value)
                if not numeric_value == numeric_value:  # NaN check
                    raise PolicyError(f"invalid_feature:{feature}")
                if not (numeric_value >= -1e6 and numeric_value <= 1e6):
                    raise PolicyError(f"invalid_feature:{feature}")
                features.append(numeric_value)
            except (TypeError, ValueError) as exc:
                raise PolicyError(f"invalid_feature:{feature}") from exc
        return features

    def decide(self, candidate: CandidateRecord) -> PolicyDecision:
        """Make a decision for a single candidate.

        Args:
            candidate: CandidateRecord to evaluate.

        Returns:
            PolicyDecision with ACCEPT/REJECT and score.
        """
        if not self._fitted:
            return PolicyDecision(
                signal_id=candidate.signal_id,
                decision=Decision.REJECT,
                reason="model_not_fitted",
            )
        try:
            features = self._prepare_features(candidate)
        except PolicyError as exc:
            return PolicyDecision(
                signal_id=candidate.signal_id,
                decision=Decision.REJECT,
                reason=str(exc),
            )
        import numpy as np

        features_array = np.array(features, dtype=float).reshape(1, -1)
        score = float(self.pipeline.predict_proba(features_array)[0, 1])
        decision = Decision.ACCEPT if score >= 0.5 else Decision.REJECT
        return PolicyDecision(
            signal_id=candidate.signal_id,
            decision=decision,
            reason=f"linear_score_{decision.lower()}",
            score=score,
        )

    def decide_batch(self, candidates: Sequence[CandidateRecord]) -> tuple[PolicyDecision, ...]:
        """Make decisions for a batch of candidates.

        Args:
            candidates: List of CandidateRecord to evaluate.

        Returns:
            Tuple of PolicyDecision for each candidate.
        """
        return tuple(self.decide(candidate) for candidate in candidates)


__all__ = ["LINEAR_FEATURES", "LINEAR_POLICY_VERSION", "LinearBaselinePolicy"]
