"""Deterministic, offline data-health scoring for captured raw events.

The scoreboard is deliberately conservative: missing provider timestamps,
duplicate packets, and crossed books block downstream fill research instead of
being repaired or inferred from receipt time.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from jev_imrp.ingest.raw_event import RawEvent


@dataclass(frozen=True, slots=True)
class DataHealthThresholds:
    """Predeclared limits for the offline scoreboard."""

    required_symbols: frozenset[str] = frozenset()
    max_missing_timestamp_ratio: float = 0.0
    max_duplicate_events: int = 0
    max_out_of_order_events: int = 0
    max_crossed_quotes: int = 0
    max_missing_touchline: int = 0
    max_non_positive_sizes: int = 0
    max_sequence_gaps: int = 0
    max_out_of_session_events: int = 0
    min_interval_coverage_ratio: float = 1.0
    session_timezone: str = "Asia/Kolkata"
    session_open_local: time = time(9, 15)
    session_close_local: time = time(15, 30)
    trading_weekdays: frozenset[int] = frozenset({0, 1, 2, 3, 4})
    holidays: frozenset[date] = frozenset()

    def __post_init__(self) -> None:
        for name in (
            "max_missing_timestamp_ratio",
            "max_duplicate_events",
            "max_out_of_order_events",
            "max_crossed_quotes",
            "max_missing_touchline",
            "max_non_positive_sizes",
            "max_sequence_gaps",
            "max_out_of_session_events",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must not be negative")
        if not 0 <= self.max_missing_timestamp_ratio <= 1:
            raise ValueError("max_missing_timestamp_ratio must be between 0 and 1")
        if not 0 <= self.min_interval_coverage_ratio <= 1:
            raise ValueError("min_interval_coverage_ratio must be between 0 and 1")
        if not self.session_timezone:
            raise ValueError("session_timezone must not be empty")
        ZoneInfo(self.session_timezone)
        if (
            self.session_open_local.tzinfo is not None
            or self.session_close_local.tzinfo is not None
        ):
            raise ValueError("session bounds must be timezone-naive local times")
        if self.session_open_local >= self.session_close_local:
            raise ValueError("session_open_local must precede session_close_local")
        if not self.trading_weekdays.issubset(set(range(7))):
            raise ValueError("trading_weekdays must contain only weekday numbers 0 through 6")


@dataclass(frozen=True, slots=True)
class DataHealthReport:
    """Metrics and fail-closed reasons for one captured event set."""

    metrics: dict[str, Any]
    blocked_reasons: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.blocked_reasons

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "metrics": dict(self.metrics),
            "blocked_reasons": list(self.blocked_reasons),
        }


def evaluate_events(
    events: Iterable[RawEvent],
    *,
    thresholds: DataHealthThresholds | None = None,
    expected_intervals: Mapping[str, Iterable[datetime]] | None = None,
) -> DataHealthReport:
    """Score quote/depth events without changing or dropping raw evidence."""
    limits = thresholds or DataHealthThresholds()
    event_list = list(events)
    quote_events = [event for event in event_list if event.message_type in {"tk", "tf"}]
    seen_ids: set[str] = set()
    duplicate_events = 0
    out_of_order_events = 0
    crossed_quotes = 0
    missing_touchline = 0
    non_positive_sizes = 0
    missing_source_timestamp = 0
    sequence_gaps = 0
    out_of_session_events = 0
    last_feed_time: dict[tuple[str, str], datetime] = {}
    last_sequence: dict[tuple[str, int], int] = {}
    symbols: set[str] = set()
    symbol_event_counts: dict[str, int] = {}
    symbol_day_event_counts: dict[str, int] = {}
    received_timestamps: dict[str, set[datetime]] = {}
    session_zone = ZoneInfo(limits.session_timezone)
    sessions = {event.session_id for event in event_list}
    connections = {event.connection_id for event in event_list}
    generations = {
        (event.session_id, event.subscription_generation) for event in event_list
    }

    for event in quote_events:
        if event.event_id in seen_ids:
            duplicate_events += 1
        seen_ids.add(event.event_id)
        symbol = event.symbol_as_received or event.token
        if symbol:
            symbols.add(symbol)
            symbol_event_counts[symbol] = symbol_event_counts.get(symbol, 0) + 1
            if event.feed_timestamp_utc is not None:
                received_timestamps.setdefault(symbol, set()).add(
                    event.feed_timestamp_utc
                )
                session_date = event.feed_timestamp_utc.astimezone(session_zone).date()
                day_key = f"{symbol}|{session_date.isoformat()}"
                symbol_day_event_counts[day_key] = (
                    symbol_day_event_counts.get(day_key, 0) + 1
                )
        sequence_key = (event.session_id, event.subscription_generation)
        previous_sequence = last_sequence.get(sequence_key)
        if previous_sequence is not None and event.sequence > previous_sequence + 1:
            sequence_gaps += event.sequence - previous_sequence - 1
        last_sequence[sequence_key] = max(event.sequence, previous_sequence or event.sequence)
        if event.feed_timestamp_utc is None:
            missing_source_timestamp += 1
        elif event.exchange and event.token:
            key = (event.exchange, event.token)
            previous = last_feed_time.get(key)
            if previous is not None and event.feed_timestamp_utc < previous:
                out_of_order_events += 1
            last_feed_time[key] = event.feed_timestamp_utc

        if event.feed_timestamp_utc is not None:
            local_timestamp = event.feed_timestamp_utc.astimezone(session_zone)
            local_date = local_timestamp.date()
            if (
                local_date.weekday() not in limits.trading_weekdays
                or local_date in limits.holidays
                or not (
                    limits.session_open_local
                    <= local_timestamp.time().replace(tzinfo=None)
                    <= limits.session_close_local
                )
            ):
                out_of_session_events += 1

        payload = event.payload
        bid = _number(payload.get("bp1"))
        ask = _number(payload.get("sp1"))
        if bid is None or ask is None:
            missing_touchline += 1
        if bid is not None and ask is not None and bid >= ask:
            crossed_quotes += 1
        for size_key in ("bq1", "sq1"):
            size = _number(payload.get(size_key))
            if size is not None and size <= 0:
                non_positive_sizes += 1

    quote_count = len(quote_events)
    missing_ratio = missing_source_timestamp / quote_count if quote_count else 0.0
    missing_symbols = sorted(limits.required_symbols - symbols)
    covered_required = limits.required_symbols.intersection(symbols)
    required_coverage_ratio = (
        len(covered_required) / len(limits.required_symbols)
        if limits.required_symbols
        else 1.0
    )
    interval_coverage: dict[str, float] = {}
    missing_intervals: dict[str, list[str]] = {}
    expected_count = 0
    received_expected_count = 0
    if expected_intervals is not None:
        for symbol, expected in expected_intervals.items():
            expected_set = {
                _aware_utc(timestamp) for timestamp in expected
            }
            observed = received_timestamps.get(symbol, set())
            matched = expected_set.intersection(observed)
            expected_count += len(expected_set)
            received_expected_count += len(matched)
            interval_coverage[symbol] = (
                len(matched) / len(expected_set) if expected_set else 1.0
            )
            missing_intervals[symbol] = sorted(
                timestamp.isoformat() for timestamp in expected_set - observed
            )
    overall_interval_coverage = (
        received_expected_count / expected_count if expected_count else 1.0
    )
    metrics: dict[str, Any] = {
        "raw_events": len(event_list),
        "quote_events": quote_count,
        "session_count": len(sessions),
        "connection_count": len(connections),
        "subscription_generation_count": len(generations),
        "reconnect_generation_count": max(0, len(generations) - len(sessions)),
        "sequence_gaps": sequence_gaps,
        "out_of_session_events": out_of_session_events,
        "symbols": sorted(symbols),
        "symbol_event_counts": dict(sorted(symbol_event_counts.items())),
        "symbol_day_event_counts": dict(sorted(symbol_day_event_counts.items())),
        "missing_source_timestamps": missing_source_timestamp,
        "missing_source_timestamp_ratio": missing_ratio,
        "duplicate_events": duplicate_events,
        "out_of_order_events": out_of_order_events,
        "crossed_quotes": crossed_quotes,
        "missing_touchline": missing_touchline,
        "non_positive_sizes": non_positive_sizes,
        "missing_required_symbols": missing_symbols,
        "required_symbol_coverage_ratio": required_coverage_ratio,
        "expected_intervals": expected_count,
        "received_expected_intervals": received_expected_count,
        "interval_coverage_ratio": overall_interval_coverage,
        "interval_coverage_by_symbol": dict(sorted(interval_coverage.items())),
        "missing_intervals_by_symbol": dict(sorted(missing_intervals.items())),
    }
    reasons: list[str] = []
    if missing_ratio > limits.max_missing_timestamp_ratio:
        reasons.append("missing_source_timestamp")
    if duplicate_events > limits.max_duplicate_events:
        reasons.append("duplicate_events")
    if out_of_order_events > limits.max_out_of_order_events:
        reasons.append("out_of_order_events")
    if crossed_quotes > limits.max_crossed_quotes:
        reasons.append("crossed_quotes")
    if missing_touchline > limits.max_missing_touchline:
        reasons.append("missing_touchline")
    if non_positive_sizes > limits.max_non_positive_sizes:
        reasons.append("non_positive_sizes")
    if sequence_gaps > limits.max_sequence_gaps:
        reasons.append("sequence_gaps")
    if out_of_session_events > limits.max_out_of_session_events:
        reasons.append("out_of_session_events")
    if overall_interval_coverage < limits.min_interval_coverage_ratio:
        reasons.append("interval_coverage")
    if missing_symbols:
        reasons.append("missing_required_symbols")
    return DataHealthReport(metrics=metrics, blocked_reasons=tuple(reasons))


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("expected interval timestamps must be timezone-aware")
    return value.astimezone(ZoneInfo("UTC"))


__all__ = ["DataHealthReport", "DataHealthThresholds", "evaluate_events"]
