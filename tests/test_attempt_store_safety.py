import asyncio
from pathlib import Path

import pytest

from jev_imrp.experiments.attempt_store import AttemptStore, AttemptStoreCorruptionError
from jev_imrp.experiments.runner import JevRunner
from jev_imrp.models.jev_client import CallResult, Candidate, Question, attempt_id_for


class SuccessfulClient:
    execution_mode = "mocked"
    model_id = "jev-1.13.0"

    async def answer(self, request, *, questions, attempt_number=0):
        return CallResult(
            payload={
                "model": self.model_id,
                "answers": {"q": {"value": "accept", "confidence": 0.9}},
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
            returned_model_id=self.model_id,
            status=200,
        )

    async def aclose(self) -> None:
        return None


def test_two_store_instances_do_not_duplicate_an_attempt(tmp_path: Path) -> None:
    asyncio.run(_assert_two_store_instances_do_not_duplicate_an_attempt(tmp_path))


async def _assert_two_store_instances_do_not_duplicate_an_attempt(tmp_path: Path) -> None:
    path = tmp_path / "attempts.jsonl"
    first_store = AttemptStore(path)
    candidate = Candidate(
        candidate_id="candidate-1",
        symbol="SYNTH",
        side="BUY",
        decision_timestamp_utc="2026-01-01T00:00:00+00:00",
        horizon_minutes=5,
        features={},
    )
    await JevRunner(
        SuccessfulClient(),
        first_store,
        experiment_id="exp-store",
        questions=(Question(id="q", type="box", instructions="classify"),),
        model_id="jev-1.13.0",
        confirmatory=True,
    ).run((candidate,))
    attempt = first_store.attempts()[0]

    second_store = AttemptStore(path)
    assert second_store.append(attempt) is False
    assert len(second_store.attempts()) == 1


def test_attempt_store_fails_closed_on_malformed_jsonl(tmp_path: Path) -> None:
    path = tmp_path / "attempts.jsonl"
    store = AttemptStore(path)
    path.write_text('{"attempt_id":"partial"', encoding="utf-8")

    with pytest.raises(AttemptStoreCorruptionError):
        store.attempts()


def test_attempt_ids_are_unambiguous_for_delimiter_containing_identifiers() -> None:
    left = attempt_id_for("a:b", "c", 0, "mocked")
    right = attempt_id_for("a", "b:c", 0, "mocked")

    assert left != right
    assert left.startswith("jev-a2-")
