"""Fixed-horizon forward labels (plan section 8.6).

A label is a cost-adjusted *execution* outcome, not a price difference: it runs
from the defined entry FILL to the exit FILL under the execution policy, charges
both legs, and stores every component.

Primary horizon is 30 minutes; 5/15/60 minutes and EOD are secondary outcomes
only and are never a hidden way to select a result (PREREGISTRATION.md section 2).

Short-market adjustment (plan 8.6): this module deliberately does NOT subtract an
index return from a side-signed trade return. ``net_return`` is the raw,
side-signed, cost-adjusted return of the trade itself. A beta-adjusted or
benchmark-relative measure is a *portfolio*-level construct and must be
estimated on training-only data with an explicit side/exposure convention;
until then, subtracting the index here would silently mix conventions.

Every field is computed explicitly in Python. Nothing relies on database update
semantics for columns written in the same statement, and nothing depends on
wall-clock time, randomness or dict iteration order: the same input data plus
the same config versions always yield byte-identical labels.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any, cast

from jev_imrp.common.config import is_unresolved
from jev_imrp.common.timeutils import SESSION_CLOSE, to_ist
from jev_imrp.execution.cost_model import CostScheduleError, FeeSchedule, SlippageScenario
from jev_imrp.execution.fills import (
    Fill,
    FillPolicy,
    FillStatus,
    Leg,
    Quote,
    UnfillableReason,
    simulate_fill,
)
from jev_imrp.execution.portfolio import realise_trade

#: Bumped whenever the label definition changes; stored on every label.
LABEL_VERSION = "fwd_v1"

PRIMARY_HORIZON_MINUTES = 30
SECONDARY_HORIZON_MINUTES = (5, 15, 60)


class LabelStatus(StrEnum):
    LABELLED = "labelled"
    UNLABELLED = "unlabelled"


class UnlabelledReason(StrEnum):
    """Predeclared reasons an observation gets no label (policy: never invented)."""

    ENTRY_UNFILLABLE = "entry_unfillable"
    EXIT_UNFILLABLE = "exit_unfillable"
    HORIZON_BEYOND_SESSION = "horizon_beyond_session"
    MISSING_FUTURE_DATA = "missing_future_data"
    ENTRY_AFTER_SESSION_CLOSE = "entry_after_session_close"


@dataclass(frozen=True, slots=True)
class LabelRecord:
    """One candidate's forward outcome, labelled or explicitly not labelled.

    When ``status`` is ``UNLABELLED`` every numeric outcome field is ``None`` and
    ``reason`` says why. No partial or zero-filled label is ever written.
    """

    symbol: str
    position_side: str
    quantity: int
    horizon_minutes: int | None  # None == EOD (intraday flat by session close)
    is_primary_horizon: bool
    decision_time: datetime
    entry_arrival_time: datetime | None
    label_interval_start: datetime | None
    label_interval_end: datetime | None
    label_available_at: datetime | None
    status: LabelStatus
    reason: UnfillableReason | UnlabelledReason | None
    entry_fill: Fill | None
    exit_fill: Fill | None
    gross_pnl_inr: Decimal | None
    raw_price_return: Decimal | None
    entry_fees: Mapping[str, Decimal] | None
    exit_fees: Mapping[str, Decimal] | None
    fees_total_inr: Decimal | None
    slippage_and_impact_inr: Decimal | None
    net_pnl_inr: Decimal | None
    net_return: Decimal | None
    net_bps: Decimal | None
    defined_entry_notional_inr: Decimal | None
    label_version: str = LABEL_VERSION
    cost_schedule_version: str = ""
    policy_version: str = ""
    cost_scenario: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "position_side": self.position_side,
            "quantity": self.quantity,
            "horizon_minutes": self.horizon_minutes,
            "is_primary_horizon": self.is_primary_horizon,
            "decision_time": self.decision_time,
            "entry_arrival_time": self.entry_arrival_time,
            "label_interval_start": self.label_interval_start,
            "label_interval_end": self.label_interval_end,
            "label_available_at": self.label_available_at,
            "status": str(self.status),
            "reason": self.reason,
            "entry_fill": self.entry_fill.as_dict() if self.entry_fill else None,
            "exit_fill": self.exit_fill.as_dict() if self.exit_fill else None,
            "gross_pnl_inr": self.gross_pnl_inr,
            "raw_price_return": self.raw_price_return,
            "entry_fees": dict(self.entry_fees) if self.entry_fees else None,
            "exit_fees": dict(self.exit_fees) if self.exit_fees else None,
            "fees_total_inr": self.fees_total_inr,
            "slippage_and_impact_inr": self.slippage_and_impact_inr,
            "net_pnl_inr": self.net_pnl_inr,
            "net_return": self.net_return,
            "net_bps": self.net_bps,
            "defined_entry_notional_inr": self.defined_entry_notional_inr,
            "label_version": self.label_version,
            "cost_schedule_version": self.cost_schedule_version,
            "policy_version": self.policy_version,
            "cost_scenario": self.cost_scenario,
        }


@dataclass(frozen=True, slots=True)
class LabelerConfig:
    """Labeller settings. Every TBD value must be frozen before data inspection."""

    policy: FillPolicy
    schedule: FeeSchedule
    policy_version: str = "v1"
    slippage: SlippageScenario | None = None
    include_ipft: bool | None = None
    ipft_in_gst_base: bool | None = None
    horizons_minutes: tuple[int, ...] = (PRIMARY_HORIZON_MINUTES, *SECONDARY_HORIZON_MINUTES)
    primary_horizon_minutes: int = PRIMARY_HORIZON_MINUTES
    include_eod: bool = True

    @classmethod
    def from_policy(
        cls,
        policy_doc: Mapping[str, Any],
        schedule: FeeSchedule,
        *,
        slippage: SlippageScenario | None = None,
    ) -> LabelerConfig:
        exit_section = policy_doc.get("exit")
        if not isinstance(exit_section, dict):
            raise ValueError("execution policy has no 'exit' section")
        primary = exit_section.get("primary_horizon_minutes")
        secondary = exit_section.get("secondary_horizon_minutes") or ()
        unresolved = sorted(
            name for name, value in (("primary_horizon_minutes", primary),) if is_unresolved(value)
        )
        if unresolved:
            raise ValueError(f"execution policy has unresolved settings {unresolved}")
        primary_horizon = int(cast(int, primary))
        horizons = (primary_horizon, *(int(h) for h in secondary))
        return cls(
            policy=FillPolicy.from_config(policy_doc),
            schedule=schedule,
            policy_version=str(policy_doc.get("policy_version", "unknown")),
            slippage=slippage,
            horizons_minutes=horizons,
            primary_horizon_minutes=primary_horizon,
            include_eod=bool(exit_section.get("eod_exit")),
        )


def _unlabelled(
    *,
    symbol: str,
    position_side: str,
    quantity: int,
    horizon_minutes: int | None,
    decision_time: datetime,
    reason: UnlabelledReason | UnfillableReason,
    config: LabelerConfig,
    entry_fill: Fill | None = None,
    exit_fill: Fill | None = None,
    entry_arrival: datetime | None = None,
) -> LabelRecord:
    return LabelRecord(
        symbol=symbol,
        position_side=position_side,
        quantity=quantity,
        horizon_minutes=horizon_minutes,
        is_primary_horizon=horizon_minutes == config.primary_horizon_minutes,
        decision_time=decision_time,
        entry_arrival_time=entry_arrival,
        label_interval_start=entry_arrival,
        label_interval_end=None,
        label_available_at=None,
        status=LabelStatus.UNLABELLED,
        reason=reason,
        entry_fill=entry_fill,
        exit_fill=exit_fill,
        gross_pnl_inr=None,
        raw_price_return=None,
        entry_fees=None,
        exit_fees=None,
        fees_total_inr=None,
        slippage_and_impact_inr=None,
        net_pnl_inr=None,
        net_return=None,
        net_bps=None,
        defined_entry_notional_inr=None,
        cost_schedule_version=config.schedule.version,
        policy_version=config.policy_version,
        cost_scenario=config.slippage.name if config.slippage else "",
    )


def label_candidate(
    *,
    symbol: str,
    position_side: str,
    quantity: int,
    decision_time: datetime,
    horizon_minutes: int | None,
    quotes: Sequence[Quote] | Mapping[datetime, Quote],
    config: LabelerConfig,
) -> LabelRecord:
    """Label one candidate from entry fill to exit fill under the execution policy.

    ``horizon_minutes=None`` means EOD (intraday flat at session close).

    Policy decisions, applied without exceptions:

    * an unusable entry or exit quote yields UNLABELLED with the fill's reason -
      no mid-price, last-price or zero substitution (plan 8.3);
    * a fixed horizon whose exit lands after the session close yields UNLABELLED
      ``HORIZON_BEYOND_SESSION``. The strategy's own square-off convention would
      decide otherwise, so this stays a recorded reason rather than a guess;
    * a data gap makes the quote preceding the exit stale, so it yields UNLABELLED
      ``EXIT_UNFILLABLE`` with reason ``STALE_QUOTE`` / ``MISSING_TOUCHLINE``;
    * ``label_available_at`` is the exit arrival: the instant the outcome is fully
      known, which is what purge and embargo logic later needs.
    """
    if horizon_minutes is not None and horizon_minutes <= 0:
        raise ValueError("horizon_minutes must be positive, or None for EOD")
    position_side = position_side.lower()
    if position_side not in ("long", "short"):
        raise ValueError("position_side must be 'long' or 'short'")

    local = to_ist(decision_time)
    session_day: date = local.date()
    session_close = datetime.combine(session_day, SESSION_CLOSE, tzinfo=local.tzinfo)

    entry = simulate_fill(
        decision_time=decision_time,
        position_side=position_side,
        leg=Leg.ENTRY,
        quantity=quantity,
        quotes=quotes,
        policy=config.policy,
    )
    if entry.status is not FillStatus.FILLED or entry.price is None:
        return _unlabelled(
            symbol=symbol,
            position_side=position_side,
            quantity=quantity,
            horizon_minutes=horizon_minutes,
            decision_time=decision_time,
            reason=UnlabelledReason.ENTRY_UNFILLABLE,
            config=config,
            entry_fill=entry,
        )

    entry_arrival = entry.arrival_time
    if entry_arrival > session_close:
        return _unlabelled(
            symbol=symbol,
            position_side=position_side,
            quantity=quantity,
            horizon_minutes=horizon_minutes,
            decision_time=decision_time,
            reason=UnlabelledReason.ENTRY_AFTER_SESSION_CLOSE,
            config=config,
            entry_fill=entry,
            entry_arrival=entry_arrival,
        )

    if horizon_minutes is None:
        exit_decision_time = session_close
    else:
        exit_decision_time = entry_arrival + timedelta(minutes=horizon_minutes)
        if exit_decision_time > session_close:
            return _unlabelled(
                symbol=symbol,
                position_side=position_side,
                quantity=quantity,
                horizon_minutes=horizon_minutes,
                decision_time=decision_time,
                reason=UnlabelledReason.HORIZON_BEYOND_SESSION,
                config=config,
                entry_fill=entry,
                entry_arrival=entry_arrival,
            )

    stream = list(quotes.values()) if isinstance(quotes, Mapping) else list(quotes)
    exit_fill = simulate_fill(
        decision_time=exit_decision_time,
        position_side=position_side,
        leg=Leg.EXIT,
        quantity=quantity,
        quotes=stream,
        policy=config.policy,
    )
    if exit_fill.status is not FillStatus.FILLED or exit_fill.price is None:
        reason = (
            UnlabelledReason.MISSING_FUTURE_DATA
            if exit_fill.reason
            in (UnfillableReason.MISSING_QUOTE, UnfillableReason.NO_QUOTE_AT_OR_BEFORE_ARRIVAL)
            else UnlabelledReason.EXIT_UNFILLABLE
        )
        return _unlabelled(
            symbol=symbol,
            position_side=position_side,
            quantity=quantity,
            horizon_minutes=horizon_minutes,
            decision_time=decision_time,
            reason=reason,
            config=config,
            entry_fill=entry,
            exit_fill=exit_fill,
            entry_arrival=entry_arrival,
        )

    try:
        entry_fees = config.schedule.leg_fees(
            turnover_inr=entry.turnover_inr,
            side=str(entry.order_side),
            on=session_day,
            include_ipft=config.include_ipft,
            ipft_in_gst_base=config.ipft_in_gst_base,
        )
        exit_fees = config.schedule.leg_fees(
            turnover_inr=exit_fill.turnover_inr,
            side=str(exit_fill.order_side),
            on=session_day,
            include_ipft=config.include_ipft,
            ipft_in_gst_base=config.ipft_in_gst_base,
        )
    except CostScheduleError:  # unresolvable dated rate: no label, no guess
        return _unlabelled(
            symbol=symbol,
            position_side=position_side,
            quantity=quantity,
            horizon_minutes=horizon_minutes,
            decision_time=decision_time,
            reason=UnlabelledReason.MISSING_FUTURE_DATA,
            config=config,
            entry_fill=entry,
            exit_fill=exit_fill,
            entry_arrival=entry_arrival,
        )

    slippage = Decimal(0)
    if config.slippage is not None:
        scenario = config.slippage
        slippage = scenario.amount_for_leg(entry.turnover_inr) + scenario.amount_for_leg(
            exit_fill.turnover_inr
        )

    trade = realise_trade(
        position_side=position_side,
        quantity=quantity,
        entry_fill=entry,
        exit_fill=exit_fill,
        entry_fees=entry_fees,
        exit_fees=exit_fees,
        slippage_and_impact_inr=slippage,
    )
    entry_notional = trade.defined_entry_notional
    raw_return = trade.gross_pnl_inr / entry_notional

    return LabelRecord(
        symbol=symbol,
        position_side=position_side,
        quantity=quantity,
        horizon_minutes=horizon_minutes,
        is_primary_horizon=horizon_minutes == config.primary_horizon_minutes,
        decision_time=decision_time,
        entry_arrival_time=entry_arrival,
        label_interval_start=entry_arrival,
        label_interval_end=exit_fill.arrival_time,
        label_available_at=exit_fill.arrival_time,
        status=LabelStatus.LABELLED,
        reason=None,
        entry_fill=entry,
        exit_fill=exit_fill,
        gross_pnl_inr=trade.gross_pnl_inr,
        raw_price_return=raw_return,
        entry_fees=entry_fees.as_dict(),
        exit_fees=exit_fees.as_dict(),
        fees_total_inr=trade.fees_total_inr,
        slippage_and_impact_inr=slippage,
        net_pnl_inr=trade.net_pnl_inr,
        net_return=trade.net_pnl_inr / entry_notional,
        net_bps=trade.net_bps,
        defined_entry_notional_inr=entry_notional,
        cost_schedule_version=config.schedule.version,
        policy_version=config.policy_version,
        cost_scenario=config.slippage.name if config.slippage else "",
    )


__all__ = [
    "LABEL_VERSION",
    "PRIMARY_HORIZON_MINUTES",
    "SECONDARY_HORIZON_MINUTES",
    "LabelRecord",
    "LabelStatus",
    "LabelerConfig",
    "UnlabelledReason",
    "label_candidate",
]
