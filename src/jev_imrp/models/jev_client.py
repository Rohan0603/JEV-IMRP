"""TypeSafe System One (JEV) client: contract, request/response validation, and
the HTTP implementation (plan sections 9.3 / 9.4).

Verified contract (official docs.typesafe.ai, checked 2026-10-17):

* ``POST https://api.typesafe.ai/v1/systemone``, ``Authorization: Bearer <key>``,
  ``Content-Type: application/json``.
* Request: ``state`` (string | object | array), ``model`` (string), ``questions``
  (map of question id -> question; question types ``noul``/``choice``/``score``/``box``).
* Response: ``model`` (the model that answered -- always logged), ``answers`` keyed by
  the question ids sent, ``usage`` with ``input_tokens``/``output_tokens``.
* One request evaluates ONE state against one or more named questions. An array
  state is a sequence inside one case, never a batch of independent cases.
* ``/v1/decisions`` and ``/v1/judgments`` are removed; never referenced here.

Rules enforced here, not left to the caller:

* no idempotency semantics are documented, so **only** 429/529 are retried;
* no server-side timeout is documented, so timeouts are explicit client-side and a
  timeout is a recorded failure;
* the returned model id is logged on every call, and a returned model other than
  the requested one is a failure, never a silent success;
* a moving alias (``jev-latest``/``jev-preview``) is refused in confirmatory mode;
* malformed responses are failures, never silent NaNs.

The deterministic mock lives in :mod:`jev_imrp.models.jev_mock`; the attempt store,
failure policy and experiment manifest live in :mod:`jev_imrp.experiments`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import time
from dataclasses import dataclass, field
from typing import Any, Final, Literal, Protocol

from jev_imrp.common.errors import JevError
from jev_imrp.common.logging import get_logger
from jev_imrp.features.definitions import TRAINING_FEATURES

_LOG = get_logger(__name__)

# --------------------------------------------------------------------------
# Verified API contract constants
# --------------------------------------------------------------------------

ENDPOINT: Final = "https://api.typesafe.ai/v1/systemone"
#: Removed legacy endpoints. Referencing one is a bug, so they are named here only
#: so a contract test can assert they appear nowhere else in the package.
DEPRECATED_ENDPOINTS: Final = ("/v1/decisions", "/v1/judgments", "/v1/decision")

REQUEST_FIELDS: Final = ("state", "model", "questions")
RESPONSE_FIELDS: Final = ("model", "answers", "usage")
USAGE_FIELDS: Final = ("input_tokens", "output_tokens")

QUESTION_TYPES: Final = ("noul", "choice", "score", "box")

#: Documented request limits.
MAX_BODY_BYTES: Final = 32 * 1024 * 1024
MAX_NESTING_LEVELS: Final = 64
ACCEPTED_ENCODINGS: Final = ("gzip", "x-gzip", "zstd")

#: Published pricing snapshot (USD per token) for cost reporting only. The API
#: returns no cost field, so nothing here invents one on a stored record.
PRICING_PER_BTOK_USD: Final = {"input": 42.0, "output": 0.042}

#: Exact model id pinned for confirmatory runs, and the aliases that must not be
#: used there (aliases move between releases silently).
CONFIRMATORY_MODEL_ID: Final = "jev-1.13.0"
MODEL_ALIASES: Final = ("jev-latest", "jev-preview")

#: Environment variable holding the bearer token. Never read from config or code.
API_KEY_ENV: Final = "TYPESAFE_API_KEY"

ExecutionMode = Literal["mocked", "live"]
FailurePolicy = Literal["reject", "skip", "fallback"]
_FAILURE_POLICIES: Final = ("reject", "skip", "fallback")

#: Failure reasons. Every one of these is a *recorded* failure, never a silent drop.
FAILURE_REASONS: Final = (
    "no_credentials",
    "request_invalid",
    "request_too_large",
    "timeout",
    "http_error",
    "malformed_response",
    "model_mismatch",
    "alias_in_confirmatory",
    "client_error",
    "cancelled",
)


class JevContractError(JevError):
    """The request we built, or the response we received, violates the contract."""


class MissingCredentialsError(JevError):
    """The live client was constructed without an API key.

    Raised rather than guessed: the live path must never run unattended.
    """


# --------------------------------------------------------------------------
# Prompt / question versioning (plan 9.4 / 9.5)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Question:
    """One named question, versioned as a unit with its content hash.

    ``criteria`` is a mapping of criterion name -> description. The live API
    contract (docs.typesafe.ai) requires ``criteria`` to be a *dictionary* for
    ``choice`` questions; a list of strings is rejected with HTTP 422.
    """

    id: str
    type: str
    instructions: str
    choices: tuple[str, ...] = ()
    criteria: dict[str, str] = field(default_factory=dict)
    prompt_version: str = "prompt_v0"

    def __post_init__(self) -> None:
        if self.type not in QUESTION_TYPES:
            raise JevContractError(f"question {self.id}: unsupported type {self.type!r}")
        if self.type == "choice" and not self.choices:
            raise JevContractError(f"question {self.id}: choice question needs choices")
        if self.type == "score" and not self.criteria:
            raise JevContractError(f"question {self.id}: score question needs criteria")

    @property
    def content_hash(self) -> str:
        """sha256 over the canonical question body, excluding the hash itself."""
        return canonical_hash(
            {
                "id": self.id,
                "type": self.type,
                "instructions": self.instructions,
                "choices": list(self.choices),
                "criteria": dict(self.criteria),
                "prompt_version": self.prompt_version,
            }
        )

    def to_payload(self) -> dict[str, Any]:
        """The question exactly as it goes on the wire."""
        body: dict[str, Any] = {"type": self.type, "instructions": self.instructions}
        if self.choices:
            body["choices"] = list(self.choices)
        if self.criteria:
            body["criteria"] = dict(self.criteria)
        return body

    def to_record(self) -> dict[str, Any]:
        return {
            "question_id": self.id,
            "question_type": self.type,
            "instructions": self.instructions,
            "choices": list(self.choices),
            "criteria": dict(self.criteria),
            "prompt_version": self.prompt_version,
            "prompt_content_hash": self.content_hash,
        }


def accept_reject_question(
    *,
    question_id: str = "jev_accept",
    prompt_version: str = "jev_prompt_v1",
) -> Question:
    """The frozen first-study question: accept/reject as an accept/reject filter.

    Plan 9.3: JEV is used as an accept/reject filter first; direction generation,
    sizing and exits are separate experiments. The instructions therefore ask for a
    judgement about *this candidate under the declared execution and cost
    assumptions*, and explicitly say the confidence is a model answer, not a
    calibrated profit probability.
    """
    return Question(
        id=question_id,
        type="choice",
        instructions=(
            "You are screening one intraday NSE cash-equity candidate signal. Given the "
            "sanitized point-in-time state, decide whether this candidate is worth "
            "taking after the declared execution and cost assumptions.\n"
            "Answer 'accept' or 'reject' only. Judge the setup and the information "
            "available at the decision timestamp; do not invent future prices, news or "
            "fills. Your confidence is a property of your answer, not a calibrated "
            "probability that the trade will be profitable."
        ),
        choices=("accept", "reject"),
        criteria={
            "accept": "The point-in-time setup is tradable rather than noise, the expected "
            "edge is plausible after the declared round-trip cost, and the quote and "
            "liquidity context is fresh enough to act on.",
            "reject": "The setup is noise, the expected edge is not plausible after costs, "
            "or the quote/liquidity context is too stale to act on.",
        },
        prompt_version=prompt_version,
    )


# --------------------------------------------------------------------------
# Canonical state payload (plan 9.4: canonical payload plus its input hash)
# --------------------------------------------------------------------------


def canonical_json(payload: Any) -> str:
    """One canonical JSON encoding: sorted keys, no insignificant whitespace."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def canonical_hash(payload: Any) -> str:
    """sha256 hex digest of the canonical JSON encoding."""
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Candidate:
    """One candidate signal plus its point-in-time feature row.

    This is the only thing a JEV call is ever built from, so it cannot silently
    acquire extra information the baselines do not have (plan 7.4 / 9.2).
    """

    candidate_id: str
    symbol: str
    side: Literal["BUY", "SELL"]
    decision_timestamp_utc: str
    horizon_minutes: int
    features: dict[str, Any]
    feature_set_version: str = ""


def build_state(candidate: Candidate) -> dict[str, Any]:
    """The sanitized canonical state payload sent as ``state``.

    Shape (declared, and hashed for every attempt)::

        {
          "schema": "jev_state_v1",
          "candidate_id": str,
          "symbol": str,
          "side": "BUY"|"SELL",
          "decision_timestamp_utc": ISO-8601 str,
          "horizon_minutes": int,
          "feature_set_version": str,
          "features": {<allowlisted feature name>: float|str},
          "missing_features": [names omitted because absent or non-finite]
        }

    Sanitization, so the payload can never violate the documented request limits:
    only allowlisted training features are included (feature parity, plan 7.4);
    ``None``, NaN/inf and non-numeric values are dropped into ``missing_features``
    instead of being sent; booleans and nested containers are rejected; values are
    rounded to 8 decimals so the hash is stable across platforms. The payload is
    flat, so nesting depth is 2 and the 64-level limit cannot be reached.
    """
    features: dict[str, float | str] = {}
    missing: list[str] = []
    for name in TRAINING_FEATURES:
        value = candidate.features.get(name)
        if value is None:
            missing.append(name)
            continue
        if isinstance(value, (bool, dict, list, tuple, set)):
            raise JevContractError(f"feature {name} is not a scalar")
        if isinstance(value, str):
            features[name] = value
            continue
        if not isinstance(value, (int, float)) or not math.isfinite(float(value)):
            missing.append(name)
            continue
        features[name] = round(float(value), 8)
    return {
        "schema": "jev_state_v1",
        "candidate_id": candidate.candidate_id,
        "symbol": candidate.symbol,
        "side": candidate.side,
        "decision_timestamp_utc": candidate.decision_timestamp_utc,
        "horizon_minutes": candidate.horizon_minutes,
        "feature_set_version": candidate.feature_set_version,
        "features": features,
        "missing_features": sorted(missing),
    }


def build_request(
    state: dict[str, Any],
    model: str,
    questions: tuple[Question, ...],
) -> dict[str, Any]:
    """The wire request body: exactly ``state``, ``model`` and ``questions``."""
    if not questions:
        raise JevContractError("at least one question is required")
    return {
        "state": state,
        "model": model,
        "questions": {q.id: q.to_payload() for q in questions},
    }


def _depth(value: Any, level: int = 1) -> int:
    if isinstance(value, dict):
        return max([level, *(_depth(v, level + 1) for v in value.values())])
    if isinstance(value, list):
        return max([level, *(_depth(v, level + 1) for v in value)])
    return level


def validate_request(body: dict[str, Any]) -> None:
    """Validate a request against the documented limits before sending it.

    Catches locally what the API would answer with 400/413/415, so a rejected
    request never becomes an attempt with a billed or ambiguous outcome.
    """
    unknown = set(body) - set(REQUEST_FIELDS)
    if unknown:
        raise JevContractError(f"request has unknown field(s): {sorted(unknown)}")
    for field_name in REQUEST_FIELDS:
        if field_name not in body:
            raise JevContractError(f"request is missing {field_name!r}")
    model = body["model"]
    if not isinstance(model, str) or not model.strip():
        raise JevContractError("'model' must be a non-empty string")
    questions = body["questions"]
    if not isinstance(questions, dict) or not questions:
        raise JevContractError("'questions' must be a non-empty mapping of id -> question")
    for qid, q in questions.items():
        if not isinstance(qid, str) or not qid:
            raise JevContractError("question ids must be non-empty strings")
        if not isinstance(q, dict) or q.get("type") not in QUESTION_TYPES:
            raise JevContractError(f"question {qid!r} has an unsupported or missing type")
        if not isinstance(q.get("instructions"), str) or not q["instructions"].strip():
            raise JevContractError(f"question {qid!r} needs non-empty instructions")
    state = body["state"]
    if not isinstance(state, (str, dict, list)):
        raise JevContractError("'state' must be a string, object or array")
    if _depth(state) > MAX_NESTING_LEVELS:
        raise JevContractError("state exceeds the documented nesting limit")
    # allow_nan=False raises on NaN/inf, which the contract forbids.
    encoded = canonical_json(state).encode("utf-8")
    if encoded.startswith(b"\xef\xbb\xbf"):
        raise JevContractError("state must be UTF-8 without a BOM")
    try:
        full = canonical_json(body).encode("utf-8")
    except ValueError as exc:
        raise JevContractError("state contains a non-finite number") from exc
    if len(full) > MAX_BODY_BYTES:
        raise JevContractError("request body exceeds the documented 32 MiB limit")


# --------------------------------------------------------------------------
# Response parsing (malformed is a failure, never a silent NaN)
# --------------------------------------------------------------------------

_REQUIRED_ANSWER_FIELDS: Final = {"noul": ("noul",), "choice": ("choice",), "score": ("score",)}


@dataclass(frozen=True)
class ParsedAnswer:
    """One answer as returned, with the fields the contract guarantees."""

    question_id: str
    question_type: str
    value: Any
    probabilities: dict[str, float] = field(default_factory=dict)
    confidence: float | None = None

    def to_record(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "question_type": self.question_type,
            "value": self.value,
            "probabilities": dict(self.probabilities),
            "confidence": self.confidence,
        }


@dataclass(frozen=True)
class ParsedResponse:
    """A validated response: the model that answered, the answers, the usage."""

    returned_model_id: str
    answers: tuple[ParsedAnswer, ...]
    input_tokens: int | None
    output_tokens: int | None
    raw: dict[str, Any]

    def answer(self, question_id: str) -> ParsedAnswer | None:
        return next((a for a in self.answers if a.question_id == question_id), None)


def _require_finite(value: Any, field_name: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        raise JevContractError(f"{field_name} must be a finite number")
    return float(value)


def validate_response(
    payload: Any,
    *,
    questions: tuple[Question, ...],
    requested_model: str,
) -> ParsedResponse:
    """Parse and validate a response body.

    A missing or malformed field raises :class:`JevContractError`; the caller turns
    that into a recorded failure. Nothing is defaulted to NaN.
    """
    if not isinstance(payload, dict):
        raise JevContractError("response body must be a JSON object")
    missing = [f for f in RESPONSE_FIELDS if f not in payload]
    if missing:
        raise JevContractError(f"response is missing field(s): {missing}")
    returned_model = payload["model"]
    if not isinstance(returned_model, str) or not returned_model.strip():
        raise JevContractError("response 'model' must be a non-empty string")
    if returned_model != requested_model:
        raise JevContractError(
            f"model mismatch: requested {requested_model!r}, returned {returned_model!r}"
        )
    raw_answers = payload["answers"]
    if not isinstance(raw_answers, dict) or not raw_answers:
        raise JevContractError("response 'answers' must be a non-empty object")
    sent = {q.id: q for q in questions}
    unexpected = sorted(set(raw_answers) - set(sent))
    if unexpected:
        raise JevContractError(f"response answers unknown question(s): {unexpected}")
    missing_answers = sorted(set(sent) - set(raw_answers))
    if missing_answers:
        raise JevContractError(f"response is missing answer(s) for: {missing_answers}")

    parsed: list[ParsedAnswer] = []
    for qid, question in sent.items():
        answer = raw_answers[qid]
        if not isinstance(answer, dict):
            raise JevContractError(f"answer {qid!r} must be an object")
        required = _REQUIRED_ANSWER_FIELDS.get(question.type, ())
        absent = [f for f in required if f not in answer]
        if absent:
            raise JevContractError(f"answer {qid!r} is missing field(s): {absent}")
        probabilities: dict[str, float] = {}
        raw_probs = answer.get("probabilities", {})
        if raw_probs is not None:
            if not isinstance(raw_probs, dict):
                raise JevContractError(f"answer {qid!r} probabilities must be an object")
            for key, prob in raw_probs.items():
                probabilities[str(key)] = _require_finite(prob, f"{qid}.probabilities[{key}]")
        confidence = answer.get("confidence")
        if confidence is not None:
            confidence = _require_finite(confidence, f"{qid}.confidence")
        key = "choice" if question.type == "choice" else question.type
        value: Any = answer.get(key)
        if question.type == "score" and isinstance(value, list):
            scores = {
                str(name): _require_finite(item, f"{qid}.score[{name}]")
                for name, item in zip(answer.get("legend", []), value, strict=False)
            }
            value = scores or value
        if question.type == "noul":
            value = _require_finite(value, f"{qid}.noul")
        if question.type == "choice" and not isinstance(value, str):
            raise JevContractError(f"answer {qid!r} choice must be a string")
        parsed.append(
            ParsedAnswer(
                question_id=qid,
                question_type=question.type,
                value=value,
                probabilities=probabilities,
                confidence=confidence,
            )
        )

    usage = payload["usage"]
    if usage is not None and not isinstance(usage, dict):
        raise JevContractError("response 'usage' must be an object or absent")
    usage = usage or {}
    tokens: dict[str, int | None] = {}
    for name in USAGE_FIELDS:
        raw = usage.get(name)
        if raw is None:
            tokens[name] = None
            continue
        if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
            raise JevContractError(f"usage.{name} must be a non-negative integer")
        tokens[name] = raw
    return ParsedResponse(
        returned_model_id=returned_model,
        answers=tuple(parsed),
        input_tokens=tokens["input_tokens"],
        output_tokens=tokens["output_tokens"],
        raw=payload,
    )


# --------------------------------------------------------------------------
# Attempt record (plan 9.4 -- every attempt, successful and failed)
# --------------------------------------------------------------------------

ATTEMPT_FIELDS: Final = (
    "attempt_id",
    "experiment_id",
    "candidate_id",
    "execution_mode",
    "requested_model_id",
    "returned_model_id",
    "payload_hash",
    "prompt_version",
    "prompt_hash",
    "question_ids",
    "attempted_at_utc",
    "http_status",
    "latency_ms",
    "retry_count",
    "failure_reason",
    "raw_response_ref",
    "answers",
    "input_tokens",
    "output_tokens",
)


@dataclass(frozen=True)
class Attempt:
    """One JEV call attempt, successful or not.

    Every attempt row carries ``execution_mode`` (``mocked`` or ``live``), so a
    mocked result can never be mistaken for a real one in a stored record.
    """

    attempt_id: str
    experiment_id: str
    candidate_id: str
    execution_mode: ExecutionMode
    requested_model_id: str
    payload_hash: str
    prompt_version: str
    prompt_hash: str
    question_ids: tuple[str, ...]
    attempted_at_utc: str
    retry_count: int = 0
    returned_model_id: str | None = None
    http_status: int | None = None
    latency_ms: float | None = None
    failure_reason: str | None = None
    raw_response_ref: str | None = None
    answers: tuple[ParsedAnswer, ...] = ()
    input_tokens: int | None = None
    output_tokens: int | None = None

    @property
    def ok(self) -> bool:
        return self.failure_reason is None

    def to_record(self) -> dict[str, Any]:
        """Flat row for the attempt store, JSON-encoding the nested answers."""
        return {
            "attempt_id": self.attempt_id,
            "experiment_id": self.experiment_id,
            "candidate_id": self.candidate_id,
            "execution_mode": self.execution_mode,
            "requested_model_id": self.requested_model_id,
            "returned_model_id": self.returned_model_id,
            "payload_hash": self.payload_hash,
            "prompt_version": self.prompt_version,
            "prompt_hash": self.prompt_hash,
            "question_ids": list(self.question_ids),
            "attempted_at_utc": self.attempted_at_utc,
            "http_status": self.http_status,
            "latency_ms": self.latency_ms,
            "retry_count": self.retry_count,
            "failure_reason": self.failure_reason,
            "raw_response_ref": self.raw_response_ref,
            "answers": [a.to_record() for a in self.answers],
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }

    @classmethod
    def from_record(cls, row: dict[str, Any]) -> Attempt:
        return cls(
            attempt_id=row["attempt_id"],
            experiment_id=row["experiment_id"],
            candidate_id=row["candidate_id"],
            execution_mode=row["execution_mode"],
            requested_model_id=row["requested_model_id"],
            payload_hash=row["payload_hash"],
            prompt_version=row["prompt_version"],
            prompt_hash=row["prompt_hash"],
            question_ids=tuple(row.get("question_ids") or ()),
            attempted_at_utc=row["attempted_at_utc"],
            retry_count=int(row.get("retry_count") or 0),
            returned_model_id=row.get("returned_model_id"),
            http_status=row.get("http_status"),
            latency_ms=row.get("latency_ms"),
            failure_reason=row.get("failure_reason"),
            raw_response_ref=row.get("raw_response_ref"),
            answers=tuple(
                ParsedAnswer(
                    question_id=a["question_id"],
                    question_type=a["question_type"],
                    value=a.get("value"),
                    probabilities=dict(a.get("probabilities") or {}),
                    confidence=a.get("confidence"),
                )
                for a in (row.get("answers") or ())
            ),
            input_tokens=row.get("input_tokens"),
            output_tokens=row.get("output_tokens"),
        )


def attempt_id_for(experiment_id: str, candidate_id: str, attempt_number: int) -> str:
    """Deterministic attempt id, so a rerun rewrites the same id, not a new one."""
    return f"{experiment_id}:{candidate_id}:a{attempt_number:02d}"


# --------------------------------------------------------------------------
# Client abstraction
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CallResult:
    """Transport-level outcome of one call, before attempt bookkeeping."""

    status: int | None = None
    payload: Any = None
    returned_model_id: str | None = None
    failure_reason: str | None = None
    error_detail: str | None = None
    retry_after: str | None = None


class JevClient(Protocol):
    """The one interface the runner uses. Implementations differ only in transport."""

    execution_mode: ExecutionMode
    model_id: str

    async def answer(
        self,
        request: dict[str, Any],
        *,
        questions: tuple[Question, ...],
        attempt_number: int = 0,
    ) -> CallResult: ...

    async def aclose(self) -> None: ...


@dataclass(frozen=True)
class RetryPolicy:
    """Bounded retry, on 429 and 529 only.

    No idempotency semantics are documented anywhere, so any other failure is
    recorded immediately. ``sleep`` is injectable so tests never actually wait.
    """

    max_retries: int = 3
    base_delay_s: float = 0.5
    max_delay_s: float = 30.0
    status_codes: tuple[int, ...] = (429, 529)

    def delay_for(self, retry_count: int, retry_after: str | None = None) -> float:
        """Exponential backoff, honouring ``retry-after`` when the server sends it."""
        if retry_after:
            try:
                return float(min(float(retry_after), self.max_delay_s))
            except ValueError:
                pass
        return float(min(self.base_delay_s * (2 ** (retry_count - 1)), self.max_delay_s))


@dataclass(frozen=True)
class LiveConfig:
    """Everything the live client must be told explicitly before it runs."""

    endpoint: str = ENDPOINT
    model_id: str = CONFIRMATORY_MODEL_ID
    confirmatory: bool = True
    timeout_s: float = 30.0
    max_concurrency: int = 4
    min_interval_s: float = 0.25
    retry: RetryPolicy = field(default_factory=RetryPolicy)
    #: Injectable sleep, so the rate limiter is testable without real waiting.
    sleep: Any = None


def check_model_id(model_id: str, *, confirmatory: bool) -> None:
    """Refuse a moving alias in a confirmatory run.

    Aliases are repointed between releases without notice, which would silently
    change a replay's model (plan 9.4).
    """
    if confirmatory and model_id in MODEL_ALIASES:
        raise JevContractError(
            f"model {model_id!r} is a moving alias; a confirmatory run must request an "
            f"exact model id such as {CONFIRMATORY_MODEL_ID!r}"
        )


class LiveJevClient:
    """Real HTTP client for ``POST /v1/systemone``.

    Never usable without an explicit API key: the key is read from the
    environment, is never logged, and is never placed in an attempt record.
    """

    def __init__(
        self,
        config: LiveConfig,
        *,
        api_key: str | None = None,
        client: Any | None = None,
    ) -> None:
        check_model_id(config.model_id, confirmatory=config.confirmatory)
        self.execution_mode: ExecutionMode = "live"
        self.model_id = config.model_id
        self.config = config
        self._api_key = api_key or os.environ.get(API_KEY_ENV)
        if not self._api_key:
            raise MissingCredentialsError(
                f"live JEV client needs an API key in ${API_KEY_ENV}; the real client is "
                "never exercised implicitly"
            )
        self._client = client
        self._owns_client = client is None
        self._semaphore = asyncio.Semaphore(config.max_concurrency)
        self._gate = asyncio.Lock()
        self._next_slot = 0.0
        self._sleep = config.sleep or asyncio.sleep

    def _ensure_client(self) -> Any:
        if self._client is None:
            import httpx  # imported lazily: mocked-only paths never need the network stack

            self._client = httpx.AsyncClient(timeout=self.config.timeout_s)
        return self._client

    async def _rate_limit(self) -> None:
        """Space starts out; concurrency is bounded by the semaphore."""
        async with self._gate:
            now = time.monotonic()
            wait = self._next_slot - now
            if wait > 0:
                await self._sleep(wait)
                now = time.monotonic()
            self._next_slot = max(now, self._next_slot) + self.config.min_interval_s

    async def _post(self, body: dict[str, Any]) -> CallResult:
        import httpx

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            await self._rate_limit()
            async with self._semaphore:
                response = await self._ensure_client().post(
                    self.config.endpoint, json=body, headers=headers
                )
        except httpx.TimeoutException as exc:
            return CallResult(failure_reason="timeout", error_detail=exc.__class__.__name__)
        except httpx.HTTPError as exc:
            return CallResult(failure_reason="client_error", error_detail=exc.__class__.__name__)
        status = response.status_code
        if status >= 400:
            return CallResult(
                status=status,
                failure_reason="http_error",
                retry_after=response.headers.get("retry-after"),
            )
        try:
            payload = response.json()
        except ValueError:
            return CallResult(status=status, failure_reason="malformed_response")
        return CallResult(
            status=status,
            payload=payload,
            returned_model_id=(payload.get("model") if isinstance(payload, dict) else None),
        )

    async def answer(
        self,
        request: dict[str, Any],
        *,
        questions: tuple[Question, ...],
        attempt_number: int = 0,
    ) -> CallResult:
        """One HTTP attempt.

        The bounded 429/529 retry loop deliberately lives in the runner, so that
        every try is a separately persisted attempt for both the live and the
        mocked client, and retry accounting cannot diverge between them.
        """
        del attempt_number
        try:
            validate_request(request)
        except JevContractError as exc:
            return CallResult(failure_reason="request_invalid", error_detail=str(exc))
        result = await self._post(request)
        if result.failure_reason is None:
            return _check_model(result, self.config.model_id)
        return result

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None


def _check_model(result: CallResult, requested_model: str) -> CallResult:
    """A response from a different model than requested is a failure to record."""
    returned = result.returned_model_id
    if returned is None:
        return CallResult(
            status=result.status, payload=result.payload, failure_reason="malformed_response"
        )
    if returned != requested_model:
        _LOG.warning(
            "jev returned unexpected model requested=%s returned=%s", requested_model, returned
        )
        return CallResult(
            status=result.status,
            payload=result.payload,
            returned_model_id=returned,
            failure_reason="model_mismatch",
        )
    return result
