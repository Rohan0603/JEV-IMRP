"""Fail-closed qualification metrics for OpenAlgo-backed FYERS captures."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from jev_imrp.ingest.raw_event import RawEvent


@dataclass(frozen=True, slots=True)
class QualificationThresholds:
    """Versioned, precommitted limits; zero means no anomalies are accepted."""

    max_source_receive_lag_ms: float = 2_000.0
    max_stale_gap_ms: float = 5_000.0
    max_crossed_books: int = 0
    max_missing_depth_levels: int = 0
    max_duplicate_events: int = 0
    max_out_of_order_events: int = 0
    max_reconnect_gap_ms: float = 5_000.0
    max_missing_source_timestamps: int = 0


@dataclass(frozen=True, slots=True)
class QualificationReport:
    metrics: dict[str, Any]
    blocked_reasons: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.blocked_reasons

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "metrics": self.metrics,
            "blocked_reasons": list(self.blocked_reasons),
        }


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def fill_eligible(event: RawEvent, *, as_of: datetime, max_stale_ms: float = 5_000.0) -> bool:
    """Return true only when the event contains a usable explicit touchline.

    A Quote frame without bid/ask is never repaired from LTP/OHLC. A Depth
    frame may qualify because its level-one prices and sizes are explicit.
    """
    payload = event.payload
    bid = _number(payload.get("bp1"))
    ask = _number(payload.get("sp1"))
    bid_qty = _number(payload.get("bq1"))
    ask_qty = _number(payload.get("sq1"))
    received = event.received_timestamp_utc
    age_ms = (as_of - received).total_seconds() * 1000.0
    return (
        event.feed_timestamp_utc is not None
        and age_ms >= 0
        and age_ms <= max_stale_ms
        and bid is not None
        and ask is not None
        and bid > 0
        and ask > bid
        and bid_qty is not None
        and bid_qty > 0
        and ask_qty is not None
        and ask_qty > 0
    )


def evaluate_qualification(
    events: Iterable[RawEvent],
    *,
    thresholds: QualificationThresholds | None = None,
    capture_end_utc: datetime | None = None,
    reconnect_gaps_ms: Iterable[float] = (),
) -> QualificationReport:
    limits = thresholds or QualificationThresholds()
    event_list = [event for event in events if event.message_type in {"tk", "tf"}]
    end = capture_end_utc or max(
        (event.received_timestamp_utc for event in event_list), default=datetime.now(UTC)
    )
    lag_values: list[float] = []
    stale_gaps: list[float] = []
    last_received: dict[tuple[str | None, str | None], datetime] = {}
    last_source: dict[tuple[str | None, str | None], datetime] = {}
    seen: set[str] = set()
    duplicate_events = 0
    out_of_order = 0
    crossed = 0
    missing_depth = 0
    missing_source_timestamps = 0
    eligible = 0
    for event in event_list:
        key = (event.exchange, event.token)
        if event.event_id in seen:
            duplicate_events += 1
        seen.add(event.event_id)
        if event.feed_timestamp_utc is not None:
            lag = (event.received_timestamp_utc - event.feed_timestamp_utc).total_seconds() * 1000
            lag_values.append(lag)
            previous_source = last_source.get(key)
            if previous_source is not None and event.feed_timestamp_utc < previous_source:
                out_of_order += 1
            last_source[key] = event.feed_timestamp_utc
        else:
            missing_source_timestamps += 1
        previous_received = last_received.get(key)
        if previous_received is not None:
            stale_gaps.append(
                (event.received_timestamp_utc - previous_received).total_seconds() * 1000
            )
        last_received[key] = event.received_timestamp_utc
        bid = _number(event.payload.get("bp1"))
        ask = _number(event.payload.get("sp1"))
        if bid is not None and ask is not None and bid >= ask:
            crossed += 1
        if event.payload.get("_openalgo_type") == "depth" and (bid is None or ask is None):
            missing_depth += 1
        if fill_eligible(event, as_of=end, max_stale_ms=limits.max_stale_gap_ms):
            eligible += 1
    reconnect = [float(value) for value in reconnect_gaps_ms]
    final_stale_gap = (
        (
            end - max((event.received_timestamp_utc for event in event_list), default=end)
        ).total_seconds()
        * 1000
        if event_list
        else 0.0
    )
    metrics = {
        "market_events": len(event_list),
        "max_source_receive_lag_ms": max(lag_values, default=0.0),
        "max_stale_gap_ms": max([*stale_gaps, final_stale_gap], default=0.0),
        "crossed_books": crossed,
        "missing_depth_levels": missing_depth,
        "missing_source_timestamps": missing_source_timestamps,
        "duplicate_events": duplicate_events,
        "out_of_order_events": out_of_order,
        "reconnect_gaps_ms": reconnect,
        "fill_eligible_events": eligible,
        "quote_events_without_explicit_touchline": sum(
            1 for event in event_list
            if event.payload.get("_openalgo_type") == "quote"
            and (event.payload.get("bp1") is None or event.payload.get("sp1") is None)
        ),
    }
    max_lag = max(lag_values, default=0.0)
    max_stale = max([*stale_gaps, final_stale_gap], default=0.0)
    reasons: list[str] = []
    if max_lag > limits.max_source_receive_lag_ms:
        reasons.append("source_receive_lag")
    if max_stale > limits.max_stale_gap_ms:
        reasons.append("stale_gap")
    if crossed > limits.max_crossed_books:
        reasons.append("crossed_book")
    if missing_depth > limits.max_missing_depth_levels:
        reasons.append("missing_depth_levels")
    if missing_source_timestamps > limits.max_missing_source_timestamps:
        reasons.append("missing_source_timestamp")
    if duplicate_events > limits.max_duplicate_events:
        reasons.append("duplicate_events")
    if out_of_order > limits.max_out_of_order_events:
        reasons.append("out_of_order_events")
    if reconnect and max(reconnect) > limits.max_reconnect_gap_ms:
        reasons.append("reconnect_gap")
    return QualificationReport(metrics=metrics, blocked_reasons=tuple(reasons))


__all__ = [
    "QualificationReport",
    "QualificationThresholds",
    "evaluate_qualification",
    "fill_eligible",
]
