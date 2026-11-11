from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta

import pytest

from jev_imrp.ingest.data_health import DataHealthThresholds, evaluate_events
from jev_imrp.ingest.raw_event import RawEvent


def _event(
    sequence: int,
    received: datetime,
    *,
    symbol: str = "RELIANCE",
    feed_time: datetime | None = None,
    bid: float | None = 100.0,
    ask: float | None = 100.1,
    bid_qty: float | None = 10.0,
    ask_qty: float | None = 10.0,
) -> RawEvent:
    return RawEvent(
        session_id="session",
        connection_id="connection",
        subscription_generation=1,
        sequence=sequence,
        message_type="tk" if sequence == 1 else "tf",
        payload={
            "t": "tk" if sequence == 1 else "tf",
            "e": "NSE",
            "tk": symbol,
            "bp1": bid,
            "sp1": ask,
            "bq1": bid_qty,
            "sq1": ask_qty,
        },
        received_timestamp_utc=received,
        received_monotonic_ns=sequence,
        exchange="NSE",
        token=symbol,
        symbol_as_received=symbol,
        feed_timestamp_utc=feed_time,
        feed_timestamp_raw=None if feed_time is None else str(feed_time.timestamp()),
    ).with_event_id()


def test_missing_source_timestamp_blocks_fill_data_health() -> None:
    now = datetime(2026, 1, 2, 10, 0, tzinfo=UTC)
    report = evaluate_events(
        [_event(1, now)],
        thresholds=DataHealthThresholds(required_symbols=frozenset({"RELIANCE"})),
    )

    assert not report.ok
    assert "missing_source_timestamp" in report.blocked_reasons
    assert report.metrics["quote_events"] == 1


def test_complete_in_order_events_pass_offline_scoreboard() -> None:
    start = datetime(2026, 1, 2, 4, 30, tzinfo=UTC)
    events = [
        _event(1, start, feed_time=start),
        _event(2, start + timedelta(seconds=1), feed_time=start + timedelta(seconds=1)),
    ]

    report = evaluate_events(
        events,
        thresholds=DataHealthThresholds(required_symbols=frozenset({"RELIANCE"})),
    )

    assert report.ok
    assert report.blocked_reasons == ()
    assert report.metrics["duplicate_events"] == 0
    assert report.metrics["out_of_order_events"] == 0


def test_duplicate_and_crossed_events_are_fail_closed() -> None:
    start = datetime(2026, 1, 2, 10, 0, tzinfo=UTC)
    first = _event(1, start, feed_time=start, bid=101.0, ask=100.0)
    duplicate = _event(1, start, feed_time=start, bid=101.0, ask=100.0)

    report = evaluate_events(
        [first, duplicate],
        thresholds=DataHealthThresholds(required_symbols=frozenset({"RELIANCE"})),
    )

    assert not report.ok
    assert report.metrics["duplicate_events"] == 1
    assert report.metrics["crossed_quotes"] == 2
    assert "duplicate_events" in report.blocked_reasons
    assert "crossed_quotes" in report.blocked_reasons


def test_missing_touchline_is_ineligible_for_fill_research() -> None:
    now = datetime(2026, 1, 2, 10, 0, tzinfo=UTC)
    report = evaluate_events(
        [_event(1, now, feed_time=now, bid=None, ask=None)],
        thresholds=DataHealthThresholds(required_symbols=frozenset({"RELIANCE"})),
    )

    assert not report.ok
    assert report.metrics["missing_touchline"] == 1
    assert "missing_touchline" in report.blocked_reasons


def test_non_positive_touchline_sizes_are_fail_closed() -> None:
    now = datetime(2026, 1, 2, 10, 0, tzinfo=UTC)
    report = evaluate_events(
        [_event(1, now, feed_time=now, bid_qty=0.0, ask_qty=-1.0)],
        thresholds=DataHealthThresholds(required_symbols=frozenset({"RELIANCE"})),
    )

    assert not report.ok
    assert report.metrics["non_positive_sizes"] == 2
    assert "non_positive_sizes" in report.blocked_reasons


def test_session_coverage_and_reconnect_sequence_gaps_are_reported() -> None:
    start = datetime(2026, 1, 2, 10, 0, tzinfo=UTC)
    first = _event(1, start, feed_time=start)
    second = replace(
        first,
        subscription_generation=2,
        sequence=3,
        received_timestamp_utc=start + timedelta(seconds=2),
        feed_timestamp_utc=start + timedelta(seconds=2),
        feed_timestamp_raw=str((start + timedelta(seconds=2)).timestamp()),
        event_id="",
    ).with_event_id()

    report = evaluate_events(
        [first, second],
        thresholds=DataHealthThresholds(
            required_symbols=frozenset({"RELIANCE", "TCS"}),
        ),
    )

    assert report.metrics["session_count"] == 1
    assert report.metrics["connection_count"] == 1
    assert report.metrics["subscription_generation_count"] == 2
    assert report.metrics["reconnect_generation_count"] == 1
    assert report.metrics["sequence_gaps"] == 0
    assert report.metrics["required_symbol_coverage_ratio"] == 0.5
    assert "missing_required_symbols" in report.blocked_reasons


def test_sequence_gaps_are_fail_closed() -> None:
    start = datetime(2026, 1, 2, 10, 0, tzinfo=UTC)
    first = _event(1, start, feed_time=start)
    third = _event(3, start + timedelta(seconds=2), feed_time=start + timedelta(seconds=2))

    report = evaluate_events(
        [first, third],
        thresholds=DataHealthThresholds(required_symbols=frozenset({"RELIANCE"})),
    )

    assert report.metrics["sequence_gaps"] == 1
    assert "sequence_gaps" in report.blocked_reasons


def test_session_and_holiday_quality_are_fail_closed() -> None:
    start = datetime(2026, 1, 2, 10, 0, tzinfo=UTC)
    in_session = _event(1, start, feed_time=start)
    outside_session = _event(
        2,
        start + timedelta(hours=7),
        feed_time=datetime(2026, 1, 2, 16, 0, tzinfo=UTC),
    )

    report = evaluate_events(
        [in_session, outside_session],
        thresholds=DataHealthThresholds(
            required_symbols=frozenset({"RELIANCE"}),
            holidays=frozenset({date(2026, 1, 26)}),
        ),
    )

    assert not report.ok
    assert report.metrics["out_of_session_events"] == 1
    assert report.metrics["symbol_event_counts"] == {"RELIANCE": 2}
    assert report.metrics["symbol_day_event_counts"] == {"RELIANCE|2026-01-02": 2}
    assert "out_of_session_events" in report.blocked_reasons


def test_holiday_events_are_not_eligible() -> None:
    holiday_timestamp = datetime(2026, 1, 26, 10, 0, tzinfo=UTC)
    report = evaluate_events(
        [_event(1, holiday_timestamp, feed_time=holiday_timestamp)],
        thresholds=DataHealthThresholds(
            required_symbols=frozenset({"RELIANCE"}),
            holidays=frozenset({date(2026, 1, 26)}),
        ),
    )

    assert report.metrics["out_of_session_events"] == 1
    assert "out_of_session_events" in report.blocked_reasons


def test_expected_interval_coverage_is_reported_and_fail_closed() -> None:
    start = datetime(2026, 1, 2, 4, 30, tzinfo=UTC)
    report = evaluate_events(
        [_event(1, start, feed_time=start)],
        thresholds=DataHealthThresholds(required_symbols=frozenset({"RELIANCE"})),
        expected_intervals={
            "RELIANCE": [start, start + timedelta(seconds=1)],
        },
    )

    assert report.metrics["expected_intervals"] == 2
    assert report.metrics["received_expected_intervals"] == 1
    assert report.metrics["interval_coverage_ratio"] == 0.5
    assert report.metrics["missing_intervals_by_symbol"] == {
        "RELIANCE": ["2026-01-02T04:30:01+00:00"]
    }
    assert "interval_coverage" in report.blocked_reasons


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_duplicate_events": -1},
        {"max_missing_timestamp_ratio": 1.1},
        {"session_open_local": time(15, 30), "session_close_local": time(9, 15)},
        {"trading_weekdays": frozenset({7})},
    ],
)
def test_session_thresholds_reject_invalid_configuration(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        DataHealthThresholds(**kwargs)
