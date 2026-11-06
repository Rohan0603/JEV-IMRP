from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

import pytest

from jev_imrp.experiments.attempt_store import AttemptStore
from jev_imrp.experiments.pricing import (
    CostStatus,
    aggregate_cost,
    estimate_attempt_cost,
    report_costs_by_mode,
)
from jev_imrp.experiments.runner import Decision, JevRunner
from jev_imrp.models.jev_client import CONFIRMATORY_MODEL_ID, Attempt

_DEFAULT_RETURNED_MODEL = object()


def attempt(
    *,
    attempt_id: str = "exp:candidate:a00",
    candidate_id: str = "candidate",
    mode: str = "live",
    retry_count: int = 0,
    input_tokens: int | None = 1_000_000,
    output_tokens: int | None = 10,
    failure_reason: str | None = None,
    model_id: str = CONFIRMATORY_MODEL_ID,
    returned_model_id: str | object | None = _DEFAULT_RETURNED_MODEL,
    http_status: int | None = None,
) -> Attempt:
    return Attempt(
        attempt_id=attempt_id,
        experiment_id="exp",
        candidate_id=candidate_id,
        execution_mode=mode,  # type: ignore[arg-type]
        requested_model_id=model_id,
        payload_hash="hash",
        prompt_version="prompt-v1",
        prompt_hash="prompt-hash",
        question_ids=("q",),
        attempted_at_utc="2026-11-05T00:00:00+00:00",
        retry_count=retry_count,
        returned_model_id=(
            model_id if returned_model_id is _DEFAULT_RETURNED_MODEL else returned_model_id
        ),  # type: ignore[arg-type]
        http_status=http_status
        if http_status is not None
        else (200 if failure_reason is None else 529),
        failure_reason=failure_reason,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def test_one_million_input_tokens_costs_042_usd_and_output_is_free() -> None:
    estimate = estimate_attempt_cost(attempt())

    assert estimate.status is CostStatus.ESTIMATED
    assert estimate.estimated_usd == Decimal("0.042")
    assert estimate.display_usd == "0.04200000"
    assert estimate.output_cost_usd == Decimal("0")


def test_small_input_uses_decimal_unit_conversion() -> None:
    estimate = estimate_attempt_cost(attempt(input_tokens=1_000, output_tokens=50_000))

    assert estimate.estimated_usd == Decimal("0.000042")
    assert estimate.display_usd == "0.00004200"


@pytest.mark.parametrize(
    "tokens",
    [None, -1, True, "1000"],
)
def test_missing_or_malformed_input_usage_is_unknown(tokens: object) -> None:
    estimate = estimate_attempt_cost(attempt(input_tokens=tokens))  # type: ignore[arg-type]

    assert estimate.status is CostStatus.UNKNOWN
    assert estimate.estimated_usd is None
    assert estimate.reason == "input_usage_missing_or_invalid"


def test_missing_output_usage_is_allowed_when_input_is_valid() -> None:
    estimate = estimate_attempt_cost(attempt(output_tokens=None))

    assert estimate.status is CostStatus.ESTIMATED
    assert estimate.estimated_usd == Decimal("0.042")
    assert estimate.missing_output_usage is True


def test_mocked_run_is_labeled_local_zero_not_provider_billing() -> None:
    estimate = estimate_attempt_cost(attempt(mode="mocked", input_tokens=1_000_000))

    assert estimate.status is CostStatus.MOCKED_LOCAL_ZERO
    assert estimate.estimated_usd == Decimal("0")
    assert estimate.display_usd == "0.00000000"
    assert estimate.mode_label == "mocked_local_spend"


@pytest.mark.parametrize("model_id", ["jev-other", "jev-latest"])
def test_unknown_model_is_not_priced(model_id: str) -> None:
    estimate = estimate_attempt_cost(attempt(model_id=model_id))

    assert estimate.status is CostStatus.UNKNOWN
    assert estimate.reason == "unknown_model"


def test_missing_returned_model_id_is_not_priced() -> None:
    estimate = estimate_attempt_cost(attempt(returned_model_id=None))

    assert estimate.status is CostStatus.UNKNOWN
    assert estimate.reason == "unknown_model"


def test_unknown_route_is_not_priced() -> None:
    estimate = estimate_attempt_cost(attempt(), route="https://example.invalid/jev")

    assert estimate.status is CostStatus.UNKNOWN
    assert estimate.reason == "unknown_route"


def test_invalid_execution_mode_is_not_priced() -> None:
    estimate = estimate_attempt_cost(attempt(mode="sandbox"))  # type: ignore[arg-type]

    assert estimate.status is CostStatus.UNKNOWN
    assert estimate.estimated_usd is None
    assert estimate.reason == "unknown_execution_mode"


@pytest.mark.parametrize("output_tokens", [-1, True, "1000"])
def test_malformed_output_usage_is_unknown(output_tokens: object) -> None:
    estimate = estimate_attempt_cost(attempt(output_tokens=output_tokens))  # type: ignore[arg-type]

    assert estimate.status is CostStatus.UNKNOWN
    assert estimate.estimated_usd is None
    assert estimate.reason == "output_usage_invalid"


def test_retry_unknown_billing_and_duplicate_attempts_are_preserved() -> None:
    first = attempt(
        attempt_id="exp:candidate:a00",
        retry_count=0,
        input_tokens=None,
        failure_reason="http_error",
    )
    terminal = attempt(attempt_id="exp:candidate:a01", retry_count=1, input_tokens=1_000_000)
    duplicate = replace(terminal)

    report = aggregate_cost([first, terminal, duplicate])

    assert report.status is CostStatus.UNKNOWN
    assert report.estimated_usd == Decimal("0.042")
    assert report.display_usd == "0.04200000"
    assert report.input_tokens == 1_000_000
    assert report.reason == "upstream_outcome_uncertain"
    assert report.attempts_counted == 2
    assert report.candidate_terminal_count == 1
    assert report.duplicate_attempts == 1


def test_failed_attempt_does_not_count_as_terminal_candidate() -> None:
    report = aggregate_cost([attempt(input_tokens=None, failure_reason="timeout")])

    assert report.status is CostStatus.UNKNOWN
    assert report.candidate_terminal_count == 0


def test_runner_decision_ids_count_failure_policy_terminals(tmp_path) -> None:
    store = AttemptStore(tmp_path / "attempts.jsonl")
    store.append(attempt(input_tokens=None, failure_reason="timeout"))
    runner = SimpleNamespace(
        store=store,
        experiment_id="exp",
        client=SimpleNamespace(
            execution_mode="live",
            config=SimpleNamespace(endpoint="api.typesafe.ai/v1/systemone"),
        ),
        failure_policy="reject",
    )

    result = JevRunner._summarise(
        runner,  # type: ignore[arg-type]
        (Decision("candidate", "reject", "failure_policy", failure_reason="jev_failed"),),
        {},
    )

    assert result.cost_report.candidate_terminal_count == 1


def test_unknown_retry_preserves_known_cost_and_token_totals() -> None:
    report = aggregate_cost(
        [
            attempt(
                attempt_id="exp:candidate:a00",
                retry_count=0,
                input_tokens=1_000_000,
            ),
            attempt(
                attempt_id="exp:candidate:a01",
                retry_count=1,
                input_tokens=None,
                failure_reason="timeout",
            ),
        ]
    )

    assert report.status is CostStatus.UNKNOWN
    assert report.reason == "upstream_outcome_uncertain"
    assert report.estimated_usd == Decimal("0.042")
    assert report.display_usd == "0.04200000"
    assert report.input_tokens == 1_000_000


def test_successful_retry_invocations_are_each_priced() -> None:
    report = aggregate_cost(
        [
            attempt(attempt_id="exp:candidate:a00", retry_count=0, input_tokens=1_000_000),
            attempt(attempt_id="exp:candidate:a01", retry_count=1, input_tokens=1_000_000),
        ]
    )

    assert report.status is CostStatus.ESTIMATED
    assert report.estimated_usd == Decimal("0.084")
    assert report.attempts_counted == 2
    assert report.candidate_terminal_count == 1


def test_aggregate_before_display_rounding() -> None:
    report = aggregate_cost(
        [
            attempt(attempt_id="exp:a:a00", candidate_id="a", input_tokens=1),
            attempt(attempt_id="exp:b:a00", candidate_id="b", input_tokens=1),
        ]
    )

    assert report.estimated_usd == Decimal("0.000000084")
    assert report.display_usd == "0.00000008"


def test_mode_report_keeps_mode_labels_separate() -> None:
    reports = report_costs_by_mode(
        [
            attempt(mode="live"),
            attempt(attempt_id="exp:mock:a00", candidate_id="mock", mode="mocked"),
        ]
    )

    assert reports["live"].status is CostStatus.ESTIMATED
    assert reports["live"].mode_label == "hypothetical_direct_provider_estimate"
    assert reports["mocked"].status is CostStatus.MOCKED_LOCAL_ZERO
    assert reports["mocked"].mode_label == "mocked_local_spend"


@pytest.mark.parametrize("http_status", [429, 529])
def test_retry_failures_keep_uncertain_outcome_unknown(http_status: int) -> None:
    report = aggregate_cost(
        [attempt(input_tokens=None, failure_reason="timeout", http_status=http_status)]
    )

    assert report.status is CostStatus.UNKNOWN
    assert report.estimated_usd is None
    assert report.reason == "upstream_outcome_uncertain"


def test_aggregate_persists_token_totals() -> None:
    report = aggregate_cost(
        [
            attempt(attempt_id="exp:a:a00", candidate_id="a", input_tokens=1_000, output_tokens=2),
            attempt(attempt_id="exp:b:a00", candidate_id="b", input_tokens=2_000, output_tokens=3),
        ]
    )

    assert report.input_tokens == 3_000
    assert report.output_tokens == 5


def test_aggregate_rejects_mixed_execution_modes() -> None:
    report = aggregate_cost(
        [attempt(), attempt(attempt_id="exp:mock:a00", candidate_id="mock", mode="mocked")]
    )

    assert report.status is CostStatus.UNKNOWN
    assert report.reason == "mixed_execution_modes"


def test_aggregate_rejects_attempts_from_wrong_execution_mode() -> None:
    report = aggregate_cost([attempt(mode="mocked")], execution_mode="live")

    assert report.status is CostStatus.UNKNOWN
    assert report.reason == "execution_mode_mismatch"


def test_empty_live_report_is_not_labeled_mocked_zero() -> None:
    report = aggregate_cost([], execution_mode="live")

    assert report.status is CostStatus.UNKNOWN
    assert report.estimated_usd is None
    assert report.mode == "live"
    assert report.mode_label == "hypothetical_direct_provider_estimate"


def test_empty_mocked_report_is_labeled_mocked_zero() -> None:
    report = aggregate_cost([], execution_mode="mocked")

    assert report.status is CostStatus.MOCKED_LOCAL_ZERO
    assert report.estimated_usd == Decimal("0")
    assert report.mode == "mocked"


def test_cost_record_persists_pricing_snapshot_metadata() -> None:
    record = aggregate_cost([attempt(input_tokens=1_000)]).to_record()

    assert record["pricing_provider"] == "typesafe"
    assert record["pricing_route"] == "api.typesafe.ai/v1/systemone"
    assert record["pricing_currency"] == "USD"
    assert record["input_usd_per_million"] == "0.042"
    assert record["output_usd_per_million"] == "0"
    assert record["pricing_source_updated"] == "2026-11-03"
    assert record["pricing_checked_at"] == "2026-11-05"
    assert record["pricing_effective_from"] == "UNKNOWN"
    assert record["pricing_source_title"] == "How much does Jev cost?"


def test_runner_result_carries_persisted_cost_report(tmp_path) -> None:
    store = AttemptStore(tmp_path / "attempts.jsonl")
    store.append(attempt(input_tokens=1_000))
    runner = SimpleNamespace(
        store=store,
        experiment_id="exp",
        client=SimpleNamespace(
            execution_mode="live",
            config=SimpleNamespace(endpoint="api.typesafe.ai/v1/systemone"),
        ),
        failure_policy="reject",
    )

    result = JevRunner._summarise(
        runner,  # type: ignore[arg-type]
        (Decision("candidate", "accept", "jev", attempt_id="exp:candidate:a00"),),
        {},
    )

    assert result.cost_report.estimated_usd == Decimal("0.000042")
    assert result.to_record()["cost_report"]["pricing_source_updated"] == "2026-11-03"


def test_runner_without_verified_live_endpoint_is_unknown(tmp_path) -> None:
    store = AttemptStore(tmp_path / "attempts.jsonl")
    store.append(attempt(input_tokens=1_000_000))
    runner = SimpleNamespace(
        store=store,
        experiment_id="exp",
        client=SimpleNamespace(execution_mode="live"),
        failure_policy="reject",
    )

    result = JevRunner._summarise(
        runner,  # type: ignore[arg-type]
        (Decision("candidate", "accept", "jev", attempt_id="exp:candidate:a00"),),
        {},
    )

    assert result.cost_report.status is CostStatus.UNKNOWN
    assert result.cost_report.reason == "unknown_route"


def test_runner_cost_report_ignores_attempts_from_other_execution_mode(tmp_path) -> None:
    store = AttemptStore(tmp_path / "attempts.jsonl")
    store.append(attempt(mode="mocked", input_tokens=1_000_000))
    runner = SimpleNamespace(
        store=store,
        experiment_id="exp",
        client=SimpleNamespace(
            execution_mode="live",
            config=SimpleNamespace(endpoint="api.typesafe.ai/v1/systemone"),
        ),
        failure_policy="reject",
    )

    result = JevRunner._summarise(
        runner,  # type: ignore[arg-type]
        (Decision("candidate", "accept", "jev", attempt_id="exp:candidate:a00"),),
        {},
    )

    assert result.cost_report.status is CostStatus.UNKNOWN
    assert result.cost_report.reason == "no_attempts"


def test_runner_cost_report_ignores_attempts_from_other_experiments(tmp_path) -> None:
    store = AttemptStore(tmp_path / "attempts.jsonl")
    store.append(
        replace(
            attempt(attempt_id="old:historical:a00", input_tokens=1_000_000),
            candidate_id="historical",
            experiment_id="old",
        )
    )
    store.append(
        replace(
            attempt(attempt_id="current:candidate:a00", input_tokens=1_000_000),
            experiment_id="current",
        )
    )
    runner = SimpleNamespace(
        store=store,
        experiment_id="current",
        client=SimpleNamespace(
            execution_mode="live", config=SimpleNamespace(endpoint="api.typesafe.ai/v1/systemone")
        ),
        failure_policy="reject",
    )

    result = JevRunner._summarise(
        runner,  # type: ignore[arg-type]
        (Decision("candidate", "accept", "jev", attempt_id="current:candidate:a00"),),
        {},
    )

    assert result.cost_report.estimated_usd == Decimal("0.042")
    assert result.cost_report.attempts_counted == 1


def test_runner_cost_report_rejects_custom_live_endpoint(tmp_path) -> None:
    store = AttemptStore(tmp_path / "attempts.jsonl")
    store.append(attempt(input_tokens=1_000_000))
    runner = SimpleNamespace(
        store=store,
        experiment_id="exp",
        client=SimpleNamespace(
            execution_mode="live",
            config=SimpleNamespace(endpoint="https://proxy.example/v1/systemone"),
        ),
        failure_policy="reject",
    )

    result = JevRunner._summarise(
        runner,  # type: ignore[arg-type]
        (Decision("candidate", "accept", "jev", attempt_id="exp:candidate:a00"),),
        {},
    )

    assert result.cost_report.status is CostStatus.UNKNOWN
    assert result.cost_report.reason == "unknown_route"


def test_custom_endpoint_route_label_redacts_credentials_and_query() -> None:
    report = aggregate_cost(
        [attempt(input_tokens=1_000)],
        route="https://user:password@proxy.example/v1/systemone?token=secret#fragment",
    )

    assert report.status is CostStatus.UNKNOWN
    assert report.route == "https://proxy.example/v1/systemone"
    record = report.to_record()
    assert record["route"] == "https://proxy.example/v1/systemone"
    assert "password" not in str(record)
    assert "secret" not in str(record)


def test_schemeless_route_label_redacts_userinfo() -> None:
    report = aggregate_cost(
        [attempt(input_tokens=1_000)],
        route="user:password@proxy.example/v1/systemone?token=secret",
    )

    assert "password" not in str(report.to_record())
    assert "secret" not in str(report.to_record())
    assert report.route == "proxy.example/v1/systemone"
