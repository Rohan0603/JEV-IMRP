"""JEV runtime budget boundary tests (ORC-87)."""

from __future__ import annotations

import pytest

from jev_imrp.experiments.attempt_store import AttemptStore
from jev_imrp.experiments.runner import JevRunner
from jev_imrp.models.jev_client import LiveConfig, RetryPolicy, accept_reject_question


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_retries": -1},
        {"base_delay_s": -0.1},
        {"max_delay_s": -0.1},
        {"max_delay_s": 0.1, "base_delay_s": 0.2},
        {"status_codes": (500,)},
        {"status_codes": (429, 500)},
    ],
)
def test_retry_policy_rejects_invalid_budgets(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        RetryPolicy(**kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_concurrency": 0},
        {"max_concurrency": -1},
        {"min_interval_s": -0.1},
        {"timeout_s": 0.0},
    ],
)
def test_live_config_rejects_invalid_runtime_limits(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        LiveConfig(**kwargs)  # type: ignore[arg-type]


def test_runner_rejects_zero_concurrency(tmp_path) -> None:
    with pytest.raises(ValueError):
        JevRunner(
            type("MockClient", (), {"execution_mode": "mocked"})(),
            AttemptStore(tmp_path / "attempts.jsonl"),
            experiment_id="exp-1",
            questions=(accept_reject_question(),),
            model_id="jev-1.13.0",
            max_concurrency=0,
        )


def test_negative_retry_after_uses_configured_backoff() -> None:
    policy = RetryPolicy(base_delay_s=0.5, max_delay_s=2.0)
    assert policy.delay_for(1, retry_after="-1") == 0.5
