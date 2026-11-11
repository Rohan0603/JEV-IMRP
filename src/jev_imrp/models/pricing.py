"""Source-verified, local-only JEV published-price estimates."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum


class CostStatus(StrEnum):
    KNOWN = "known"
    UNKNOWN = "unknown"
    MOCKED_ZERO = "mocked_zero"


@dataclass(frozen=True)
class PricingSnapshot:
    provider: str
    route: str
    model_id: str
    input_usd_per_million: Decimal
    output_usd_per_million: Decimal
    source_url: str
    source_updated: str
    checked_at: str
    effective_from: str = "UNKNOWN"
    version: str = "typesafe-jev-2026-11-05"
    currency: str = "USD"


@dataclass(frozen=True)
class CostEstimate:
    status: CostStatus
    estimated_usd: Decimal | None
    mode: str
    reason: str | None = None
    attempt_id: str | None = None
    snapshot_version: str = ""
    provider: str = ""
    route: str = ""
    model_id: str = ""
    source_url: str = ""
    formula: str = "input_tokens * input_usd_per_million / 1000000"
    display_usd: str | None = None


@dataclass(frozen=True)
class CostReport:
    status: CostStatus
    estimated_usd: Decimal | None
    display_usd: str | None
    attempt_count: int
    mode: str
    reason: str | None = None
    snapshot_version: str = ""
    source_url: str = ""


def _tokens(value: int | None, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{name} must be a non-negative integer or None")
    return value


def estimate_cost(
    input_tokens: int | None,
    output_tokens: int | None,
    model_id: str,
    mode: str,
    snapshot: PricingSnapshot,
    *,
    attempt_id: str | None = None,
    route: str | None = None,
) -> CostEstimate:
    """Estimate published direct-provider cost without claiming invoice parity."""
    input_tokens = _tokens(input_tokens, "input_tokens")
    _tokens(output_tokens, "output_tokens")
    if mode == "mocked":
        return CostEstimate(
            CostStatus.MOCKED_ZERO,
            Decimal("0"),
            mode,
            attempt_id=attempt_id,
            snapshot_version=snapshot.version,
            provider=snapshot.provider,
            route=snapshot.route,
            model_id=model_id,
            source_url=snapshot.source_url,
            display_usd="0.00000000",
        )
    if mode != "live":
        return CostEstimate(CostStatus.UNKNOWN, None, mode, "unknown_mode", attempt_id)
    if model_id != snapshot.model_id:
        return CostEstimate(
            CostStatus.UNKNOWN,
            None,
            mode,
            "unknown_model",
            attempt_id,
            provider=snapshot.provider,
            route=route or snapshot.route,
            model_id=model_id,
            source_url=snapshot.source_url,
        )
    if route is not None and route != snapshot.route:
        return CostEstimate(
            CostStatus.UNKNOWN,
            None,
            mode,
            "unknown_route",
            attempt_id,
            provider=snapshot.provider,
            route=route,
            model_id=model_id,
            source_url=snapshot.source_url,
        )
    if input_tokens is None:
        return CostEstimate(
            CostStatus.UNKNOWN,
            None,
            mode,
            "missing_input_usage",
            attempt_id,
            provider=snapshot.provider,
            route=snapshot.route,
            model_id=model_id,
            source_url=snapshot.source_url,
        )
    amount = Decimal(input_tokens) * snapshot.input_usd_per_million / Decimal(1_000_000)
    return CostEstimate(
        CostStatus.KNOWN,
        amount,
        mode,
        attempt_id=attempt_id,
        snapshot_version=snapshot.version,
        provider=snapshot.provider,
        route=snapshot.route,
        model_id=model_id,
        source_url=snapshot.source_url,
        display_usd=_display(amount),
    )


def _display(amount: Decimal) -> str:
    return f"{amount.quantize(Decimal('0.00000001'), rounding=ROUND_HALF_UP):.8f}"


def aggregate_costs(estimates: Iterable[CostEstimate]) -> CostReport:
    """Aggregate unique attempt estimates before applying display rounding."""
    unique: dict[str, CostEstimate] = {}
    anonymous: list[CostEstimate] = []
    for estimate in estimates:
        if estimate.attempt_id is None:
            anonymous.append(estimate)
        else:
            unique.setdefault(estimate.attempt_id, estimate)
    values = list(unique.values()) + anonymous
    if not values:
        return CostReport(CostStatus.UNKNOWN, None, None, 0, "unknown", "no_estimates")
    if any(value.status is CostStatus.UNKNOWN for value in values):
        return CostReport(
            CostStatus.UNKNOWN, None, None, len(values), values[0].mode, "unknown_estimate"
        )
    mode = values[0].mode
    amount = sum((value.estimated_usd or Decimal("0") for value in values), Decimal("0"))
    status = (
        CostStatus.MOCKED_ZERO
        if all(value.status is CostStatus.MOCKED_ZERO for value in values)
        else CostStatus.KNOWN
    )
    return CostReport(
        status,
        amount,
        _display(amount),
        len(values),
        mode,
        snapshot_version=values[0].snapshot_version,
        source_url=values[0].source_url,
    )


__all__ = [
    "CostEstimate",
    "CostReport",
    "CostStatus",
    "PricingSnapshot",
    "aggregate_costs",
    "estimate_cost",
]
