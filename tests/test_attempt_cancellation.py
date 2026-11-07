import asyncio
from pathlib import Path

import pytest

from jev_imrp.experiments.attempt_store import AttemptStore
from jev_imrp.experiments.runner import JevRunner
from jev_imrp.models.jev_client import CallResult, Candidate, Question, RetryPolicy, attempt_id_for


class BlockingClient:
    execution_mode = "mocked"
    model_id = "jev-1.13.0"

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def answer(
        self,
        request: dict[str, object],
        *,
        questions: tuple[Question, ...],
        attempt_number: int = 0,
    ) -> CallResult:
        self.started.set()
        await self.release.wait()
        return CallResult(failure_reason="client_error")

    async def aclose(self) -> None:
        return None


def test_cancelled_inflight_call_is_durably_recorded(tmp_path: Path) -> None:
    asyncio.run(_assert_cancelled_inflight_call_is_durably_recorded(tmp_path))


async def _assert_cancelled_inflight_call_is_durably_recorded(tmp_path: Path) -> None:
    client = BlockingClient()
    store = AttemptStore(tmp_path / "attempts.jsonl")
    runner = JevRunner(
        client,
        store,
        experiment_id="exp-cancel",
        questions=(Question(id="q", type="box", instructions="classify"),),
        model_id="jev-1.13.0",
        confirmatory=True,
    )

    task = asyncio.create_task(
        runner.run(
            (
                Candidate(
                    candidate_id="candidate-1",
                    symbol="SYNTH",
                    side="BUY",
                    decision_timestamp_utc="2026-01-01T00:00:00+00:00",
                    horizon_minutes=5,
                    features={},
                ),
            )
        )
    )
    await client.started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    attempts = store.attempts()
    assert len(attempts) == 1
    assert attempts[0].candidate_id == "candidate-1"
    assert attempts[0].failure_reason == "cancelled"
    assert attempts[0].attempt_id == attempt_id_for("exp-cancel", "candidate-1", 0, "mocked")


def test_cancelled_attempt_gets_one_explicit_recovery_with_zero_retry_budget(
    tmp_path: Path,
) -> None:
    asyncio.run(_assert_cancelled_attempt_gets_one_explicit_recovery_with_zero_retry_budget(tmp_path))


async def _assert_cancelled_attempt_gets_one_explicit_recovery_with_zero_retry_budget(
    tmp_path: Path,
) -> None:
    client = BlockingClient()
    store = AttemptStore(tmp_path / "attempts.jsonl")
    candidate = Candidate(
        candidate_id="candidate-1",
        symbol="SYNTH",
        side="BUY",
        decision_timestamp_utc="2026-01-01T00:00:00+00:00",
        horizon_minutes=5,
        features={},
    )
    kwargs = dict(
        store=store,
        experiment_id="exp-cancel-recovery",
        questions=(Question(id="q", type="box", instructions="classify"),),
        model_id="jev-1.13.0",
        retry=RetryPolicy(max_retries=0),
        confirmatory=True,
    )
    task = asyncio.create_task(JevRunner(client, **kwargs).run((candidate,)))
    await client.started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    class RecoveryClient:
        execution_mode = "mocked"
        model_id = "jev-1.13.0"

        def __init__(self) -> None:
            self.attempt_numbers: list[int] = []

        async def answer(self, request, *, questions, attempt_number=0):
            self.attempt_numbers.append(attempt_number)
            return CallResult(
                payload={
                    "model": self.model_id,
                    "answers": {"q": {"value": "accept", "confidence": 0.9}},
                    "usage": {"input_tokens": 2, "output_tokens": 1},
                },
                returned_model_id=self.model_id,
                status=200,
            )

        async def aclose(self) -> None:
            return None

    recovery = RecoveryClient()
    result = await JevRunner(recovery, **kwargs).run((candidate,))

    assert result.decision_for(candidate.candidate_id).terminal == "accept"
    assert recovery.attempt_numbers == [1]
    assert [attempt.retry_count for attempt in store.attempts()] == [0, 1]
