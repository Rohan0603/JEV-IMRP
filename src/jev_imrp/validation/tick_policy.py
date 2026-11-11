"""Effective-dated NSE cash-equity tick policy and evidence resolver."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


class TickPolicyError(ValueError):
    """Tick policy or effective-dated evidence is unusable."""


@dataclass(frozen=True, slots=True)
class TickEvidence:
    symbol: str
    series: str
    effective_from: date
    effective_to: date | None
    tick_size: Decimal
    source: str
    source_sha256: str


def nse_cash_tick_from_price(price: Decimal) -> Decimal:
    """Return the published generic NSE cash price-band tick size."""

    if price <= 0:
        raise TickPolicyError("reference price must be positive")
    if price < Decimal("250"):
        return Decimal("0.01")
    if price <= Decimal("1000"):
        return Decimal("0.05")
    if price <= Decimal("5000"):
        return Decimal("0.10")
    if price <= Decimal("10000"):
        return Decimal("0.50")
    if price <= Decimal("20000"):
        return Decimal("1.00")
    return Decimal("5.00")


def resolve_effective_tick(
    records: list[TickEvidence], symbol: str, as_of: date
) -> Decimal:
    """Resolve one verified EQ tick record, failing closed on ambiguity."""

    candidates = [
        record
        for record in records
        if record.symbol == symbol
        and record.series == "EQ"
        and record.effective_from <= as_of
        and (record.effective_to is None or as_of < record.effective_to)
    ]
    if not candidates:
        raise TickPolicyError(f"no effective tick evidence for {symbol} on {as_of}")
    if len(candidates) != 1:
        raise TickPolicyError(f"ambiguous effective tick evidence for {symbol} on {as_of}")
    record = candidates[0]
    if not record.source or len(record.source_sha256) != 64:
        raise TickPolicyError("tick evidence source hash is missing or invalid")
    try:
        int(record.source_sha256, 16)
    except ValueError as exc:
        raise TickPolicyError("tick evidence source hash is missing or invalid") from exc
    if record.tick_size <= 0:
        raise TickPolicyError("tick evidence size must be positive")
    return record.tick_size


__all__ = [
    "TickEvidence",
    "TickPolicyError",
    "nse_cash_tick_from_price",
    "resolve_effective_tick",
]
