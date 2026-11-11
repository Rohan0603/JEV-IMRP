"""Offline ORC-87 failure-injection and bounded-runner coverage."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jev_imrp.experiments.attempt_store import AttemptStore
from jev_imrp.experiments.runner import JevRunner
from jev_imrp.models.jev_client import (
    CONFIRMATORY_MODEL_ID,
    Candidate,
    RetryPolicy,
    accept_reject_question,
)
from jev_imrp.models.jev_mock import MockConfig, MockJevClient


class TrackingMockClient(MockJevClient):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.active = 0
        self.max_active = 0

    async def answer(self, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            await asyncio.sleep(0.001)
            return await super().answer(*args, **kwargs)
        finally:
            self.active -= 1


def candidate(candidate_id: str) -> Candidate:
    return Candidate(
        candidate_id=candidate_id,
        symbol="SYNTHETIC",
        side="BUY",
        decision_timestamp_utc="2026-01-01T00:00:00+00:00",
        horizon_minutes=15,
        features={},
        feature_set_version="synthetic_v1",
    )


def runner(tmp_path: Path, client: MockJevClient, **kwargs: object) -> JevRunner:
    return JevRunner(
        client,
        AttemptStore(tmp_path / "attempts.jsonl"),
        experiment_id="offline-orc-87",
        questions=(accept_reject_question(),),
        model_id=CONFIRMATORY_MODEL_ID,
        retry=RetryPolicy(max_retries=2, base_delay_s=0, max_delay_s=1),
        **kwargs,
    )


def test_retryable_statuses_retry_with_bounded_attempt_persistence(tmp_path: Path) -> None:
    client = MockJevClient(
        MockConfig(steps={"c": ("429", "529", "ok")})
    )
    result = asyncio.run(runner(tmp_path, client).run((candidate("c"),)))

    attempts = AttemptStore(tmp_path / "attempts.jsonl").attempts()
    assert [attempt.http_status for attempt in attempts] == [429, 529, 200]
    assert [attempt.retry_count for attempt in attempts] == [0, 1, 2]
    assert result.coverage["attempts_total"] == 3
    assert result.decision_for("c") is not None
    assert result.cost_report.attempts_counted == 3
    assert all(attempt.latency_ms is not None for attempt in attempts)


@pytest.mark.parametrize("step", ["500", "404", "timeout"])
def test_non_retryable_and_timeout_failures_are_recorded_once(
    tmp_path: Path, step: str
) -> None:
    client = MockJevClient(MockConfig(steps={"c": (step,)}))
    result = asyncio.run(runner(tmp_path, client).run((candidate("c"),)))

    attempts = AttemptStore(tmp_path / "attempts.jsonl").attempts()
    assert len(attempts) == 1
    assert attempts[0].failure_reason in {"http_error", "timeout"}
    assert result.coverage["attempts_total"] == 1
    assert result.coverage["candidates_failed"] == 1


def test_concurrency_is_bounded_and_zero_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="max_concurrency"):
        runner(tmp_path, MockJevClient(), max_concurrency=0)

    candidates = tuple(candidate(str(index)) for index in range(8))
    client = TrackingMockClient()
    result = asyncio.run(runner(tmp_path, client, max_concurrency=2).run(candidates))
    assert result.coverage["candidates_answered"] == len(candidates)
    assert client.max_active == 2


def test_retry_policy_rejects_hidden_statuses_and_negative_retry_after() -> None:
    with pytest.raises(ValueError, match="status codes"):
        RetryPolicy(status_codes=(500,))
    with pytest.raises(ValueError, match="max_retries"):
        RetryPolicy(max_retries=-1)
    assert RetryPolicy(max_delay_s=2).delay_for(1, "-10") == 0.5
