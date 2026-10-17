"""Structured data-health report (plan 11.1 data gate, 16.2).

The report is measurement, not judgement: it counts coverage, invalid quotes,
stale observations, gaps, duplicates and rejected rows so a human decides whether
a session is usable. Every threshold used here is an argument with a default that
is *stated in the report*, so a reader can see which limits were applied.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from jev_imrp.common.timeutils import ist_session_date


@dataclass(frozen=True, slots=True)
class DataHealthReport:
    """Health of one captured dataset, per symbol and session."""

    report_version: str
    symbols_covered: list[str]
    sessions_covered: list[str]
    rows_total: int
    rows_valid: int
    rows_rejected_invalid: int
    rows_duplicate_event_id: int
    rows_missing_feed_timestamp: int
    rows_stale: int
    coverage_by_symbol_session: dict[str, int]
    invalid_reason_counts: dict[str, int]
    minutes_since_previous_bar_total: int
    bars_incomplete: int
    bars_missing_price: int
    thresholds: dict[str, float]
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "report_version": self.report_version,
            "symbols_covered": self.symbols_covered,
            "sessions_covered": self.sessions_covered,
            "rows_total": self.rows_total,
            "rows_valid": self.rows_valid,
            "rows_rejected_invalid": self.rows_rejected_invalid,
            "rows_duplicate_event_id": self.rows_duplicate_event_id,
            "rows_missing_feed_timestamp": self.rows_missing_feed_timestamp,
            "rows_stale": self.rows_stale,
            "coverage_by_symbol_session": self.coverage_by_symbol_session,
            "invalid_reason_counts": self.invalid_reason_counts,
            "minutes_since_previous_bar_total": self.minutes_since_previous_bar_total,
            "bars_incomplete": self.bars_incomplete,
            "bars_missing_price": self.bars_missing_price,
            "thresholds": self.thresholds,
            "notes": self.notes,
        }

    def blocking_problems(self) -> list[str]:
        """Problems that must stop downstream research, given the thresholds."""
        problems: list[str] = []
        if not self.symbols_covered:
            problems.append("no symbol produced a valid quote state")
        if self.rows_rejected_invalid:
            problems.append(f"{self.rows_rejected_invalid} quote-state row(s) were invalid")
        if self.rows_duplicate_event_id:
            problems.append(f"{self.rows_duplicate_event_id} duplicate event_id(s)")
        if self.rows_stale:
            problems.append(f"{self.rows_stale} observation(s) exceeded max quote age")
        if self.minutes_since_previous_bar_total:
            problems.append(f"{self.minutes_since_previous_bar_total} missing minute(s) in observed bars")
        return problems


def build_health_report(
    states: pd.DataFrame,
    bars: pd.DataFrame | None = None,
    max_quote_age_ms: float = 5_000.0,
    report_version: str = "data_health_v1",
) -> DataHealthReport:
    """Measure the health of a reconstructed dataset.

    ``states`` is the ``quote_states`` frame; ``bars`` the optional ``bars_1m``
    frame. Nothing is dropped: invalid and duplicate rows are counted.
    """
    thresholds = {"max_quote_age_ms": max_quote_age_ms}
    notes: list[str] = []

    if states is None or len(states) == 0:
        return DataHealthReport(
            report_version=report_version,
            symbols_covered=[],
            sessions_covered=[],
            rows_total=0,
            rows_valid=0,
            rows_rejected_invalid=0,
            rows_duplicate_event_id=0,
            rows_missing_feed_timestamp=0,
            rows_stale=0,
            coverage_by_symbol_session={},
            invalid_reason_counts={},
            minutes_since_previous_bar_total=0,
            bars_incomplete=0,
            bars_missing_price=0,
            thresholds=thresholds,
            notes=["no quote states supplied"],
        )

    frame = states.copy()
    frame["received_timestamp_utc"] = pd.to_datetime(frame["received_timestamp_utc"], utc=True)
    frame["feed_timestamp_utc"] = pd.to_datetime(
        frame.get("feed_timestamp_utc"), utc=True, errors="coerce"
    )
    frame["session"] = frame["received_timestamp_utc"].map(lambda v: ist_session_date(v).isoformat())
    frame["state_valid"] = frame["state_valid"].fillna(False).astype(bool)

    valid = frame[frame["state_valid"]]
    coverage = {
        f"{symbol}|{session}": int(len(group))
        for (symbol, session), group in valid.groupby(["symbol", "session"], sort=True)
    }
    invalid_reasons: dict[str, int] = {}
    for reasons in frame.loc[~frame["state_valid"], "invalid_reason"].dropna():
        for reason in str(reasons).split(","):
            if reason:
                invalid_reasons[reason] = invalid_reasons.get(reason, 0) + 1

    duplicates = int(frame["event_id"].duplicated().sum()) if "event_id" in frame else 0
    missing_feed_time = int(frame["feed_timestamp_utc"].isna().sum())
    stale = 0
    if "quote_age_ms" in valid:
        stale = int((valid["quote_age_ms"] > max_quote_age_ms).sum())

    minutes_since_previous_bar = 0
    incomplete = 0
    missing_price = 0
    if bars is not None and len(bars):
        minutes_since_previous_bar = int((bars["minutes_since_previous_bar"].clip(lower=0) - 1).sum())
        incomplete = int((~bars["bar_complete"].astype(bool)).sum())
        missing_price = int(bars["trade_updates"].eq(0).sum())
        if (bars["trade_updates"].eq(0)).any():
            notes.append("bars with no priced update are retained as gaps, not filled")

    if missing_feed_time:
        notes.append(
            "rows without a feed timestamp use receive time for bucketing; "
            "source_time_basis records which was used"
        )

    return DataHealthReport(
        report_version=report_version,
        symbols_covered=sorted(valid["symbol"].dropna().unique().tolist()),
        sessions_covered=sorted(valid["session"].unique().tolist()),
        rows_total=int(len(frame)),
        rows_valid=int(len(valid)),
        rows_rejected_invalid=int((~frame["state_valid"]).sum()),
        rows_duplicate_event_id=duplicates,
        rows_missing_feed_timestamp=missing_feed_time,
        rows_stale=stale,
        coverage_by_symbol_session=coverage,
        invalid_reason_counts=dict(sorted(invalid_reasons.items())),
        minutes_since_previous_bar_total=minutes_since_previous_bar,
        bars_incomplete=incomplete,
        bars_missing_price=missing_price,
        thresholds=thresholds,
        notes=notes,
    )
