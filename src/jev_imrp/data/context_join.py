"""Point-in-time market context: Nifty/VIX/sector/breadth as-of joins.

Plan 6.1 requires each context observation to carry a *source time* and a *local
availability time*, and only observations available by the candidate decision
time may be joined, with a maximum staleness limit. This module makes that
mechanical: an observation whose ``available_at_utc`` is later than the decision
time is invisible, and one older than ``max_staleness`` is returned but flagged.

Nothing here is fetched. The caller supplies a frame; a future data source must
carry the same two timestamps or the join will refuse it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd  # type: ignore[import-untyped]

CONTEXT_COLUMNS = (
    "context_name",
    "source_timestamp_utc",
    "available_at_utc",
    "value",
    "context_version",
    "source",
)


@dataclass(frozen=True, slots=True)
class MarketContext:
    """Validated context observations, one row per published value."""

    frame: pd.DataFrame

    def names(self) -> list[str]:
        return sorted(self.frame["context_name"].dropna().unique().tolist())

    def asof(self, context_name: str, decision_timestamp_utc: Any,
             max_staleness_seconds: float = 900.0) -> dict[str, Any] | None:
        """Latest observation of ``context_name`` knowable at the decision time.

        Returns None when nothing was available. Otherwise the joined row carries
        ``context_age_seconds`` and ``context_stale`` so the caller decides.
        """
        decision = pd.Timestamp(decision_timestamp_utc)
        if decision.tzinfo is None:
            raise ValueError("decision timestamp must be timezone-aware (UTC)")
        decision = decision.tz_convert("UTC")

        subset = self.frame[self.frame["context_name"] == context_name]
        if subset.empty:
            return None
        subset = subset.sort_values("available_at_utc")
        known = subset[subset["available_at_utc"] <= decision]
        if known.empty:
            return None
        row = known.iloc[-1]
        age = (decision - row["available_at_utc"]).total_seconds()
        return {
            "context_name": context_name,
            "value": row["value"],
            "source_timestamp_utc": row["source_timestamp_utc"],
            "available_at_utc": row["available_at_utc"],
            "context_age_seconds": age,
            "context_stale": age > max_staleness_seconds,
            "context_version": row.get("context_version"),
            "source": row.get("source"),
        }

    def join(
        self, decisions: pd.DataFrame,
        decision_timestamp_column: str = "decision_timestamp_utc",
        max_staleness_seconds: float = 900.0,
    ) -> pd.DataFrame:
        """Attach the latest knowable observation of every context name.

        The join is done on availability time only. A future value cannot appear,
        whatever its source timestamp claims.
        """
        if decisions.empty:
            return decisions.assign(
                context_value=pd.Series(dtype="object"),
                context_stale=pd.Series(dtype="bool"),
            )
        out = decisions.copy()
        out["context_value"] = None
        out["context_stale"] = None
        for name in self.names():
            picks = [
                self.asof(name, stamp, max_staleness_seconds)
                for stamp in out[decision_timestamp_column]
            ]
            out[f"context_{name}_value"] = [p["value"] if p else None for p in picks]
            out[f"context_{name}_available_at_utc"] = [
                p["available_at_utc"] if p else None for p in picks
            ]
            out[f"context_{name}_stale"] = [bool(p["context_stale"]) if p else None for p in picks]
        return out


def load_market_context(rows: list[dict[str, Any]] | pd.DataFrame) -> MarketContext:
    """Build a validated context frame.

    Rows missing the availability timestamp are rejected: without it a value
    cannot be shown to have been knowable at decision time, so accepting it would
    be a silent lookahead.
    """
    if isinstance(rows, pd.DataFrame):
        frame = rows.copy()
    else:
        frame = pd.DataFrame(list(rows))
    if frame.empty:
        return MarketContext(frame=pd.DataFrame(columns=list(CONTEXT_COLUMNS)))

    missing = [
        name
        for name in ("context_name", "source_timestamp_utc", "available_at_utc", "value")
        if name not in frame.columns
    ]
    if missing:
        raise ValueError(f"market context is missing required column(s): {', '.join(missing)}")
    for name in ("source_timestamp_utc", "available_at_utc"):
        frame[name] = pd.to_datetime(frame[name], utc=True, errors="coerce")
    rejected = frame["available_at_utc"].isna() | frame["source_timestamp_utc"].isna()
    if rejected.any():
        raise ValueError(
            f"{int(rejected.sum())} market context row(s) lack a source or availability "
            "timestamp; a value that cannot be shown to have been available in time "
            "is rejected, not defaulted"
        )
    return MarketContext(frame=frame.sort_values(["context_name", "available_at_utc"]))
