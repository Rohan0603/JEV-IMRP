"""Frozen feature definitions for the point-in-time feature pipeline.

Plan references: 7.1 (feature contract), 7.2 (initial features), 7.3 (L1 naming),
7.4 (feature parity), 7.5 (causality tests), 6.1/6.2 (table shapes and units).

Three rules are enforced from here rather than from each feature body:

1. **Availability, not event time.** A feature at ``as_of`` may only read
   observations whose *local availability* timestamp is at or before ``as_of``.
   See ``QUOTE_AVAILABILITY_COL`` / ``BAR_AVAILABILITY_COL`` /
   ``CONTEXT_AVAILABILITY_COL`` in :mod:`jev_imrp.features.definitions`.
2. **Explicit units.** Every numeric feature name ends in ``_bps``, ``_pct`` or
   is declared with its unit in ``FEATURE_UNITS``.
3. **L1 naming.** The feed is a touchline only. The size feature is
   ``touch_size_imbalance``; nothing here is called depth (plan 7.3).

All lookback/window values are frozen constants below. ``config/feature_config.yaml``
still carries ``null`` placeholders for several of them; when a value is present
and not unresolved it overrides the constant here (see :func:`params_from_config`),
and when it is ``null`` the frozen constant is used. Nothing is invented per run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Final

from jev_imrp.common.config import is_unresolved, load_feature_config

# --------------------------------------------------------------------------
# Observation schemas (plan 6.1). These are the documented producer contracts;
# the producers are built in parallel, so nothing here imports their code.
# --------------------------------------------------------------------------

#: quote_states: L1 touchline only. Availability is the local receive time: the
#: earliest moment the strategy could know the state (plan 5.3).
QUOTE_COLUMNS: Final = (
    "event_id",
    "symbol",
    "feed_timestamp_utc",
    "received_timestamp_utc",
    "bid_price_1",
    "bid_qty_1",
    "ask_price_1",
    "ask_qty_1",
    "last_traded_price",
    "last_traded_volume",
    "quote_age_ms",
    "state_valid",
)
QUOTE_AVAILABILITY_COL: Final = "received_timestamp_utc"
_QUOTE_ORDER: Final = ("received_timestamp_utc", "feed_timestamp_utc", "event_id")

#: bars_1m. ``available_timestamp_utc`` is when the bar's final values became
#: known; it is not the bar timestamp (plan 6.1).
BAR_COLUMNS: Final = (
    "symbol",
    "bar_start_utc",
    "bar_end_utc",
    "open_price",
    "high_price",
    "low_price",
    "close_price",
    "volume",
    "is_complete",
    "has_gap",
    "available_timestamp_utc",
)
BAR_AVAILABILITY_COL: Final = "available_timestamp_utc"
_BAR_ORDER: Final = ("available_timestamp_utc", "bar_start_utc")

#: market_context: Nifty / India VIX, each with a source time and a local
#: availability time (plan 6.1).
CONTEXT_COLUMNS: Final = (
    "series",
    "value",
    "source_timestamp_utc",
    "available_timestamp_utc",
)
CONTEXT_AVAILABILITY_COL: Final = "available_timestamp_utc"
_CONTEXT_ORDER: Final = ("available_timestamp_utc", "source_timestamp_utc")

NIFTY_SERIES: Final = "nifty_index"
INDIA_VIX_SERIES: Final = "india_vix"

# --------------------------------------------------------------------------
# Frozen parameters (plan 7.2: freeze the indicator definition and lookback).
# --------------------------------------------------------------------------

#: A quote older than this (by receive time or by its own quote_age_ms field)
#: is not "fresh" and never produces spread/imbalance values.
MAX_QUOTE_AGE_MS: Final = 3_000

#: ATR: simple moving average of true range over this many COMPLETED bars,
#: within the current session only (no overnight carry). The first bar of a
#: session has no previous close and does not contribute a true range.
ATR_LOOKBACK_BARS: Final = 14
ATR_TRUE_RANGE_DEFINITION: Final = "max(high-low, |high-prev_close|, |low-prev_close|)"
ATR_SMOOTHER: Final = "sma"

#: relative_volume reference: cumulative volume at the same minutes-since-open
#: point, averaged over this many strictly prior sessions. The current session's
#: final volume is never used (plan 7.2).
REL_VOLUME_PRIOR_SESSIONS: Final = 5

#: VWAP accumulator resets at the start of every IST session date.
VWAP_RESET_RULE: Final = "ist_session_date"

#: Latest published India VIX usable if it became available no longer than this
#: before the decision time.
INDIA_VIX_MAX_STALENESS_SECONDS: Final = 900

#: Causal multi-horizon Nifty returns (minutes). Returns are computed against
#: the newest context point that was available at least ``h`` minutes before
#: ``as_of``, so gaps are handled without interpolation.
NIFTY_HORIZONS_MINUTES: Final = (1, 5, 15)

#: Predeclared time-of-day buckets over minutes since the IST session open.
#: Half-open [start, end) except the last bucket, which is open-ended.
TIME_OF_DAY_BUCKETS: Final = (
    ("open", 0, 60),
    ("early", 60, 150),
    ("mid", 150, 240),
    ("late", 240, 330),
    ("close", 330, None),
)

#: Length of the continuous IST session in minutes (09:15-15:30).
MINUTES_IN_SESSION: Final = 375

#: A prior session needs at least this many COMPLETED bars before its high/low
#: is trusted as the previous day high/low.
MIN_COMPLETE_BARS_FOR_PDH_PDL: Final = 300


@dataclass(frozen=True)
class FeatureParams:
    """Frozen computation parameters. One instance per feature run."""

    max_quote_age_ms: int = MAX_QUOTE_AGE_MS
    atr_lookback_bars: int = ATR_LOOKBACK_BARS
    rel_volume_prior_sessions: int = REL_VOLUME_PRIOR_SESSIONS
    india_vix_max_staleness_seconds: int = INDIA_VIX_MAX_STALENESS_SECONDS
    nifty_horizons_minutes: tuple[int, ...] = NIFTY_HORIZONS_MINUTES
    min_complete_bars_for_pdh_pdl: int = MIN_COMPLETE_BARS_FOR_PDH_PDL
    vwap_reset_rule: str = VWAP_RESET_RULE
    atr_true_range_definition: str = ATR_TRUE_RANGE_DEFINITION
    atr_smoother: str = ATR_SMOOTHER
    time_of_day_buckets: tuple[tuple[str, int, int | None], ...] = TIME_OF_DAY_BUCKETS


def params_from_config(config: dict[str, Any] | None = None) -> FeatureParams:
    """Build :class:`FeatureParams` from ``config/feature_config.yaml``.

    A ``null`` (unresolved) config entry falls back to the frozen constant in
    this module. The returned params always state what they are, so a record
    can be reproduced from the config plus these constants.
    """
    cfg = load_feature_config() if config is None else config
    by_name = {str(entry.get("name")): entry for entry in cfg.get("features", [])}

    def setting(name: str, key: str) -> Any:
        value = by_name.get(name, {}).get(key)
        return None if is_unresolved(value) else value

    defaults = FeatureParams()
    horizons = setting("nifty_returns_trend", "horizons_minutes")
    return FeatureParams(
        max_quote_age_ms=int(
            setting("spread_bps", "max_quote_age_ms") or defaults.max_quote_age_ms
        ),
        atr_lookback_bars=int(setting("atr_pct", "lookback_bars") or defaults.atr_lookback_bars),
        rel_volume_prior_sessions=int(
            setting("relative_volume", "reference_window") or defaults.rel_volume_prior_sessions
        ),
        india_vix_max_staleness_seconds=int(
            setting("india_vix", "max_staleness_seconds")
            or defaults.india_vix_max_staleness_seconds
        ),
        nifty_horizons_minutes=tuple(int(h) for h in horizons)
        if horizons
        else defaults.nifty_horizons_minutes,
        min_complete_bars_for_pdh_pdl=defaults.min_complete_bars_for_pdh_pdl,
        vwap_reset_rule=str(setting("dist_vwap_pct", "vwap_reset") or defaults.vwap_reset_rule),
    )


# --------------------------------------------------------------------------
# Feature registry
# --------------------------------------------------------------------------

#: Training/model input features, in a fixed order. This tuple IS the
#: allowlist enforced by :func:`jev_imrp.features.causal_checks.assert_no_leaks`.
#: Metadata columns (versions, lineage, validity, quality) are never training
#: inputs; plan 7.4 requires every model to see the same information.
TRAINING_FEATURES: Final = (
    "spread_bps",
    "touch_size_imbalance",
    "atr_pct",
    "relative_volume",
    "dist_vwap_pct",
    "dist_pdh_pct",
    "dist_pdl_pct",
    "india_vix",
    "india_vix_change_pct",
    "nifty_ret_1_pct",
    "nifty_ret_5_pct",
    "nifty_ret_15_pct",
    "tod_minutes_since_open",
    "tod_bucket",
)

FEATURE_UNITS: Final = {
    "spread_bps": "_bps",
    "touch_size_imbalance": "ratio",
    "atr_pct": "_pct",
    "relative_volume": "ratio",
    "dist_vwap_pct": "_pct",
    "dist_pdh_pct": "_pct",
    "dist_pdl_pct": "_pct",
    "india_vix": "index",
    "india_vix_change_pct": "_pct",
    "nifty_ret_1_pct": "_pct",
    "nifty_ret_5_pct": "_pct",
    "nifty_ret_15_pct": "_pct",
    "tod_minutes_since_open": "minutes",
    "tod_bucket": "categorical",
}

#: Quality/reason codes a feature may carry. Anything not listed here is a bug.
QUALITY_OK: Final = "ok"
QUALITY_CODES: Final = (
    QUALITY_OK,
    "warmup",  # not enough history yet
    "stale_quote",  # quote older than the freshness limit
    "invalid_quote_state",  # state_valid false / crossed or non-positive prices
    "zero_denominator",  # size denominator zero
    "missing_observation",  # no usable observation available by as_of
    "stale_context",  # context older than its staleness limit
    "insufficient_prior_session",  # prior session too incomplete for pdh/pdl
    "gap_in_prior_session",  # prior session carries a gap flag
    "outside_session",  # decision time outside the declared IST session
)

#: Name fragments that indicate a column carries information from after the
#: decision time. Matched case-insensitively against a column name.
LEAK_PATTERNS: Final = (
    re.compile(r"future"),
    re.compile(r"forward"),
    re.compile(r"label"),
    re.compile(r"outcome"),
    re.compile(r"target"),
    re.compile(r"\bnext\b"),
    re.compile(r"lead"),
)


@lru_cache(maxsize=1)
def feature_versions() -> tuple[str, str]:
    """``(feature_config_version, feature_set_version)`` from the config file."""
    cfg = load_feature_config()
    return str(cfg["feature_config_version"]), str(cfg["feature_set_version"])
