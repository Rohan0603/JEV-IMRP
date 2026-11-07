"""JEV candidate-stream runner (plan 9.4 / 10.5).

Responsibilities, and nothing beyond them:

* bounded concurrency over the candidate stream (never one unbounded task per row);
* bounded retries, on 429/529 only -- no idempotency is documented, so every other
  failure is recorded and not re-sent;
* persist **every** attempt to the durable store, successes and failures alike;
* apply the predeclared failure policy, and report coverage and failure rate as
  first-class outputs rather than as an afterthought.

The scoring note that matters for interpretation: the score is JEV's own answer
probability/confidence, used as a ranking/decision score. It is not a calibrated
probability that a trade is profitable (plan 9.3), and nothing here treats it as
one.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from jev_imrp.common.logging import get_logger
from jev_imrp.common.timeutils import utc_now
from jev_imrp.experiments.attempt_store import AttemptStore
from jev_imrp.experiments.manifest import RunStatus
from jev_imrp.models.jev_client import (
    Attempt,
    CallResult,
    Candidate,
    FailurePolicy,
    JevClient,
    JevContractError,
    ParsedAnswer,
    ParsedResponse,
    Question,
    RetryPolicy,
    attempt_id_for,
    build_request,
    build_state,
    canonical_hash,
    check_model_id,
    validate_response,
)

_LOG = get_logger(__name__)

#: The coverage/failure-rate fields the runner always emits (plan 10.5 item 10).
COVERAGE_OUTPUTS: tuple[str, ...] = (
    "candidates_total",
    "candidates_attempted",
    "candidates_answered",
    "candidates_failed",
    "candidates_skipped",
    "coverage_rate",
    "failure_rate",
    "accepted",
    "rejected",
    "accepted_rate",
    "attempts_total",
    "failure_reasons",
)


@dataclass(frozen=True)
class Decision:
    """The filter's outcome for one candidate.

    ``source`` is ``jev`` when JEV answered, ``failure_policy`` when the
    predeclared policy resolved a failed candidate, and ``no_answer`` when
    nothing could be asked (e.g. the candidate was skipped by policy).
    """

    candidate_id: str
    terminal: str
    source: str
    score: float | None = None
    answer: ParsedAnswer | None = None
    attempt_id: str | None = None
    failure_reason: str | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "terminal": self.terminal,
            "source": self.source,
            "score": self.score,
            "answer": self.answer.to_record() if self.answer else None,
            "attempt_id": self.attempt_id,
            "failure_reason": self.failure_reason,
        }


@dataclass(frozen=True)
class JevRunResult:
    """Everything one run produced: decisions, coverage outputs, artifact paths."""

    experiment_id: str
    execution_mode: str
    failure_policy: str
    status: RunStatus
    decisions: tuple[Decision, ...]
    coverage: dict[str, Any]
    artifact_paths: dict[str, str]
    failures: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def decision_for(self, candidate_id: str) -> Decision | None:
        return next((d for d in self.decisions if d.candidate_id == candidate_id), None)

    def to_record(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "execution_mode": self.execution_mode,
            "failure_policy": self.failure_policy,
            "status": str(self.status),
            "decisions": [d.to_record() for d in self.decisions],
            "coverage": dict(self.coverage),
            "artifact_paths": dict(self.artifact_paths),
            "failures": list(self.failures),
            "warnings": list(self.warnings),
        }

    def outputs_hash(self) -> str:
        """Hash of the decisions + coverage: the repeatability fingerprint.

        Excludes timestamps and artifact paths, which legitimately differ between
        two runs of the same experiment.
        """
        return canonical_hash(
            {
                "experiment_id": self.experiment_id,
                "execution_mode": self.execution_mode,
                "failure_policy": self.failure_policy,
                "decisions": [d.to_record() for d in self.decisions],
                "coverage": dict(self.coverage),
            }
        )


#: What each predeclared failure policy does to a candidate whose JEV call failed.
FAILURE_POLICY_TERMINAL: Mapping[FailurePolicy, str] = {
    "reject": "reject",
    "skip": "skip",
    # Fallback means: the comparator's own rule decides, so a JEV outage cannot
    # quietly change the coverage any other contestant is evaluated at.
    "fallback": "fallback",
}


def apply_failure_policy(
    policy: FailurePolicy,
    candidate: Candidate,
    *,
    fallback_decider: Callable[[Candidate], bool] | None = None,
) -> Decision:
    """Resolve one failed candidate under the predeclared policy.

    The same function is used for every candidate in a run, so the policy cannot
    be applied inconsistently across a comparison (plan 9.4).
    """
    if policy not in FAILURE_POLICY_TERMINAL:
        raise ValueError(f"unknown failure policy: {policy!r}")
    if policy == "fallback":
        accept = bool(fallback_decider(candidate)) if fallback_decider else True
        return Decision(
            candidate_id=candidate.candidate_id,
            terminal="accept" if accept else "reject",
            source="failure_policy",
            failure_reason="jev_failed",
        )
    return Decision(
        candidate_id=candidate.candidate_id,
        terminal=FAILURE_POLICY_TERMINAL[policy],
        source="failure_policy",
        failure_reason="jev_failed",
    )


def _score_of(answer: ParsedAnswer) -> float | None:
    """The decision score: the model's own answer probability for its first choice.

    This is JEV's answer probability, not a calibrated P(profit) (plan 9.3).
    """
    if answer.probabilities:
        return next(iter(answer.probabilities.values()))
    return answer.confidence


@dataclass
class _AttemptOutcome:
    attempt_ids: list[str] = field(default_factory=list)
    answers: tuple[ParsedAnswer, ...] = ()
    returned_model_id: str | None = None
    failure_reason: str | None = None
    http_status: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    payload_hash: str = ""


class JevRunner:
    """Runs one candidate stream through a JEV client under a declared policy."""

    def __init__(
        self,
        client: JevClient,
        store: AttemptStore,
        *,
        experiment_id: str,
        questions: tuple[Question, ...],
        model_id: str,
        failure_policy: FailurePolicy = "reject",
        retry: RetryPolicy | None = None,
        max_concurrency: int = 4,
        accept_choice: str | None = None,
        clock: Callable[[], datetime] = utc_now,
        fallback_decider: Callable[[Candidate], bool] | None = None,
        raw_response_dir: str | None = None,
        confirmatory: bool = True,
    ) -> None:
        if failure_policy not in FAILURE_POLICY_TERMINAL:
            raise ValueError(f"unknown failure policy: {failure_policy!r}")
        if not questions:
            raise ValueError("a run needs at least one question")
        check_model_id(model_id, confirmatory=confirmatory)
        self.client = client
        self.store = store
        self.experiment_id = experiment_id
        self.questions = questions
        self.model_id = model_id
        self.failure_policy = failure_policy
        self.retry = retry or RetryPolicy()
        self.max_concurrency = max_concurrency
        self.clock = clock
        self.fallback_decider = fallback_decider
        self.raw_response_dir = raw_response_dir
        primary = questions[0]
        self.accept_choice = accept_choice or (primary.choices[0] if primary.choices else None)
        self.prompt_version = primary.prompt_version
        self.prompt_hash = primary.content_hash

    # -- one candidate -----------------------------------------------------

    async def _run_one(self, candidate: Candidate, state: dict[str, Any]) -> _AttemptOutcome:
        """Send one state with bounded retries, persisting every try."""
        request = build_request(state, self.model_id, self.questions)
        outcome = _AttemptOutcome(payload_hash=canonical_hash(state))
        existing = [
            attempt
            for attempt in self.store.for_candidate(candidate.candidate_id)
            if attempt.experiment_id == self.experiment_id
            and attempt.execution_mode == self.client.execution_mode
        ]
        if existing:
            latest = max(existing, key=lambda attempt: attempt.retry_count)
            outcome = _AttemptOutcome(
                attempt_ids=[
                    attempt.attempt_id for attempt in sorted(existing, key=lambda a: a.retry_count)
                ],
                answers=latest.answers,
                returned_model_id=latest.returned_model_id,
                failure_reason=latest.failure_reason,
                http_status=latest.http_status,
                input_tokens=latest.input_tokens,
                output_tokens=latest.output_tokens,
                payload_hash=latest.payload_hash,
            )
            retryable = (
                latest.failure_reason is not None
                and latest.http_status in self.retry.status_codes
                and latest.retry_count < self.retry.max_retries
            )
            resumable_cancellation = latest.failure_reason == "cancelled"
            if latest.answers or (
                latest.failure_reason is not None and not retryable and not resumable_cancellation
            ):
                return outcome
            start_attempt = latest.retry_count + 1
        else:
            start_attempt = 0
        for attempt_number in range(start_attempt, self.retry.max_retries + 1):
            started = time.perf_counter()
            try:
                result: CallResult = await self.client.answer(
                    request, questions=self.questions, attempt_number=attempt_number
                )
            except asyncio.CancelledError:
                latency_ms = (time.perf_counter() - started) * 1000.0
                self._record(
                    request,
                    candidate,
                    CallResult(failure_reason="cancelled"),
                    attempt_number,
                    outcome,
                    latency_ms,
                )
                raise
            except Exception as exc:
                latency_ms = (time.perf_counter() - started) * 1000.0
                outcome = self._record(
                    request,
                    candidate,
                    CallResult(
                        failure_reason="client_error",
                        error_detail=exc.__class__.__name__,
                    ),
                    attempt_number,
                    outcome,
                    latency_ms,
                )
                return outcome
            latency_ms = (time.perf_counter() - started) * 1000.0
            outcome = self._record(request, candidate, result, attempt_number, outcome, latency_ms)
            if outcome.failure_reason is None:
                return outcome
            if outcome.http_status not in self.retry.status_codes:
                return outcome
            if attempt_number >= self.retry.max_retries:
                return outcome
            delay = self.retry.delay_for(attempt_number + 1, result.retry_after)
            _LOG.warning(
                "jev retry candidate=%s status=%s retry=%s delay_s=%.2f",
                candidate.candidate_id,
                outcome.http_status,
                attempt_number + 1,
                delay,
            )
            await asyncio.sleep(delay)
        return outcome  # pragma: no cover - the loop always returns

    def _record(
        self,
        request: dict[str, Any],
        candidate: Candidate,
        result: CallResult,
        attempt_number: int,
        outcome: _AttemptOutcome,
        latency_ms: float,
    ) -> _AttemptOutcome:
        """Validate one try and append it to the durable store."""
        answers: tuple[ParsedAnswer, ...] = ()
        response: ParsedResponse | None = None
        failure_reason = result.failure_reason
        if failure_reason is None:
            try:
                response = validate_response(
                    result.payload, questions=self.questions, requested_model=self.model_id
                )
            except JevContractError as exc:
                detail = str(exc)
                failure_reason = (
                    "model_mismatch"
                    if detail.startswith("model mismatch:")
                    else "malformed_response"
                )
                _LOG.warning(
                    "jev %s for candidate %s: %s",
                    failure_reason,
                    candidate.candidate_id,
                    exc.__class__.__name__,
                )
            else:
                answers = response.answers

        attempt = Attempt(
            attempt_id=attempt_id_for(
                self.experiment_id,
                candidate.candidate_id,
                attempt_number,
                self.client.execution_mode,
            ),
            experiment_id=self.experiment_id,
            candidate_id=candidate.candidate_id,
            execution_mode=self.client.execution_mode,
            requested_model_id=self.model_id,
            payload_hash=outcome.payload_hash,
            prompt_version=self.prompt_version,
            prompt_hash=self.prompt_hash,
            question_ids=tuple(q.id for q in self.questions),
            attempted_at_utc=self.clock().isoformat(),
            retry_count=attempt_number,
            returned_model_id=result.returned_model_id,
            http_status=result.status,
            latency_ms=round(latency_ms, 3),
            failure_reason=failure_reason,
            raw_response_ref=self._store_raw(result.payload, candidate, attempt_number),
            answers=answers,
            input_tokens=response.input_tokens if response else outcome.input_tokens,
            output_tokens=response.output_tokens if response else outcome.output_tokens,
        )
        self.store.append(attempt)
        outcome.attempt_ids.append(attempt.attempt_id)
        return _AttemptOutcome(
            attempt_ids=outcome.attempt_ids,
            answers=answers,
            returned_model_id=result.returned_model_id,
            failure_reason=failure_reason,
            http_status=result.status,
            input_tokens=response.input_tokens if response else outcome.input_tokens,
            output_tokens=response.output_tokens if response else outcome.output_tokens,
            payload_hash=outcome.payload_hash,
        )

    def _store_raw(self, payload: Any, candidate: Candidate, attempt_number: int) -> str | None:
        """Keep the raw response next to the attempt record (plan 9.4)."""
        if payload is None or not self.raw_response_dir:
            return None
        path = (
            Path(self.raw_response_dir)
            / self.experiment_id
            / self.client.execution_mode
            / f"{candidate.candidate_id}-a{attempt_number:02d}.json"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(payload, sort_keys=True, indent=2, default=str)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            with contextlib.suppress(FileExistsError):
                os.link(temporary_name, path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temporary_name)
        return str(path)

    # -- the stream --------------------------------------------------------

    async def run(
        self,
        candidates: Sequence[Candidate],
        *,
        artifact_paths: Mapping[str, str] | None = None,
    ) -> JevRunResult:
        """Evaluate the whole stream and report coverage and failure rate."""
        semaphore = asyncio.Semaphore(self.max_concurrency)
        states = {c.candidate_id: build_state(c) for c in candidates}

        async def guarded(candidate: Candidate) -> _AttemptOutcome:
            async with semaphore:
                return await self._run_one(candidate, states[candidate.candidate_id])

        outcomes = await asyncio.gather(*(guarded(c) for c in candidates))
        decisions = tuple(
            self._decide(candidate, outcome)
            for candidate, outcome in zip(candidates, outcomes, strict=True)
        )
        return self._summarise(decisions, dict(artifact_paths or {}))

    def _decide(self, candidate: Candidate, outcome: _AttemptOutcome) -> Decision:
        answer = outcome.answers[0] if outcome.answers else None
        if answer is not None:
            accepted = (
                answer.value == self.accept_choice if self.accept_choice is not None else True
            )
            return Decision(
                candidate_id=candidate.candidate_id,
                terminal="accept" if accepted else "reject",
                source="jev",
                score=_score_of(answer),
                answer=answer,
                attempt_id=outcome.attempt_ids[-1] if outcome.attempt_ids else None,
            )
        decision = apply_failure_policy(
            self.failure_policy, candidate, fallback_decider=self.fallback_decider
        )
        return Decision(
            candidate_id=decision.candidate_id,
            terminal=decision.terminal,
            source="failure_policy",
            attempt_id=outcome.attempt_ids[-1] if outcome.attempt_ids else None,
            failure_reason=outcome.failure_reason,
        )

    def _summarise(
        self, decisions: tuple[Decision, ...], artifact_paths: dict[str, str]
    ) -> JevRunResult:
        candidate_ids = {decision.candidate_id for decision in decisions}
        attempts = [
            attempt
            for attempt in self.store.attempts()
            if attempt.experiment_id == self.experiment_id
            and attempt.execution_mode == self.client.execution_mode
            and attempt.candidate_id in candidate_ids
        ]
        total = len(decisions)
        answered = sum(1 for d in decisions if d.source == "jev")
        failed = sum(1 for d in decisions if d.source == "failure_policy")
        skipped = sum(1 for d in decisions if d.terminal == "skip")
        accepted = sum(1 for d in decisions if d.terminal == "accept")
        rejected = sum(1 for d in decisions if d.terminal == "reject")
        reasons: dict[str, int] = {}
        for attempt in attempts:
            if attempt.failure_reason:
                reasons[attempt.failure_reason] = reasons.get(attempt.failure_reason, 0) + 1
        coverage = {
            "candidates_total": total,
            "candidates_attempted": sum(1 for d in decisions if d.attempt_id is not None),
            "candidates_answered": answered,
            "candidates_failed": failed,
            "candidates_skipped": skipped,
            "coverage_rate": round(answered / total, 6) if total else 0.0,
            "failure_rate": round(failed / total, 6) if total else 0.0,
            "accepted": accepted,
            "rejected": rejected,
            "accepted_rate": round(accepted / total, 6) if total else 0.0,
            "attempts_total": len(attempts),
            "failure_reasons": reasons,
        }
        if total == 0:
            status = RunStatus.FAILED
        elif failed == 0:
            status = RunStatus.COMPLETE
        else:
            status = RunStatus.PARTIAL
        warnings = (
            ("execution_mode=mocked: answers are synthetic, not real JEV judgements",)
            if self.client.execution_mode == "mocked"
            else ()
        )
        artifacts = {**artifact_paths, "attempts": str(self.store.path)}
        return JevRunResult(
            experiment_id=self.experiment_id,
            execution_mode=self.client.execution_mode,
            failure_policy=self.failure_policy,
            status=status,
            decisions=decisions,
            coverage=coverage,
            artifact_paths=artifacts,
            failures=tuple(sorted(reasons)),
            warnings=warnings,
        )
