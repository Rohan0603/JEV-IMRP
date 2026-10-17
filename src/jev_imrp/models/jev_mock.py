"""Deterministic mock JEV client (plan 9.4 / 9.5).

The mock is the default for tests, CI and the synthetic demo. It exists so the
platform can be exercised end to end without credentials and without network,
and so a *reproducible* answer is a property of the code rather than of a
provider's mood.

Two rules it never breaks:

1. Every answer it returns is labelled ``execution_mode="mocked"`` in the attempt
   store and in anything derived from it. A mocked number must never be
   mistakable for a real one.
2. It fabricates nothing silently. Answers are a deterministic function of the
   request payload hash, and any scripted failure is a named step in a fixture.

Scripted steps (``"ok"``, ``"429"``, ``"529"``, ``"500"``, ``"timeout"``,
``"malformed"``, ``"model_mismatch"``) let a test inject failures without a
server.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from jev_imrp.models.jev_client import (
    CONFIRMATORY_MODEL_ID,
    CallResult,
    JevContractError,
    Question,
    canonical_json,
)

#: Scenario steps a fixture may script, in the order they may occur.
MOCK_STEPS: Final = (
    "ok",
    "429",
    "529",
    "500",
    "404",
    "timeout",
    "malformed",
    "model_mismatch",
)


def _digest(value: float, places: int = 6) -> float:
    return round(value, places)


def deterministic_answer(
    payload_hash: str,
    question: Question,
    *,
    model_id: str,
    force_choice: str | None = None,
) -> dict[str, Any]:
    """A stable pseudo-answer derived only from the request hash.

    Deterministic on purpose: two runs over the same immutable data and config
    must produce identical outputs (plan 14.3).
    """
    seed = hashlib.sha256(f"{payload_hash}|{question.content_hash}".encode()).digest()
    unit = int.from_bytes(seed[:8], "big") / 2**64
    if question.type == "choice":
        accept_probability = 0.2 + 0.6 * unit
        choice = force_choice or (question.choices[0] if unit < 0.5 else question.choices[-1])
        probabilities = {
            name: _digest(accept_probability if index == 0 else 1.0 - accept_probability)
            for index, name in enumerate(question.choices)
        }
        return {
            "choice": choice,
            "probabilities": probabilities,
            "confidence": _digest(0.5 + 0.4 * unit),
        }
    if question.type == "score":
        return {
            "score": [_digest(unit) for _ in question.criteria],
            "legend": list(question.criteria),
            "probabilities": {name: _digest(unit) for name in question.criteria},
            "confidence": _digest(unit),
        }
    if question.type == "noul":
        return {"noul": _digest(unit)}
    return {"box": {"x": _digest(unit), "y": _digest(1.0 - unit)}}


def response_payload(
    payload_hash: str,
    questions: tuple[Question, ...],
    *,
    model_id: str,
    answers: Mapping[str, Any] | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> dict[str, Any]:
    """A contract-shaped response body for one request."""
    body: dict[str, Any] = {"model": model_id, "answers": {}}
    for question in questions:
        override = (answers or {}).get(question.id)
        body["answers"][question.id] = (
            dict(override)
            if override is not None
            else deterministic_answer(payload_hash, question, model_id=model_id)
        )
    body["usage"] = {
        "input_tokens": input_tokens if input_tokens is not None else len(payload_hash) // 2,
        "output_tokens": output_tokens if output_tokens is not None else len(payload_hash) // 4,
    }
    return body


@dataclass(frozen=True)
class MockConfig:
    """Fixture contents plus the model the mock claims to have answered with."""

    #: candidate_id -> the ordered steps scripted for that candidate.
    steps: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    #: Steps used for any candidate the fixture does not mention.
    default_steps: tuple[str, ...] = ("ok",)
    #: candidate_id -> a literal answers object overriding the deterministic answer.
    answers: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    model_id: str = CONFIRMATORY_MODEL_ID
    #: Model id the response claims, when it should differ from the requested one.
    returned_model_id: str | None = None

    @staticmethod
    def from_fixture(path: str | Path) -> MockConfig:
        """Load a JSON fixture: ``{"steps": {...}, "default_steps": [...], ...}``."""
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise JevContractError(f"mock fixture must be an object: {path}")
        steps = {key: tuple(value) for key, value in (raw.get("steps") or {}).items()}
        for key, sequence in steps.items():
            for step in sequence:
                if step not in MOCK_STEPS:
                    raise JevContractError(f"mock fixture {key}: unknown step {step!r}")
        return MockConfig(
            steps=steps,
            default_steps=tuple(raw.get("default_steps") or ("ok",)),
            answers={k: dict(v) for k, v in (raw.get("answers") or {}).items()},
            model_id=raw.get("model_id", CONFIRMATORY_MODEL_ID),
            returned_model_id=raw.get("returned_model_id"),
        )


class MockJevClient:
    """Fixture-driven, deterministic stand-in for :class:`LiveJevClient`.

    It implements the same :class:`~jev_imrp.models.jev_client.JevClient`
    interface, so the runner, the attempt store and the failure policy are
    exercised by exactly the same code in tests as in a live replay.
    """

    execution_mode = "mocked"

    def __init__(
        self,
        config: MockConfig | None = None,
        *,
        endpoint: str = "mock://jev",
    ) -> None:
        self.config = config or MockConfig()
        self.execution_mode = "mocked"
        self.model_id = self.config.model_id
        self.endpoint = endpoint
        self.calls: list[str] = []

    def _candidate_id(self, request: Mapping[str, Any]) -> str:
        state = request.get("state")
        if isinstance(state, dict):
            return str(state.get("candidate_id", ""))
        return ""

    def _next_step(self, candidate_id: str, index: int) -> str:
        sequence = self.config.steps.get(candidate_id, self.config.default_steps)
        if not sequence:
            return "ok"
        if index < len(sequence):
            return sequence[index]
        return sequence[-1]

    async def answer(
        self,
        request: dict[str, Any],
        *,
        questions: tuple[Question, ...],
        attempt_number: int = 0,
    ) -> CallResult:
        """One scripted attempt. ``attempt_number`` indexes the scripted steps."""
        candidate_id = self._candidate_id(request)
        self.calls.append(candidate_id)
        step = self._next_step(candidate_id, attempt_number)
        payload_hash = hashlib.sha256(canonical_json(request).encode()).hexdigest()
        returned = self.config.returned_model_id or self.config.model_id

        if step == "429":
            return CallResult(status=429, failure_reason="http_error", retry_after="0")
        if step == "529":
            return CallResult(status=529, failure_reason="http_error", retry_after="0")
        if step == "500":
            return CallResult(status=500, failure_reason="http_error")
        if step == "404":
            return CallResult(status=404, failure_reason="http_error")
        if step == "timeout":
            return CallResult(failure_reason="timeout")
        if step == "malformed":
            return CallResult(status=200, payload={"model": returned}, failure_reason=None)
        if step == "model_mismatch":
            return CallResult(
                status=200,
                payload=response_payload(payload_hash, questions, model_id="jev-other"),
                returned_model_id="jev-other",
            )
        return CallResult(
            status=200,
            payload=response_payload(
                payload_hash,
                questions,
                model_id=returned,
                answers=self.config.answers.get(candidate_id),
            ),
            returned_model_id=returned,
        )

    async def aclose(self) -> None:
        return None


def scripted_steps(*steps: str) -> dict[str, tuple[str, ...]]:
    """Fixture shorthand: ``scripted_steps("429", "ok")`` -> one candidate's steps."""
    return {"c": tuple(steps)}


def steps_for(candidates: Sequence[str], *steps: str) -> dict[str, tuple[str, ...]]:
    """The same scripted step list applied to several candidates."""
    return {candidate_id: tuple(steps) for candidate_id in candidates}
