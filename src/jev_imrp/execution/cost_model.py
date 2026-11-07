"""Per-leg statutory cost engine (plan section 8.4).

Every charge is calculated in rupees, per executed leg, before any
normalisation. The rules encoded here are exactly the ones declared in
``config/cost_schedule.yaml``:

  brokerage        min(percent * turnover, Rs 5 per executed order)
  STT              percent of turnover, sell side only (equity intraday)
  exchange txn fee percent of turnover per side, versioned by effective date
  SEBI turnover    Rs 10 per crore of turnover (absolute, not percent)
  stamp duty       BUY side only, min(percent * turnover, Rs 300 per crore)
  GST              18% of (brokerage + SEBI + exchange transaction fee) only
  IPFT             Rs 0.01 per crore, only when the schedule enables it; its GST
                   treatment is UNVERIFIED_CONTRACT_NOTE_CHECK and is therefore
                   excluded from the GST base unless a caller explicitly opts in.

Brokerage is deliberately *not* a constant bps rate: the Rs 5 cap makes the
effective rate notional-dependent, so entry and exit legs are always charged
separately and never approximated by a round-trip rate.

Arithmetic uses ``decimal.Decimal``. Rupee amounts must reconcile EXACTLY
against hand-worked contract-note examples (plan section 11.2), which floating
point cannot guarantee.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Final

from jev_imrp.common.config import config_path, is_unresolved, load_yaml
from jev_imrp.common.errors import ConfigError

CRORE: Final[Decimal] = Decimal(10_000_000)
ZERO: Final[Decimal] = Decimal(0)


def _dec(value: Any) -> Decimal:
    """Exact Decimal from YAML scalars (floats go through their repr, not binary)."""
    return Decimal(str(value))


class CostScheduleError(ValueError):
    """Raised when the cost schedule cannot answer a request exactly."""


@dataclass(frozen=True, slots=True)
class LegFees:
    """Statutory charges in rupees for ONE executed leg.

    ``total`` is the exact sum of the components; it is never recomputed from a
    rounded bps approximation.
    """

    brokerage: Decimal
    stt: Decimal
    exchange_transaction_fee: Decimal
    sebi_turnover_fee: Decimal
    stamp_duty: Decimal
    gst: Decimal
    ipft: Decimal

    @property
    def total(self) -> Decimal:
        return sum(self._all(), ZERO)

    def _all(self) -> tuple[Decimal, ...]:
        return (
            self.brokerage,
            self.stt,
            self.exchange_transaction_fee,
            self.sebi_turnover_fee,
            self.stamp_duty,
            self.gst,
            self.ipft,
        )

    def as_dict(self) -> dict[str, Decimal]:
        return {
            "brokerage": self.brokerage,
            "stt": self.stt,
            "exchange_transaction_fee": self.exchange_transaction_fee,
            "sebi_turnover_fee": self.sebi_turnover_fee,
            "stamp_duty": self.stamp_duty,
            "gst": self.gst,
            "ipft": self.ipft,
            "total": self.total,
        }


@dataclass(frozen=True, slots=True)
class SlippageScenario:
    """Modelled slippage and impact, kept strictly separate from statutory fees.

    Plan 8.5 declares three scenarios (base, stress 1, stress 2). Their numeric
    parameters are still ``TBD`` in ``config/cost_schedule.yaml`` and MUST NOT be
    chosen after seeing results, so this class refuses an unresolved value: the
    caller must pass the frozen scenario parameters in explicitly.
    """

    name: str
    slippage_bps_per_leg: Decimal
    impact_bps_per_leg: Decimal = ZERO

    @classmethod
    def from_config(cls, scenario: Mapping[str, Any]) -> SlippageScenario:
        name = str(scenario.get("name", "unnamed"))
        raw = {
            "slippage_bps_per_leg": scenario.get("slippage_bps_per_leg"),
            "impact_bps_per_leg": scenario.get("impact_bps_per_leg"),
        }
        unresolved = sorted(k for k, v in raw.items() if is_unresolved(v))
        if unresolved:
            raise CostScheduleError(
                f"cost scenario '{name}' has unresolved parameters {unresolved}; "
                "they must be frozen in config/cost_schedule.yaml before use"
            )
        return cls(
            name=name,
            slippage_bps_per_leg=_dec(raw["slippage_bps_per_leg"]),
            impact_bps_per_leg=_dec(raw["impact_bps_per_leg"]),
        )

    def amount_for_leg(self, turnover_inr: Decimal) -> Decimal:
        """Slippage + impact in rupees for one leg of ``turnover_inr``."""
        bps = self.slippage_bps_per_leg + self.impact_bps_per_leg
        return turnover_inr * bps / Decimal(10_000)


class FeeSchedule:
    """Dated, per-leg statutory cost calculator built from config/cost_schedule.yaml."""

    def __init__(self, raw: Mapping[str, Any], *, source: str = "<memory>") -> None:
        self.source = source
        self.version: str = str(raw.get("cost_schedule_version", "unknown"))
        self._base, self._overrides = self._split_versions(raw.get("versions"))

    # ------------------------------------------------------------------ load

    @classmethod
    def from_yaml(cls, path: str | Path | None = None) -> FeeSchedule:
        target = Path(path) if path is not None else config_path("config/cost_schedule.yaml")
        data = load_yaml(target)
        if "versions" not in data:
            raise ConfigError(f"{target}: cost schedule has no 'versions'")
        return cls(data, source=str(target))

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> FeeSchedule:
        return cls(raw)

    # -------------------------------------------------------------- versions

    def _split_versions(
        self, versions: Any
    ) -> tuple[Mapping[str, Any], list[tuple[date, date, Mapping[str, Any]]]]:
        """Split the config into the open-ended base entry and closed dated windows.

        The base is the entry that is open-ended (``effective_to: null``); it
        supplies the full component set. Every other entry is a closed window
        that partially overrides the base. An entry written with
        ``effective_from: null`` and a dated ``effective_to`` (the historical
        pre-2026-03-01 NSE charge) is a window running from the beginning of time
        up to that date, exactly as the schedule's own note describes.
        """
        if not isinstance(versions, list) or not versions:
            raise CostScheduleError("cost schedule 'versions' must be a non-empty list")
        base: dict[str, Any] = {}
        windows: list[tuple[date, date, Mapping[str, Any]]] = []
        for entry in versions:
            if not isinstance(entry, dict):
                raise CostScheduleError("each cost schedule version must be a mapping")
            components = entry.get("components")
            if not isinstance(components, dict):
                raise CostScheduleError("each cost schedule version needs a 'components' mapping")
            start = _as_date(entry.get("effective_from"))
            end = _as_date(entry.get("effective_to"))
            if end is None:
                for name, spec in components.items():
                    if isinstance(spec, dict):
                        base[name] = spec
            else:
                windows.append((start or date.min, end, components))
        if not base:
            raise CostScheduleError(
                "cost schedule has no open-ended version entry; cannot resolve base rates"
            )
        return base, sorted(windows, key=lambda v: v[1])

    def components_for(self, on: date) -> Mapping[str, Any]:
        """Merged components effective on ``on``: open-ended base plus dated overrides."""
        merged: dict[str, Any] = dict(self._base)
        for start, end, components in self._overrides:
            if start <= on <= end:
                for name, spec in components.items():
                    if isinstance(spec, dict):
                        merged[name] = spec
        return merged

    # ------------------------------------------------------------------- fees

    def leg_fees(
        self,
        *,
        turnover_inr: Decimal,
        side: str,
        on: date,
        include_ipft: bool | None = None,
        ipft_in_gst_base: bool = False,
    ) -> LegFees:
        """Statutory charges for ONE executed leg of ``turnover_inr`` rupees.

        ``side`` is ``"buy"`` or ``"sell"`` from the BROKER's point of view
        (a long entry is a buy, a long exit is a sell).

        ``include_ipft`` defaults to the schedule's ``included_by_default``
        flag: IPFT is only charged when the broker actually charges it.
        ``ipft_in_gst_base`` defaults to False because the schedule marks the
        IPFT GST treatment ``UNVERIFIED_CONTRACT_NOTE_CHECK``.
        """
        if turnover_inr < 0:
            raise CostScheduleError(f"turnover must be non-negative, got {turnover_inr}")
        side = side.lower()
        if side not in ("buy", "sell"):
            raise CostScheduleError(f"side must be 'buy' or 'sell', got {side!r}")

        components = self.components_for(on)
        brokerage_spec = _require(components, "brokerage", on)
        stt_spec = _require(components, "stt", on)
        exchange_spec = _require(components, "exchange_transaction_fee_nse", on)
        sebi_spec = _require(components, "sebi_turnover_fee", on)
        stamp_spec = _require(components, "stamp_duty", on)
        gst_spec = _require(components, "gst", on)

        brokerage = min(
            turnover_inr * _dec(brokerage_spec["percent"]) / Decimal(100),
            _dec(brokerage_spec["cap_per_order_inr"]),
        )

        stt = (
            turnover_inr * _dec(stt_spec["rate_pct"]) / Decimal(100)
            if side in _sides(stt_spec)
            else ZERO
        )

        exchange = (
            turnover_inr * _dec(exchange_spec["rate_pct"]) / Decimal(100)
            if side in _sides(exchange_spec)
            else ZERO
        )

        crore = _dec(sebi_spec.get("crore_inr", 10_000_000))
        sebi = (
            turnover_inr * _dec(sebi_spec["rate_per_crore_inr"]) / crore
            if side in _sides(sebi_spec)
            else ZERO
        )

        stamp = ZERO
        if side in _sides(stamp_spec):
            # Dual form: min(percent * turnover, Rs per crore * turnover).
            # For the configured values the two branches coincide exactly
            # (300 per crore == 0.003%); the lower-of form is implemented
            # regardless so a future rate change is honoured.
            by_percent = turnover_inr * _dec(stamp_spec["percent"]) / Decimal(100)
            by_absolute = (
                turnover_inr
                * _dec(stamp_spec["absolute_per_crore_inr"])
                / _dec(stamp_spec.get("crore_inr", CRORE))
            )
            stamp = min(by_percent, by_absolute)

        ipft = ZERO
        ipft_spec = components.get("ipft_and_other_levies")
        if ipft_spec is not None:
            enabled = (
                bool(ipft_spec.get("included_by_default", False))
                if include_ipft is None
                else include_ipft
            )
            if enabled and side in _sides(ipft_spec):
                ipft = (
                    turnover_inr
                    * _dec(ipft_spec["rate_per_crore_inr"])
                    / _dec(ipft_spec.get("crore_inr", 10_000_000))
                )

        gst_base = brokerage + sebi + exchange
        if ipft_in_gst_base and ipft != ZERO:
            gst_base = gst_base + ipft
        gst = gst_base * _dec(gst_spec["rate_pct"]) / Decimal(100)

        return LegFees(
            brokerage=brokerage,
            stt=stt,
            exchange_transaction_fee=exchange,
            sebi_turnover_fee=sebi,
            stamp_duty=stamp,
            gst=gst,
            ipft=ipft,
        )


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def _require(components: Mapping[str, Any], name: str, on: date) -> Mapping[str, Any]:
    spec = components.get(name)
    if not isinstance(spec, dict):
        raise CostScheduleError(f"cost schedule effective {on} is missing component {name!r}")
    return spec


def _sides(spec: Mapping[str, Any]) -> frozenset[str]:
    return frozenset(str(s).lower() for s in spec.get("sides", ()))


def load_cost_schedule(path: str | Path | None = None) -> FeeSchedule:
    """Load the dated statutory cost schedule from configuration."""
    return FeeSchedule.from_yaml(path)


__all__ = [
    "CRORE",
    "CostScheduleError",
    "FeeSchedule",
    "LegFees",
    "SlippageScenario",
    "load_cost_schedule",
]
