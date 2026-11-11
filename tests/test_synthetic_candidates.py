"""Synthetic candidate fixture contract tests (ORC-76)."""

from __future__ import annotations

import pytest

from jev_imrp.candidates.synthetic import SyntheticGeneratorConfig


@pytest.mark.parametrize(
    "kwargs",
    [
        {"candidate_probability": -0.01},
        {"candidate_probability": 1.01},
        {"horizon_minutes": 0},
        {"max_candidates_per_symbol_session": 0},
        {"max_spread_bps_for_eligible": -0.01},
    ],
)
def test_synthetic_config_rejects_invalid_coverage_controls(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        SyntheticGeneratorConfig(seed=7, **kwargs)  # type: ignore[arg-type]
