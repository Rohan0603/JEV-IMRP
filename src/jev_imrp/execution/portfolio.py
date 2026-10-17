"""Round-trip portfolio accounting (plan section 8.4).

One executed position is two executed legs. Statutory fees are charged per leg,
kept in rupees, and only then normalised:

    signed_gross_pnl = (exit_price - entry_price) * quantity   # long
                      = (entry_price - exit_price) * quantity   # short
    net_pnl = signed_gross_pnl - sum(all fee components) - modelled_slippage_and_impact
    net_bps = 10000 * net_pnl / defined_entry_notional

**Defined entry notional (the denominator, fixed and documented):** the entry
leg's turnover, ``entry_fill_price * quantity``, i.e. the rupees actually
committed at the entry fill under the execution policy. It is a property of the
position, not of the outcome, so the denominator cannot move with the exit.

A one-sided ``cost_bps`` is never subtracted from a round-trip return: both legs
are charged and summed.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from jev_imrp.execution.cost_model import LegFees
from jev_imrp.execution.fills import Fill, FillStatus

BPS_SCALE: Decimal = Decimal(10_000)


@dataclass(frozen=True, slots=True)
class TradeResult:
    """A completed round trip with every cost component preserved."""

    position_side: str
    quantity: int
    entry_price: Decimal
    exit_price: Decimal
    entry_turnover_inr: Decimal
    exit_turnover_inr: Decimal
    entry_fees: LegFees
    exit_fees: LegFees
    slippage_and_impact_inr: Decimal
    gross_pnl_inr: Decimal
    fees_total_inr: Decimal
    net_pnl_inr: Decimal
    net_bps: Decimal

    @property
    def defined_entry_notional(self) -> Decimal:
        """The fixed bps denominator: entry fill turnover."""
        return self.entry_turnover_inr

    def as_dict(self) -> dict[str, Any]:
        return {
            "position_side": self.position_side,
            "quantity": self.quantity,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "defined_entry_notional_inr": self.defined_entry_notional,
            "entry_fees": self.entry_fees.as_dict(),
            "exit_fees": self.exit_fees.as_dict(),
            "fees_total_inr": self.fees_total_inr,
            "slippage_and_impact_inr": self.slippage_and_impact_inr,
            "gross_pnl_inr": self.gross_pnl_inr,
            "net_pnl_inr": self.net_pnl_inr,
            "net_bps": self.net_bps,
        }


def signed_gross_pnl(*, position_side: str, quantity: int, entry_price: Decimal,
                      exit_price: Decimal) -> Decimal:
    """Gross P&L in rupees, signed by the position side."""
    side = position_side.lower()
    if side == "long":
        return (exit_price - entry_price) * quantity
    if side == "short":
        return (entry_price - exit_price) * quantity
    raise ValueError(f"position_side must be 'long' or 'short', got {position_side!r}")


def realise_trade(
    *,
    position_side: str,
    quantity: int,
    entry_fill: Fill,
    exit_fill: Fill,
    entry_fees: LegFees,
    exit_fees: LegFees,
    slippage_and_impact_inr: Decimal = Decimal(0),
) -> TradeResult:
    """Assemble one round trip from two filled legs.

    Refuses unfillable legs: a failed fill is a skipped trade recorded with a
    reason (policy ``failed_fills: skipped_trades_and_reported``), never a zero
    price and never a silent execution loss.
    """
    for name, fill in (("entry", entry_fill), ("exit", exit_fill)):
        if fill.status is not FillStatus.FILLED or fill.price is None:
            raise ValueError(
                f"{name} leg is unfillable ({fill.reason}); cannot realise a round trip"
            )
    entry_price = entry_fill.price
    exit_price = exit_fill.price
    assert entry_price is not None and exit_price is not None  # narrowed by the check above

    gross = signed_gross_pnl(
        position_side=position_side,
        quantity=quantity,
        entry_price=entry_price,
        exit_price=exit_price,
    )
    fees_total = entry_fees.total + exit_fees.total
    entry_turnover = entry_price * quantity
    net = gross - fees_total - slippage_and_impact_inr
    if entry_turnover <= 0:
        raise ValueError("defined entry notional must be positive")
    return TradeResult(
        position_side=position_side.lower(),
        quantity=quantity,
        entry_price=entry_price,
        exit_price=exit_price,
        entry_turnover_inr=entry_turnover,
        exit_turnover_inr=exit_price * quantity,
        entry_fees=entry_fees,
        exit_fees=exit_fees,
        slippage_and_impact_inr=slippage_and_impact_inr,
        gross_pnl_inr=gross,
        fees_total_inr=fees_total,
        net_pnl_inr=net,
        net_bps=BPS_SCALE * net / entry_turnover,
    )


__all__ = ["BPS_SCALE", "TradeResult", "realise_trade", "signed_gross_pnl"]