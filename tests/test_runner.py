"""Durable JEV runner reconciliation tests (plan 9.4)."""

from __future__ import annotations

import asyncio
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from jev_imrp.experiments.attempt_store import AttemptStore
from jev_imrp.experiments.runner import Decision, JevRunner
from jev_imrp.models.jev_client import (
    Attempt,
    CallResult,
    Candidate,
    Question,
    RetryPolicy,
    accept_reject_question,
    attempt_id_for,
)


class _CountingClient:
    execution_mode = "mocked"
    model_id = "jev-1.13.0"

    def __init__(self) -> None:
        self.calls = 0

    async def answer(self, request, *, questions, attempt_number=0):
        self.calls += 1
        return CallResult(
            status=200,
            returned_model_id=self.model_id,
            payload={
                "model": self.model_id,
                "answers": {"q": {"box": "ok"}},
                "usage": {"input_tokens": 1, "output_tokens": 0},
            },
        )


def _candidate(candidate_id: str = "candidate-1", value: float = 1.0) -> Candidate:
    return Candidate(
        candidate_id=candidate_id,
        symbol="SYNTH",
        side="BUY",
        decision_timestamp_utc="2026-01-01T00:00:00+00:00",
        horizon_minutes=5,
        features={"spread_bps": value},
    )


def _append_attempt_in_child(path: str) -> bool:
    return AttemptStore(path).append(
        stored_attempt(experiment_id="current", attempt_id="v2-process-shared-a00")
    )


def stored_attempt(
    *,
    experiment_id: str,
    attempt_id: str,
    candidate_id: str = "candidate-1",
    execution_mode: str = "mocked",
) -> Attempt:
    return Attempt(
        attempt_id=attempt_id,
        experiment_id=experiment_id,
        candidate_id=candidate_id,
        execution_mode=execution_mode,  # type: ignore[arg-type]
        requested_model_id="jev-1.13.0",
        payload_hash="payload-hash",
        prompt_version="prompt-v1",
        prompt_hash="prompt-hash",
        question_ids=("jev_accept",),
        attempted_at_utc="2026-11-05T00:00:00+00:00",
        failure_reason="timeout",
    )


def test_runner_summary_excludes_attempts_from_another_experiment(tmp_path: Path) -> None:
    store = AttemptStore(tmp_path / "attempts.jsonl")
    assert store.append(stored_attempt(experiment_id="other", attempt_id="other:candidate-1:a00"))

    runner = JevRunner(
        SimpleNamespace(execution_mode="mocked"),  # type: ignore[arg-type]
        store,
        experiment_id="current",
        questions=(accept_reject_question(),),
        model_id="jev-1.13.0",
    )

    result = runner._summarise(
        (Decision(candidate_id="candidate-current", terminal="skip", source="failure_policy"),),
        {},
    )

    assert result.coverage["attempts_total"] == 0
    assert result.coverage["failure_reasons"] == {}


def test_runner_summary_excludes_orphan_attempts_from_same_store(tmp_path: Path) -> None:
    store = AttemptStore(tmp_path / "attempts.jsonl")
    assert store.append(
        stored_attempt(
            experiment_id="current",
            attempt_id="current:orphan:a00",
            candidate_id="orphan",
        )
    )

    runner = JevRunner(
        SimpleNamespace(execution_mode="mocked"),  # type: ignore[arg-type]
        store,
        experiment_id="current",
        questions=(accept_reject_question(),),
        model_id="jev-1.13.0",
    )

    result = runner._summarise(
        (Decision(candidate_id="candidate-current", terminal="skip", source="failure_policy"),),
        {},
    )

    assert result.coverage["attempts_total"] == 0
    assert result.coverage["failure_reasons"] == {}


def test_attempt_store_rejects_conflicting_duplicate_id(tmp_path: Path) -> None:
    store = AttemptStore(tmp_path / "attempts.jsonl")
    first = stored_attempt(experiment_id="current", attempt_id="current:candidate-1:a00")
    assert store.append(first)

    conflicting = replace(first, payload_hash="different-payload")
    with pytest.raises(ValueError, match="conflicting attempt"):
        store.append(conflicting)


def test_attempt_store_serializes_writes_across_instances(tmp_path: Path) -> None:
    path = tmp_path / "attempts.jsonl"
    stores = [AttemptStore(path) for _ in range(8)]
    attempt = stored_attempt(experiment_id="current", attempt_id="v2-shared-a00")

    with ThreadPoolExecutor(max_workers=len(stores)) as pool:
        results = list(pool.map(lambda store: store.append(attempt), stores))

    assert sum(results) == 1
    assert len(AttemptStore(path).attempts()) == 1


def test_attempt_store_serializes_writes_across_processes(tmp_path: Path) -> None:
    path = tmp_path / "process-attempts.jsonl"
    with ProcessPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(_append_attempt_in_child, [str(path)] * 4))

    assert sum(results) == 1
    assert len(AttemptStore(path).attempts()) == 1


def test_attempt_store_fails_closed_on_truncated_record(tmp_path: Path) -> None:
    path = tmp_path / "attempts.jsonl"
    path.write_text('{"attempt_id":"partial"', encoding="utf-8")

    with pytest.raises(ValueError, match="corrupt at line 1"):
        AttemptStore(path)


def test_attempt_id_does_not_collide_on_colon_delimiters() -> None:
    assert attempt_id_for("a:b", "c", 0) != attempt_id_for("a", "b:c", 0)


def test_runner_summary_excludes_other_execution_modes(tmp_path: Path) -> None:
    store = AttemptStore(tmp_path / "attempts.jsonl")
    assert store.append(
        stored_attempt(
            experiment_id="current",
            attempt_id="current:candidate-current:a00",
            candidate_id="candidate-current",
            execution_mode="live",
        )
    )

    runner = JevRunner(
        SimpleNamespace(execution_mode="mocked"),  # type: ignore[arg-type]
        store,
        experiment_id="current",
        questions=(accept_reject_question(),),
        model_id="jev-1.13.0",
    )

    result = runner._summarise(
        (Decision(candidate_id="candidate-current", terminal="skip", source="failure_policy"),),
        {},
    )

    assert result.coverage["attempts_total"] == 0
    assert result.coverage["failure_reasons"] == {}


def test_restart_replay_requires_matching_request_contract(tmp_path: Path) -> None:
    asyncio.run(_assert_restart_replay_requires_matching_request_contract(tmp_path))


async def _assert_restart_replay_requires_matching_request_contract(tmp_path: Path) -> None:
    store = AttemptStore(tmp_path / "attempts.jsonl")
    client = _CountingClient()
    runner = JevRunner(
        client,
        store,
        experiment_id="exp-replay",
        questions=(Question(id="q", type="box", instructions="classify"),),
        model_id="jev-1.13.0",
    )
    await runner.run((_candidate(value=1.0),))
    replay = await runner.run((_candidate(value=2.0),))

    assert client.calls == 1
    assert replay.decision_for("candidate-1").failure_reason == "replay_contract_mismatch"


def test_legacy_attempt_without_request_fingerprint_fails_closed(tmp_path: Path) -> None:
    asyncio.run(_assert_legacy_attempt_without_request_fingerprint_fails_closed(tmp_path))


async def _assert_legacy_attempt_without_request_fingerprint_fails_closed(
    tmp_path: Path,
) -> None:
    store = AttemptStore(tmp_path / "attempts.jsonl")
    client = _CountingClient()
    runner = JevRunner(
        client,
        store,
        experiment_id="exp-legacy",
        questions=(Question(id="q", type="box", instructions="classify"),),
        model_id="jev-1.13.0",
    )
    await runner.run((_candidate(),))
    record = store.attempts()[0]
    legacy = replace(record, request_fingerprint=None)
    legacy_path = tmp_path / "legacy.jsonl"
    legacy_store = AttemptStore(legacy_path)
    assert legacy_store.append(legacy)

    result = await replace_runner_store(runner, legacy_store).run((_candidate(),))

    assert client.calls == 1
    assert result.decision_for("candidate-1").failure_reason == "replay_contract_mismatch"


def replace_runner_store(runner: JevRunner, store: AttemptStore) -> JevRunner:
    return JevRunner(
        runner.client,
        store,
        experiment_id=runner.experiment_id,
        questions=runner.questions,
        model_id=runner.model_id,
        failure_policy=runner.failure_policy,
    )


def test_raw_response_path_is_safe_and_unique_per_attempt(tmp_path: Path) -> None:
    asyncio.run(_assert_raw_response_path_is_safe_and_unique_per_attempt(tmp_path))


async def _assert_raw_response_path_is_safe_and_unique_per_attempt(tmp_path: Path) -> None:
    store = AttemptStore(tmp_path / "attempts.jsonl")
    client = _CountingClient()
    runner = JevRunner(
        client,
        store,
        experiment_id="../escape",
        questions=(Question(id="q", type="box", instructions="classify"),),
        model_id="jev-1.13.0",
        raw_response_dir=str(tmp_path / "raw"),
        retry=RetryPolicy(max_retries=1, base_delay_s=0, max_delay_s=0),
    )
    await runner.run((_candidate(candidate_id="../candidate"),))

    attempt = store.attempts()[0]
    raw_path = Path(attempt.raw_response_ref or "")
    assert raw_path.is_relative_to((tmp_path / "raw").resolve())
    assert raw_path.name.endswith("-a00.json")


def test_cancelled_attempt_is_persisted_and_recovers_with_zero_retry_budget(tmp_path: Path) -> None:
    asyncio.run(
        _assert_cancelled_attempt_is_persisted_and_recovers_with_zero_retry_budget(tmp_path)
    )


async def _assert_cancelled_attempt_is_persisted_and_recovers_with_zero_retry_budget(
    tmp_path: Path,
) -> None:
    class CancelOnceClient(_CountingClient):
        async def answer(self, request, *, questions, attempt_number=0):
            self.calls += 1
            if self.calls == 1:
                raise asyncio.CancelledError
            return await super().answer(request, questions=questions, attempt_number=attempt_number)

    store = AttemptStore(tmp_path / "attempts.jsonl")
    client = CancelOnceClient()
    runner = JevRunner(
        client,
        store,
        experiment_id="exp-cancel",
        questions=(Question(id="q", type="box", instructions="classify"),),
        model_id="jev-1.13.0",
        retry=RetryPolicy(max_retries=0, base_delay_s=0, max_delay_s=0),
    )
    with pytest.raises(asyncio.CancelledError):
        await runner.run((_candidate(),))
    result = await runner.run((_candidate(),))

    assert client.calls == 3
    assert store.attempts()[0].failure_reason == "cancelled"
    assert result.decision_for("candidate-1").source == "jev"
