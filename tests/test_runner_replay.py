import asyncio
from pathlib import Path

from jev_imrp.experiments.attempt_store import AttemptStore
from jev_imrp.experiments.runner import JevRunner
from jev_imrp.models.jev_client import CallResult, Candidate, Question


class CountingClient:
    execution_mode = "mocked"
    model_id = "jev-1.13.0"

    def __init__(self) -> None:
        self.calls = 0

    async def answer(self, request, *, questions, attempt_number=0):
        self.calls += 1
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


def test_restart_replays_terminal_attempt_without_recalling_client(tmp_path: Path) -> None:
    asyncio.run(_assert_restart_replays_terminal_attempt_without_recalling_client(tmp_path))


async def _assert_restart_replays_terminal_attempt_without_recalling_client(tmp_path: Path) -> None:
    client = CountingClient()
    store = AttemptStore(tmp_path / "attempts.jsonl")
    kwargs = dict(
        client=client,
        store=store,
        experiment_id="exp-replay",
        questions=(Question(id="q", type="box", instructions="classify"),),
        model_id="jev-1.13.0",
        confirmatory=True,
    )
    candidate = Candidate(
        candidate_id="candidate-1",
        symbol="SYNTH",
        side="BUY",
        decision_timestamp_utc="2026-01-01T00:00:00+00:00",
        horizon_minutes=5,
        features={},
    )

    first = await JevRunner(**kwargs).run((candidate,))
    second = await JevRunner(**kwargs).run((candidate,))

    assert first.decision_for(candidate.candidate_id).terminal == "accept"
    assert second.decision_for(candidate.candidate_id).terminal == "accept"
    assert client.calls == 1
    assert len(store.attempts()) == 1


def test_summary_excludes_other_experiments(tmp_path: Path) -> None:
    asyncio.run(_assert_summary_excludes_other_experiments(tmp_path))


async def _assert_summary_excludes_other_experiments(tmp_path: Path) -> None:
    store = AttemptStore(tmp_path / "attempts.jsonl")
    client = CountingClient()
    candidate = Candidate(
        candidate_id="candidate-1",
        symbol="SYNTH",
        side="BUY",
        decision_timestamp_utc="2026-01-01T00:00:00+00:00",
        horizon_minutes=5,
        features={},
    )
    common = dict(
        client=client,
        store=store,
        questions=(Question(id="q", type="box", instructions="classify"),),
        model_id="jev-1.13.0",
        confirmatory=True,
    )
    await JevRunner(experiment_id="other", **common).run((candidate,))
    result = await JevRunner(experiment_id="current", **common).run((candidate,))

    assert result.coverage["attempts_total"] == 1
    assert result.coverage["failure_reasons"] == {}
