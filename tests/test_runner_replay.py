import asyncio
import json
from pathlib import Path

import pytest

from jev_imrp.experiments.attempt_store import AttemptStore
from jev_imrp.experiments.runner import JevRunner
from jev_imrp.models.jev_client import CallResult, Candidate, JevClient, Question, RetryPolicy
from jev_imrp.models.jev_mock import MockConfig, MockJevClient


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


def _runner(
    client: JevClient,
    store: AttemptStore,
    *,
    experiment_id: str,
    raw_response_dir: str | None = None,
) -> JevRunner:
    return JevRunner(
        client=client,
        store=store,
        experiment_id=experiment_id,
        questions=(Question(id="q", type="box", instructions="classify"),),
        model_id="jev-1.13.0",
        confirmatory=True,
        raw_response_dir=raw_response_dir,
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


def test_replay_fails_closed_when_request_contract_changes(tmp_path: Path) -> None:
    asyncio.run(_assert_replay_fails_closed_when_request_contract_changes(tmp_path))


async def _assert_replay_fails_closed_when_request_contract_changes(tmp_path: Path) -> None:
    client = CountingClient()
    store = AttemptStore(tmp_path / "attempts.jsonl")
    candidate = _candidate("candidate-1")
    questions = (Question(id="q", type="box", instructions="classify"),)
    first = await JevRunner(
        client=client,
        store=store,
        experiment_id="exp-contract",
        questions=questions,
        model_id="jev-1.13.0",
        confirmatory=True,
    ).run((candidate,))

    changed = Candidate(
        candidate_id=candidate.candidate_id,
        symbol=candidate.symbol,
        side=candidate.side,
        decision_timestamp_utc=candidate.decision_timestamp_utc,
        horizon_minutes=candidate.horizon_minutes,
        features={"new_feature": 1},
    )
    second = await JevRunner(
        client=client,
        store=store,
        experiment_id="exp-contract",
        questions=questions,
        model_id="jev-1.13.0",
        failure_policy="reject",
        retry=RetryPolicy(max_retries=0),
        confirmatory=True,
    ).run((changed,))

    assert first.decision_for(candidate.candidate_id).terminal == "accept"
    decision = second.decision_for(candidate.candidate_id)
    assert decision.terminal == "reject"
    assert decision.failure_reason == "replay_contract_mismatch"
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


def test_raw_response_publication_is_no_clobber_for_same_attempt(tmp_path: Path) -> None:
    runner = _runner(
        CountingClient(),
        AttemptStore(tmp_path / "attempts.jsonl"),
        experiment_id="exp-no-clobber",
        raw_response_dir=str(tmp_path / "raw"),
    )
    candidate = _candidate("candidate-1")

    first = runner._store_raw({"attempt": 0, "value": "first"}, candidate, 0)
    second = runner._store_raw({"attempt": 0, "value": "second"}, candidate, 0)

    assert first == second
    assert first is not None
    assert json.loads(Path(first).read_text(encoding="utf-8"))["value"] == "first"


def test_raw_response_reference_is_safe_for_path_like_identifiers(tmp_path: Path) -> None:
    runner = _runner(
        CountingClient(),
        AttemptStore(tmp_path / "attempts.jsonl"),
        experiment_id="exp/..\\outside",
        raw_response_dir=str(tmp_path / "raw"),
    )
    candidate = _candidate("candidate/..\\outside")

    reference = runner._store_raw({"value": "safe"}, candidate, 0)

    assert reference is not None
    reference_path = Path(reference).resolve()
    raw_root = (tmp_path / "raw").resolve()
    assert reference_path.is_relative_to(raw_root)
    assert len(reference_path.stem) == 68
    assert reference_path.suffix == ".json"


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


@pytest.mark.parametrize("status_step", ["429", "529"])
def test_retryable_http_failures_are_reconciled_per_attempt(
    tmp_path: Path, status_step: str
) -> None:
    asyncio.run(_assert_retryable_http_failures_are_reconciled_per_attempt(tmp_path, status_step))


async def _assert_retryable_http_failures_are_reconciled_per_attempt(
    tmp_path: Path, status_step: str
) -> None:
    candidate = _candidate("c")
    store = AttemptStore(tmp_path / "attempts.jsonl")
    client = MockJevClient(MockConfig(steps={"c": (status_step, "ok")}))
    result = await _runner(client, store, experiment_id=f"exp-{status_step}").run((candidate,))

    assert result.decision_for("c").terminal == "accept"
    assert result.coverage["attempts_total"] == 2
    assert result.coverage["candidates_attempted"] == 1
    assert result.coverage["failure_reasons"] == {"http_error": 1}
    assert client.calls == ["c", "c"]


@pytest.mark.parametrize(
    "step,expected_reason",
    [
        ("500", "http_error"),
        ("timeout", "timeout"),
        ("malformed", "malformed_response"),
        ("model_mismatch", "model_mismatch"),
    ],
)
def test_non_retryable_failures_are_durable(
    tmp_path: Path, step: str, expected_reason: str
) -> None:
    asyncio.run(_assert_non_retryable_failures_are_durable(tmp_path, step, expected_reason))


async def _assert_non_retryable_failures_are_durable(
    tmp_path: Path, step: str, expected_reason: str
) -> None:
    store = AttemptStore(tmp_path / "attempts.jsonl")
    client = MockJevClient(MockConfig(steps={"c": (step,)}))
    result = await _runner(client, store, experiment_id=f"exp-{step}").run((_candidate("c"),))

    assert result.decision_for("c").terminal == "reject"
    assert result.coverage["attempts_total"] == 1
    assert result.coverage["failure_reasons"] == {expected_reason: 1}
    assert client.calls == ["c"]


@pytest.mark.parametrize(
    "policy,expected_terminal,expected_source",
    [("reject", "reject", "failure_policy"), ("skip", "skip", "failure_policy")],
)
def test_failure_policies_reconcile_failed_candidates(
    tmp_path: Path, policy: str, expected_terminal: str, expected_source: str
) -> None:
    asyncio.run(
        _assert_failure_policies_reconcile_failed_candidates(
            tmp_path, policy, expected_terminal, expected_source
        )
    )


async def _assert_failure_policies_reconcile_failed_candidates(
    tmp_path: Path, policy: str, expected_terminal: str, expected_source: str
) -> None:
    store = AttemptStore(tmp_path / "attempts.jsonl")
    client = MockJevClient(MockConfig(steps={"c": ("500",)}))
    runner = _runner(client, store, experiment_id=f"exp-policy-{policy}")
    runner.failure_policy = policy
    result = await runner.run((_candidate("c"),))

    decision = result.decision_for("c")
    assert decision.terminal == expected_terminal
    assert decision.source == expected_source
    assert result.coverage["candidates_attempted"] == 1
    assert result.coverage["attempts_total"] == 1


def test_fallback_policy_is_deterministic_in_multi_candidate_run(tmp_path: Path) -> None:
    asyncio.run(_assert_fallback_policy_is_deterministic_in_multi_candidate_run(tmp_path))


async def _assert_fallback_policy_is_deterministic_in_multi_candidate_run(tmp_path: Path) -> None:
    candidates = tuple(_candidate(candidate_id) for candidate_id in ("accept", "reject"))
    store = AttemptStore(tmp_path / "attempts.jsonl")
    client = MockJevClient(MockConfig(default_steps=("timeout",)))
    runner = JevRunner(
        client=client,
        store=store,
        experiment_id="exp-fallback",
        questions=(Question(id="q", type="box", instructions="classify"),),
        model_id="jev-1.13.0",
        failure_policy="fallback",
        fallback_decider=lambda candidate: candidate.candidate_id == "accept",
        confirmatory=True,
    )
    result = await runner.run(candidates)

    assert [result.decision_for(c.candidate_id).terminal for c in candidates] == [
        "accept",
        "reject",
    ]
    assert result.coverage["candidates_total"] == 2
    assert result.coverage["candidates_attempted"] == 2
    assert result.coverage["attempts_total"] == 2
    assert result.coverage["failure_reasons"] == {"timeout": 2}
