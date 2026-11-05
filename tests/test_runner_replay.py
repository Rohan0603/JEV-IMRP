import asyncio
from pathlib import Path

from jev_imrp.experiments.attempt_store import AttemptStore
from jev_imrp.experiments.runner import JevRunner
from jev_imrp.models.jev_client import CallResult, Candidate, JevClient, Question


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


class LiveCountingClient(CountingClient):
    execution_mode = "live"


def _candidate(candidate_id: str) -> Candidate:
    return Candidate(
        candidate_id=candidate_id,
        symbol="SYNTH",
        side="BUY",
        decision_timestamp_utc="2026-01-01T00:00:00+00:00",
        horizon_minutes=5,
        features={},
    )


def _runner(client: JevClient, store: AttemptStore, *, experiment_id: str) -> JevRunner:
    return JevRunner(
        client=client,
        store=store,
        experiment_id=experiment_id,
        questions=(Question(id="q", type="box", instructions="classify"),),
        model_id="jev-1.13.0",
        confirmatory=True,
    )


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


def test_execution_modes_have_distinct_attempt_records(tmp_path: Path) -> None:
    asyncio.run(_assert_execution_modes_have_distinct_attempt_records(tmp_path))


async def _assert_execution_modes_have_distinct_attempt_records(tmp_path: Path) -> None:
    store = AttemptStore(tmp_path / "attempts.jsonl")
    candidate = _candidate("candidate-1")
    mocked = CountingClient()
    live = LiveCountingClient()

    mocked_result = await _runner(mocked, store, experiment_id="exp-modes").run((candidate,))
    live_result = await _runner(live, store, experiment_id="exp-modes").run((candidate,))

    assert mocked_result.coverage["attempts_total"] == 1
    assert live_result.coverage["attempts_total"] == 1
    assert mocked.calls == 1
    assert live.calls == 1
    assert len(store.attempts()) == 2
    assert {attempt.execution_mode for attempt in store.attempts()} == {"mocked", "live"}


def test_summary_excludes_stale_candidates(tmp_path: Path) -> None:
    asyncio.run(_assert_summary_excludes_stale_candidates(tmp_path))


async def _assert_summary_excludes_stale_candidates(tmp_path: Path) -> None:
    store = AttemptStore(tmp_path / "attempts.jsonl")
    client = CountingClient()
    runner = _runner(client, store, experiment_id="exp-candidates")

    await runner.run((_candidate("stale"),))
    result = await runner.run((_candidate("current"),))

    assert result.coverage["candidates_total"] == 1
    assert result.coverage["attempts_total"] == 1


def test_raw_responses_are_unique_per_attempt(tmp_path: Path) -> None:
    asyncio.run(_assert_raw_responses_are_unique_per_attempt(tmp_path))


async def _assert_raw_responses_are_unique_per_attempt(tmp_path: Path) -> None:
    class RetryingClient(CountingClient):
        async def answer(self, request, *, questions, attempt_number=0):
            self.calls += 1
            return CallResult(
                payload={
                    "attempt": attempt_number,
                    "model": self.model_id,
                    "answers": {}
                    if attempt_number == 0
                    else {"q": {"value": "accept", "confidence": 0.9}},
                    "usage": {"input_tokens": 2, "output_tokens": 1},
                },
                returned_model_id=self.model_id,
                status=529 if attempt_number == 0 else 200,
            )

    store = AttemptStore(tmp_path / "attempts.jsonl")
    client = RetryingClient()
    result = await JevRunner(
        client=client,
        store=store,
        experiment_id="exp-raw",
        questions=(Question(id="q", type="box", instructions="classify"),),
        model_id="jev-1.13.0",
        confirmatory=True,
        raw_response_dir=str(tmp_path / "raw"),
    ).run((_candidate("candidate-1"),))

    assert result.decision_for("candidate-1").terminal == "accept"
    attempts = store.attempts()
    assert len(attempts) == 2
    assert attempts[0].raw_response_ref != attempts[1].raw_response_ref
    assert Path(attempts[0].raw_response_ref).read_text(encoding="utf-8").find('"attempt": 0') >= 0
    assert Path(attempts[1].raw_response_ref).read_text(encoding="utf-8").find('"attempt": 1') >= 0


def test_unexpected_client_exception_is_durably_recorded(tmp_path: Path) -> None:
    asyncio.run(_assert_unexpected_client_exception_is_durably_recorded(tmp_path))


async def _assert_unexpected_client_exception_is_durably_recorded(tmp_path: Path) -> None:
    class BrokenClient(CountingClient):
        async def answer(self, request, *, questions, attempt_number=0):
            self.calls += 1
            raise RuntimeError("transport exploded")

    store = AttemptStore(tmp_path / "attempts.jsonl")
    result = await _runner(BrokenClient(), store, experiment_id="exp-error").run(
        (_candidate("candidate-1"),)
    )

    assert result.decision_for("candidate-1").terminal == "reject"
    attempts = store.attempts()
    assert len(attempts) == 1
    assert attempts[0].failure_reason == "client_error"
