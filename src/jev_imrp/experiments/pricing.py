"""Local, source-versioned JEV cost estimates.

This module only prices persisted attempt metadata.  It never calls the provider,
reads credentials, or treats a missing usage field as a zero-cost response.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from typing import Any, Final, TypeGuard
from urllib.parse import urlsplit, urlunsplit

from jev_imrp.models.jev_client import CONFIRMATORY_MODEL_ID, ENDPOINT, Attempt

PRICING_SNAPSHOT_VERSION: Final = "typesafe-jev-2026-11-05"
PROVIDER: Final = "typesafe"
JEV_ROUTE: Final = "api.typesafe.ai/v1/systemone"
CURRENCY: Final = "USD"
SOURCE_URL: Final = "https://help.typesafe.ai/articles/5187202169-how-much-does-jev-cost"
SOURCE_UPDATED: Final = "2026-11-03"
CHECKED_AT: Final = "2026-11-05"
EFFECTIVE_FROM: Final = "UNKNOWN"
INPUT_USD_PER_MILLION: Final = Decimal("0.042")
OUTPUT_USD_PER_MILLION: Final = Decimal("0")
_MILLION: Final = Decimal("1000000")
_EIGHT_PLACES: Final = Decimal("0.00000001")


class CostStatus(StrEnum):
    ESTIMATED = "ESTIMATED"
    MOCKED_LOCAL_ZERO = "MOCKED_LOCAL_ZERO"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class PricingSnapshot:
    provider: str = PROVIDER
    route: str = JEV_ROUTE
    model_id: str = CONFIRMATORY_MODEL_ID
    currency: str = CURRENCY
    input_usd_per_million: Decimal = INPUT_USD_PER_MILLION
    output_usd_per_million: Decimal = OUTPUT_USD_PER_MILLION
    source_url: str = SOURCE_URL
    source_updated: str = SOURCE_UPDATED
    checked_at: str = CHECKED_AT
    effective_from: str = EFFECTIVE_FROM
    version: str = PRICING_SNAPSHOT_VERSION


DEFAULT_PRICING_SNAPSHOT: Final = PricingSnapshot()


@dataclass(frozen=True)
class CostEstimate:
    estimated_usd: Decimal | None
    display_usd: str | None
    status: CostStatus
    reason: str | None
    mode: str
    mode_label: str
    model_id: str
    route: str
    input_tokens: int | None
    output_tokens: int | None
    output_cost_usd: Decimal | None
    missing_output_usage: bool
    snapshot: PricingSnapshot

    def to_record(self) -> dict[str, Any]:
        return {
            "estimated_usd": str(self.estimated_usd) if self.estimated_usd is not None else None,
            "display_usd": self.display_usd,
            "cost_status": self.status.value,
            "reason": self.reason,
            "execution_mode": self.mode,
            "mode_label": self.mode_label,
            "model_id": self.model_id,
            "route": self.route,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "output_cost_usd": (
                str(self.output_cost_usd) if self.output_cost_usd is not None else None
            ),
            "missing_output_usage": self.missing_output_usage,
            "pricing_snapshot_version": self.snapshot.version,
            "pricing_provider": self.snapshot.provider,
            "pricing_route": self.snapshot.route,
            "pricing_currency": self.snapshot.currency,
            "input_usd_per_million": str(self.snapshot.input_usd_per_million),
            "output_usd_per_million": str(self.snapshot.output_usd_per_million),
            "pricing_source": self.snapshot.source_url,
            "pricing_source_updated": self.snapshot.source_updated,
            "pricing_checked_at": self.snapshot.checked_at,
            "pricing_effective_from": self.snapshot.effective_from,
            "formula": "input_tokens * input_usd_per_million / 1000000",
        }


@dataclass(frozen=True)
class CostReport(CostEstimate):
    attempts_considered: int = 0
    attempts_counted: int = 0
    duplicate_attempts: int = 0

    def to_record(self) -> dict[str, Any]:
        return {
            **super().to_record(),
            "attempts_considered": self.attempts_considered,
            "attempts_counted": self.attempts_counted,
            "duplicate_attempts": self.duplicate_attempts,
        }


def _canonical_route(route: str) -> str:
    if route == ENDPOINT:
        return JEV_ROUTE
    return route.rstrip("/")


def _safe_route_label(route: str) -> str:
    """Keep persisted route labels free of URL credentials and query secrets."""
    parts = urlsplit(route)
    if not parts.scheme or not parts.netloc:
        return route.split("?", 1)[0].split("#", 1)[0].rstrip("/")
    try:
        host = parts.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parts.port is not None:
            host = f"{host}:{parts.port}"
    except ValueError:
        return "<redacted-invalid-route>"
    return urlunsplit((parts.scheme, host, parts.path.rstrip("/"), "", ""))


def _valid_tokens(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _display(value: Decimal) -> str:
    return format(value.quantize(_EIGHT_PLACES, rounding=ROUND_HALF_UP), ".8f")


def estimate_attempt_cost(
    attempt: Attempt,
    *,
    route: str = JEV_ROUTE,
    snapshot: PricingSnapshot = DEFAULT_PRICING_SNAPSHOT,
) -> CostEstimate:
    """Estimate one terminal attempt from local metadata only."""
    canonical_route = _canonical_route(route)
    safe_route = _safe_route_label(canonical_route)
    mode_label = (
        "mocked_local_spend"
        if attempt.execution_mode == "mocked"
        else "hypothetical_direct_provider_estimate"
    )
    base: dict[str, Any] = {
        "mode": attempt.execution_mode,
        "mode_label": mode_label,
        "model_id": attempt.requested_model_id,
        "route": safe_route,
        "input_tokens": attempt.input_tokens,
        "output_tokens": attempt.output_tokens,
        "missing_output_usage": attempt.output_tokens is None,
        "snapshot": snapshot,
    }
    if attempt.execution_mode == "mocked":
        return CostEstimate(
            estimated_usd=Decimal("0"),
            display_usd="0.00000000",
            status=CostStatus.MOCKED_LOCAL_ZERO,
            reason="mocked_execution_not_provider_billing",
            output_cost_usd=Decimal("0"),
            **base,
        )
    if canonical_route != snapshot.route:
        return CostEstimate(
            None, None, CostStatus.UNKNOWN, "unknown_route", output_cost_usd=None, **base
        )
    if attempt.requested_model_id != snapshot.model_id or attempt.returned_model_id not in (
        None,
        snapshot.model_id,
    ):
        return CostEstimate(
            None, None, CostStatus.UNKNOWN, "unknown_model", output_cost_usd=None, **base
        )
    if attempt.failure_reason is not None:
        return CostEstimate(
            None,
            None,
            CostStatus.UNKNOWN,
            "upstream_outcome_uncertain",
            output_cost_usd=None,
            **base,
        )
    if not _valid_tokens(attempt.input_tokens):
        return CostEstimate(
            None,
            None,
            CostStatus.UNKNOWN,
            "input_usage_missing_or_invalid",
            output_cost_usd=None,
            **base,
        )

    input_cost = Decimal(attempt.input_tokens) * snapshot.input_usd_per_million / _MILLION
    return CostEstimate(
        estimated_usd=input_cost,
        display_usd=_display(input_cost),
        status=CostStatus.ESTIMATED,
        reason=None,
        output_cost_usd=Decimal("0"),
        **base,
    )


def _terminal_attempts(attempts: Iterable[Attempt]) -> tuple[list[Attempt], int, int]:
    unique: dict[str, Attempt] = {}
    duplicate_count = 0
    for item in attempts:
        if item.attempt_id in unique:
            duplicate_count += 1
            continue
        unique[item.attempt_id] = item
    by_candidate: dict[str, Attempt] = {}
    for item in unique.values():
        prior = by_candidate.get(item.candidate_id)
        if prior is None or (item.retry_count, item.attempt_id) > (
            prior.retry_count,
            prior.attempt_id,
        ):
            by_candidate[item.candidate_id] = item
    return list(by_candidate.values()), len(unique), duplicate_count


def aggregate_cost(
    attempts: Iterable[Attempt],
    *,
    route: str = JEV_ROUTE,
    snapshot: PricingSnapshot = DEFAULT_PRICING_SNAPSHOT,
) -> CostReport:
    """Aggregate terminal attempts without per-attempt display rounding."""
    terminal, unique_count, duplicate_count = _terminal_attempts(attempts)
    aggregate_input_tokens: int | None = None
    if terminal:
        input_values: list[int] = []
        for item in terminal:
            if not _valid_tokens(item.input_tokens):
                break
            input_values.append(item.input_tokens)
        else:
            aggregate_input_tokens = sum(input_values)

    aggregate_output_tokens: int | None = None
    if terminal:
        output_values: list[int] = []
        for item in terminal:
            if not _valid_tokens(item.output_tokens):
                break
            output_values.append(item.output_tokens)
        else:
            aggregate_output_tokens = sum(output_values)
    if not terminal:
        estimate = CostEstimate(
            Decimal("0"),
            "0.00000000",
            CostStatus.MOCKED_LOCAL_ZERO,
            "no_attempts",
            "unknown",
            "no_attempts",
            "",
            _canonical_route(route),
            aggregate_input_tokens,
            aggregate_output_tokens,
            Decimal("0"),
            False,
            snapshot,
        )
    else:
        estimates = [
            estimate_attempt_cost(item, route=route, snapshot=snapshot) for item in terminal
        ]
        modes = {item.mode for item in estimates}
        if len(modes) > 1:
            estimate = CostEstimate(
                None,
                None,
                CostStatus.UNKNOWN,
                "mixed_execution_modes",
                "mixed",
                "mixed_execution_modes",
                snapshot.model_id,
                snapshot.route,
                aggregate_input_tokens,
                aggregate_output_tokens,
                None,
                any(item.missing_output_usage for item in estimates),
                snapshot,
            )
        elif modes == {"mocked"}:
            estimate = CostEstimate(
                Decimal("0"),
                "0.00000000",
                CostStatus.MOCKED_LOCAL_ZERO,
                "mocked_execution_not_provider_billing",
                "mocked",
                "mocked_local_spend",
                snapshot.model_id,
                snapshot.route,
                aggregate_input_tokens,
                aggregate_output_tokens,
                Decimal("0"),
                False,
                snapshot,
            )
        elif any(item.status is CostStatus.UNKNOWN for item in estimates):
            first_unknown = next(item for item in estimates if item.status is CostStatus.UNKNOWN)
            estimate = CostEstimate(
                None,
                None,
                CostStatus.UNKNOWN,
                first_unknown.reason,
                first_unknown.mode,
                first_unknown.mode_label,
                first_unknown.model_id,
                first_unknown.route,
                aggregate_input_tokens,
                aggregate_output_tokens,
                None,
                any(item.missing_output_usage for item in estimates),
                snapshot,
            )
        else:
            total = sum((item.estimated_usd or Decimal("0") for item in estimates), Decimal("0"))
            estimate = CostEstimate(
                total,
                _display(total),
                CostStatus.ESTIMATED,
                None,
                "live",
                "hypothetical_direct_provider_estimate",
                snapshot.model_id,
                snapshot.route,
                aggregate_input_tokens,
                aggregate_output_tokens,
                Decimal("0"),
                any(item.missing_output_usage for item in estimates),
                snapshot,
            )
    return CostReport(
        **estimate.__dict__,
        attempts_considered=unique_count + duplicate_count,
        attempts_counted=len(terminal),
        duplicate_attempts=duplicate_count,
    )


def report_costs_by_mode(
    attempts: Iterable[Attempt],
    *,
    route: str = JEV_ROUTE,
    snapshot: PricingSnapshot = DEFAULT_PRICING_SNAPSHOT,
) -> Mapping[str, CostReport]:
    """Return separately labeled reports for mocked and live executions."""
    grouped: dict[str, list[Attempt]] = defaultdict(list)
    for item in attempts:
        grouped[item.execution_mode].append(item)
    return {
        mode: aggregate_cost(items, route=route, snapshot=snapshot)
        for mode, items in grouped.items()
    }


__all__ = [
    "CHECKED_AT",
    "DEFAULT_PRICING_SNAPSHOT",
    "EFFECTIVE_FROM",
    "INPUT_USD_PER_MILLION",
    "JEV_ROUTE",
    "OUTPUT_USD_PER_MILLION",
    "PRICING_SNAPSHOT_VERSION",
    "SOURCE_UPDATED",
    "SOURCE_URL",
    "CostEstimate",
    "CostReport",
    "CostStatus",
    "PricingSnapshot",
    "aggregate_cost",
    "estimate_attempt_cost",
    "report_costs_by_mode",
]
