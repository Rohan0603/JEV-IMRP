"""Instrument master: the frozen research universe and optional provider IDs.

``config/universe.yaml`` freezes membership (plan 2.3). Broker instrument tokens
are not part of active configuration: provider adapters resolve them from dated
masters, and this module reports missing provider IDs rather than substituting a
plausible number. Tick-size checks are unverifiable until provider metadata is
resolved separately.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd  # type: ignore[import-untyped]

from jev_imrp.common.config import load_yaml


@dataclass(frozen=True, slots=True)
class InstrumentMaster:
    """Immutable universe metadata with effective dates."""

    frame: pd.DataFrame
    universe_version: str
    frozen_at_utc: str

    def tick_sizes(self) -> dict[tuple[str, str], float]:
        """Return tick sizes only for rows with a resolved provider identifier."""
        known = self.frame.dropna(subset=["instrument_token", "tick_size_inr"])
        return {
            (str(row.exchange), str(row.instrument_token)): float(row.tick_size_inr)
            for row in known.itertuples()
        }

    def symbols(self) -> list[str]:
        return [str(symbol) for symbol in sorted(self.frame["symbol"].tolist())]

    def unresolved_tokens(self) -> list[str]:
        """Symbols still missing a broker token. Blocks live capture, not research."""
        return [str(symbol) for symbol in self.frame.loc[
            self.frame["instrument_token"].isna(), "symbol"
        ].tolist()]


def load_instrument_master(path: str | Path = "config/universe.yaml") -> InstrumentMaster:
    """Load the frozen universe from configuration."""
    data: dict[str, Any] = load_yaml(path)
    rule: dict[str, Any] = data.get("eligibility_rule") or {}
    effective_dates: dict[str, Any] = rule.get("effective_dates") or {}
    rows = [
        {
            "symbol": entry["symbol"],
            "exchange": entry.get("exchange", data.get("exchange")),
            "instrument_token": entry.get("instrument_token"),
            "tick_size_inr": entry.get("tick_size_inr", data.get("tick_size_inr")),
            "currency": data.get("currency"),
            "instrument_type": data.get("instrument_type"),
            "lot_size": data.get("lot_size"),
            "effective_start": effective_dates.get("start"),
            "effective_end": effective_dates.get("end"),
        }
        for entry in data["instruments"]
    ]
    frame = pd.DataFrame(rows).sort_values("symbol").reset_index(drop=True)
    return InstrumentMaster(
        frame=frame,
        universe_version=str(data.get("universe_version", "unversioned")),
        frozen_at_utc=str(data.get("frozen_at_utc", "")),
    )
