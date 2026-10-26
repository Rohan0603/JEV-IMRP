"""Linear baseline policy for binary net profitability classification (plan 9.1).

This policy implements a logistic regression model for binary classification of net profitability. It adheres to the fairness constraints (plan 9.2) and uses the same feature set as the other baselines.

Key design decisions:
- Binary classification task: predict `net_return > 0`.
- Logistic regression with fixed hyperparameters: `C=1.0`, `penalty='l2'`, `solver='lbfgs'`, `max_iter=1000`.
- Preprocessing: Standardize features using `StandardScaler` fitted on training data only.
- Missing data policy: If a required feature is missing or non-finite, REJECT with `missing_feature:<name>` reason.
- Deterministic: Same inputs → same decisions. Set `random_state` on the logistic regression.
- Feature encoding: Use only numeric features from `TRAINING_FEATURES`, skipping `tod_bucket` (categorical).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from jev_imrp.candidates.schema import CandidateRecord
from jev_imrp.models.policy import Decision, Policy, PolicyDecision, PolicyError
from jev_imrp.features.definitions import TRAINING_FEATURES

LINEAR_POLICY_VERSION = "linear_v1"


@dataclass(frozen=True, slots=True)
class LinearBaselinePolicy:
    """Logistic regression policy for binary net profitability classification.

    This policy implements a logistic regression model for binary classification of net profitability. It adheres to the fairness constraints (plan 9.2) and uses the same feature set as the other baselines.
    """
    seed: int = 0
    name: str = "linear_baseline"
    version: str = LINEAR_POLICY_VERSION

    # Fixed hyperparameters
    C: float = 1.0
    penalty: str = "l2"
    solver: str = "lbfgs"
    max_iter: int = 1000
    random_state: int = 0

    # Pipeline: StandardScaler + LogisticRegression
    pipeline: Pipeline = field(default_factory=lambda: Pipeline([
        ('scaler', StandardScaler()),
        ('classifier', LogisticRegression(
            C=C,
            penalty=penalty,
            solver=solver,
            max_iter=max_iter,
            random_state=random_state,
        )),
    ]))

    def config(self) -> dict[str, Any]:
        """Return the configuration for the trial ledger.
        """
        return {
            "C": self.C,
            "penalty": self.penalty,
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
        # Convert to numpy arrays for sklearn
        import numpy as np
        X_array = np.array([
            [c.get(feature) for feature in TRAINING_FEATURES if feature != "tod_bucket"]
            for c in X
        ])
        y_array = np.array(y)

        # Fit the pipeline
        self.pipeline.fit(X_array, y_array)

    def _prepare_features(self, candidate: CandidateRecord) -> list[float]:
        """Prepare features for a candidate, handling missing values.

        Args:
            candidate: CandidateRecord to process.

        Returns:
            List of numeric features, skipping `tod_bucket`.

        Raises:
            PolicyError: If a required feature is missing or non-finite.
        """
        features = []
        for feature in TRAINING_FEATURES:
            if feature == "tod_bucket":
                continue
            value = candidate.feature(feature)
            if value is None:
                raise PolicyError(f"missing_feature:{feature}")
            try:
                numeric_value = float(value)
                if not (numeric_value >= -1e6 and numeric_value <= 1e6):
                    raise PolicyError(f"invalid_feature:{feature}")
                features.append(numeric_value)
            except (TypeError, ValueError):
                raise PolicyError(f"invalid_feature:{feature}")
        return features

    def decide(self, candidate: CandidateRecord) -> PolicyDecision:
        """Make a decision for a single candidate.

        Args:
            candidate: CandidateRecord to evaluate.

        Returns:
            PolicyDecision with ACCEPT/REJECT and score.
        """
        try:
            features = self._prepare_features(candidate)
            import numpy as np
            features_array = np.array(features).reshape(1, -1)
            score = self.pipeline.predict_proba(features_array)[0, 1]
            decision = Decision.ACCEPT if score >= 0.5 else Decision.REJECT
            return PolicyDecision(
                signal_id=candidate.signal_id,
                decision=decision,
                reason=f"linear_score_{decision.lower()}",
                score=score,
            )
        except PolicyError as e:
            return PolicyDecision(
                signal_id=candidate.signal_id,
                decision=Decision.REJECT,
                reason=str(e),
            )

    def decide_batch(self, candidates: Sequence[CandidateRecord]) -> tuple[PolicyDecision, ...]:
        """Make decisions for a batch of candidates.

        Args:
            candidates: List of CandidateRecord to evaluate.

        Returns:
            Tuple of PolicyDecision for each candidate.
        """
        decisions = []
        for candidate in candidates:
            decisions.append(self.decide(candidate))
        return tuple(decisions)


__all__ = ["LINEAR_POLICY_VERSION", "LinearBaselinePolicy"]