"""Fail-closed gate for the archived Shoonya runtime.

The active provider boundary is direct FYERS v3. The Shoonya adapter is kept
only as historical replay provenance until ORC-112 removes it; nothing may
reach it through the package's runtime surface by accident.

The gate is deliberately explicit: a process must opt in with
``JEV_ENABLE_LEGACY_SHOONYA=1`` and the affected export is then loaded lazily.
Importing :mod:`jev_imrp.ingest` must never import a Shoonya module.
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Mapping

#: Opt-in flag. Absent or false means the archived runtime is disabled.
LEGACY_ENV_FLAG = "JEV_ENABLE_LEGACY_SHOONYA"

#: Research gates that still have to be satisfied before Shoonya can return.
RESEARCH_GATE_ISSUES = ("ORC-57", "ORC-113")

#: Archived Shoonya module basenames. The regression scanner flags new imports.
LEGACY_MODULE_NAMES = (
    "shoonya_recorder",
    "shoonya_feed_client",
    "historical_data_client",
    "symbol_resolver",
)

#: Package-level exports that now resolve lazily through this gate.
LEGACY_EXPORTS: dict[str, str] = {
    "Recorder": "jev_imrp.ingest.shoonya_recorder",
    "ShoonyaFeedClient": "jev_imrp.ingest.shoonya_feed_client",
    "HistoricalDataError": "jev_imrp.ingest.historical_data_client",
    "fetch_eod_chart_data": "jev_imrp.ingest.historical_data_client",
    "fetch_tp_series_data": "jev_imrp.ingest.historical_data_client",
    "ResolvedToken": "jev_imrp.ingest.symbol_resolver",
    "SymbolResolutionError": "jev_imrp.ingest.symbol_resolver",
    "resolve_symbol": "jev_imrp.ingest.symbol_resolver",
    "resolve_symbols_batch": "jev_imrp.ingest.symbol_resolver",
    "resolve_via_master": "jev_imrp.ingest.symbol_resolver",
    "resolve_via_searchscrip": "jev_imrp.ingest.symbol_resolver",
}


class LegacyShoonyaDisabled(RuntimeError):
    """The archived Shoonya runtime was requested without an explicit opt-in."""


def legacy_shoonya_enabled(environ: Mapping[str, str] | None = None) -> bool:
    """True only for an explicit truthy opt-in on the process environment."""
    value = (environ if environ is not None else os.environ).get(LEGACY_ENV_FLAG, "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def require_legacy_shoonya(environ: Mapping[str, str] | None = None) -> None:
    """Raise unless the archived runtime was explicitly enabled."""
    if legacy_shoonya_enabled(environ):
        return
    issues = ", ".join(RESEARCH_GATE_ISSUES)
    raise LegacyShoonyaDisabled(
        "the archived Shoonya runtime is disabled; set "
        f"{LEGACY_ENV_FLAG}=1 only for offline provenance work. "
        f"Re-enabling it requires {issues}."
    )


def legacy_export(name: str, *, environ: Mapping[str, str] | None = None) -> object:
    """Load one archived export after the gate, or raise ``AttributeError``."""
    if name not in LEGACY_EXPORTS:
        raise AttributeError(name)
    require_legacy_shoonya(environ)
    module = importlib.import_module(LEGACY_EXPORTS[name])
    return getattr(module, name)


__all__ = [
    "LEGACY_ENV_FLAG",
    "LEGACY_EXPORTS",
    "LEGACY_MODULE_NAMES",
    "RESEARCH_GATE_ISSUES",
    "LegacyShoonyaDisabled",
    "legacy_export",
    "legacy_shoonya_enabled",
    "require_legacy_shoonya",
]
