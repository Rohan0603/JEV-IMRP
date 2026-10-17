"""One-minute bars from reconstructed quote states (plan section 6.1, ``bars_1m``).

Declared bar convention (frozen; a result must state which convention produced it):

* **Bucket time** is the *source* time of the update: ``feed_timestamp_utc`` when
  the feed provided one, otherwise ``received_timestamp_utc``. The basis used is
  recorded per bar in ``source_time_basis``. Minute boundaries are UTC-aligned,
  which coincide with IST minute boundaries because IST is a whole number of
  minutes offset from UTC.
* **Contents** come from ``last_price`` only (this feed is L1 touchline; there is
  no depth and no per-trade tape). A row without a usable ``last_price``
  contributes nothing to OHLC and is counted in ``updates_without_price``.
* **Volume** is the exchange-reported cumulative daily field ``v``. The minute
  volume is the difference from the previous observed cumulative value for the
  same symbol and session; it is null when no earlier value exists, never zero
  substituted. ``volume_basis`` records that convention.
* **Availability** is separate from bar time: ``available_at_utc`` is the latest
  local receive time among the contributing updates. A bar is
  ``bar_complete`` only when ``bar_end_utc`` is at or before
  ``complete_through_utc`` (normally the last receive time seen, or the declared
  session close). An incomplete bar is still written, flagged.
* **Gap flags**: ``minutes_since_previous_bar`` is the distance in minutes from
  the previous observed bar for that symbol/session, and ``gap_before`` is true
  when it exceeds one, i.e. minutes are missing before this bar.

Absent market fields stay null. A bar is never synthesized for a minute with no
trades; the caller sees the gap through the gap flags and the quality report.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

#: Human-readable convention string stored alongside every bar dataset.
BARS_1M_CONVENTION = (
    "ohlcv_1m_from_l1_last_price; bucket=source_time(feed_timestamp_utc,fallback=received); "
    "volume=cumulative_field_difference(v); bar_complete=bar_end<=complete_through; "
    "availability=available_at_utc(max received of contributing updates)"
)

BARS_1M_COLUMNS = (
    "symbol",
    "exchange",
    "bar_start_utc",
    "bar_end_utc",
    "open_price",
    "high_price",
    "low_price",
    "close_price",
    "volume",
    "volume_basis",
    "trade_updates",
    "updates_total",
    "updates_without_price",
    "source_time_basis",
    "source_max_feed_utc",
    "available_at_utc",
    "bar_complete",
    "gap_before",
    "minutes_since_previous_bar",
    "bar_convention",
)


def build_bars_1m(
    states: pd.DataFrame,
    complete_through_utc: Any = None,
    bar_convention: str = BARS_1M_CONVENTION,
) -> pd.DataFrame:
    """Aggregate quote-state updates into one row per symbol/minute.

    ``states`` is the ``quote_states`` frame (one row per update). Rows with
    ``state_valid`` false are rejected and counted, never silently dropped, and
    rows whose ``last_price`` is absent contribute to the counters but not to
    OHLC.
    """
    if states is None or len(states) == 0:
        return pd.DataFrame(columns=list(BARS_1M_COLUMNS))

    frame = states.copy()
    for name in ("feed_timestamp_utc", "received_timestamp_utc"):
        frame[name] = pd.to_datetime(frame[name], utc=True, errors="coerce")
    frame["state_valid"] = frame["state_valid"].fillna(False).astype(bool)

    rejected = int((~frame["state_valid"]).sum())
    frame = frame[frame["state_valid"]].copy()
    if frame.empty:
        empty = pd.DataFrame(columns=list(BARS_1M_COLUMNS))
        empty.attrs["rows_rejected_invalid"] = rejected
        return empty
    frame["source_utc"] = frame["feed_timestamp_utc"].fillna(frame["received_timestamp_utc"])
    frame["source_time_basis"] = frame["feed_timestamp_utc"].notna().map(
        {True: "feed_timestamp_utc", False: "received_timestamp_utc"}
    )
    frame["bar_start_utc"] = frame["source_utc"].dt.floor("min")
    frame["bar_end_utc"] = frame["bar_start_utc"] + pd.Timedelta(minutes=1)
    frame["has_price"] = frame["last_price"].notna()
    # A minute is in exchange IST terms: the IST date, used for session grouping.
    frame["session_date"] = frame["bar_start_utc"].dt.tz_convert("Asia/Kolkata").dt.date
    frame["_order"] = frame["source_utc"]

    bars: list[dict[str, Any]] = []
    for (symbol, _session_date), group in frame.groupby(["symbol", "session_date"], sort=True):
        bars.extend(_group_bars(symbol, group, bar_convention))

    result = pd.DataFrame(bars)
    horizon = (
        pd.to_datetime(complete_through_utc, utc=True)
        if complete_through_utc is not None
        else result["available_at_utc"].max()
    )
    result["bar_complete"] = result["bar_end_utc"] <= horizon
    result["minutes_since_previous_bar"] = _minutes_since_previous_bar(result["bar_start_utc"])
    result["gap_before"] = result["minutes_since_previous_bar"] > 1
    result = result.sort_values(["symbol", "bar_start_utc"]).reset_index(drop=True)
    result.attrs["rows_rejected_invalid"] = rejected
    return result[list(BARS_1M_COLUMNS)]


def _group_bars(symbol: str, group: pd.DataFrame, convention: str) -> list[dict[str, Any]]:
    """Bars for one symbol/session. ``rejected`` is shared and reported once."""
    group = group.sort_values("_order")
    out: list[dict[str, Any]] = []
    previous_cumulative: float | None = None
    for bar_start, minute in group.groupby("bar_start_utc", sort=True):
        priced = minute[minute["has_price"]]
        cumulative = minute["volume"].dropna()
        volume = None
        if not cumulative.empty and previous_cumulative is not None:
            difference = float(cumulative.iloc[-1]) - previous_cumulative
            volume = difference if difference >= 0 else None
        if not cumulative.empty:
            previous_cumulative = float(cumulative.iloc[-1])
        out.append(
            {
                "symbol": symbol,
                "exchange": _first(minute, "exchange"),
                "bar_start_utc": bar_start,
                "bar_end_utc": bar_start + pd.Timedelta(minutes=1),
                "open_price": _first(priced, "last_price"),
                "high_price": _max(priced, "last_price"),
                "low_price": _min(priced, "last_price"),
                "close_price": _last(priced, "last_price"),
                "volume": volume,
                "volume_basis": "cumulative_v_difference" if volume is not None else None,
                "trade_updates": len(priced),
                "updates_total": len(minute),
                "updates_without_price": int(len(minute) - len(priced)),
                "source_time_basis": _join(minute, "source_time_basis"),
                "source_max_feed_utc": minute["feed_timestamp_utc"].max(),
                "available_at_utc": minute["received_timestamp_utc"].max(),
                "bar_complete": False,  # set once the completeness horizon is known
                "gap_before": False,
                "minutes_since_previous_bar": 1,
                "bar_convention": convention,
            }
        )
    return out


def _minutes_since_previous_bar(starts: pd.Series) -> pd.Series:
    """Minutes between consecutive observed bars; 1 means contiguous."""
    starts = pd.Series(sorted(starts))
    if len(starts) < 2:
        return pd.Series([1] * len(starts), index=starts.index)
    deltas = starts.diff().dt.total_seconds() / 60.0
    return deltas.fillna(1).astype(int)


def _first(frame: pd.DataFrame, column: str) -> Any:
    return None if frame.empty or column not in frame else frame[column].iloc[0]


def _last(frame: pd.DataFrame, column: str) -> Any:
    return None if frame.empty or column not in frame else frame[column].iloc[-1]


def _max(frame: pd.DataFrame, column: str) -> Any:
    return None if frame.empty or column not in frame else frame[column].max()


def _min(frame: pd.DataFrame, column: str) -> Any:
    return None if frame.empty or column not in frame else frame[column].min()


def _join(frame: pd.DataFrame, column: str) -> str | None:
    values = sorted(set(frame[column].dropna()))
    return "+".join(values) if values else None
