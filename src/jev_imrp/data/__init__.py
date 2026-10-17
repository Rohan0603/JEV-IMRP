"""Sprint 1 data layer: instrument master, minute bars, context joins, quality.

Everything here is pure, point-in-time and research-only: no network, no
orders, no mutable state. Bar and context rows carry both a source timestamp and
a local availability timestamp, so a later feature stage can never read a value
that was not knowable at decision time (plan 6.1, 7.1).
"""

from __future__ import annotations

from jev_imrp.data.bars import BARS_1M_CONVENTION, build_bars_1m
from jev_imrp.data.context_join import MarketContext, load_market_context
from jev_imrp.data.instrument_master import InstrumentMaster, load_instrument_master
from jev_imrp.data.quality import DataHealthReport, build_health_report

__all__ = [
    "BARS_1M_CONVENTION",
    "DataHealthReport",
    "InstrumentMaster",
    "MarketContext",
    "build_bars_1m",
    "build_health_report",
    "load_instrument_master",
    "load_market_context",
]
