from __future__ import annotations

from datetime import UTC, datetime, timedelta

from jev_imrp.ingest.qualification import (
    QualificationThresholds,
    evaluate_qualification,
    fill_eligible,
)
from jev_imrp.ingest.raw_event import RawEvent


def _event(
    sequence: int,
    received: datetime,
    *,
    source: datetime | None,
    frame: str = "quote",
    bid: float | None = None,
    ask: float | None = None,
    bid_qty: float | None = None,
    ask_qty: float | None = None,
) -> RawEvent:
    payload = {
        "t": "tk" if sequence == 1 else "tf",
        "e": "NSE",
        "tk": "RELIANCE",
        "ti": "RELIANCE",
        "_openalgo_type": frame,
    }
    for key, value in (("bp1", bid), ("sp1", ask), ("bq1", bid_qty), ("sq1", ask_qty)):
        if value is not None:
            payload[key] = value
    return RawEvent(
        session_id="session",
        connection_id="connection",
        subscription_generation=1,
        sequence=sequence,
        message_type=payload["t"],
        payload=payload,
        received_timestamp_utc=received,
        received_monotonic_ns=sequence,
        exchange="NSE",
        token="RELIANCE",
        symbol_as_received="RELIANCE",
        feed_timestamp_raw=None if source is None else str(source.timestamp()),
        feed_timestamp_utc=source,
    ).with_event_id()


def test_quote_without_touchline_is_not_fill_eligible_but_depth_is() -> None:
    now = datetime(2026, 1, 2, 4, 30, tzinfo=UTC)
    quote = _event(1, now, source=now, frame="quote")
    depth = _event(
        2,
        now + timedelta(milliseconds=10),
        source=now + timedelta(milliseconds=10),
        frame="depth",
        bid=100.0,
        ask=100.1,
        bid_qty=10.0,
        ask_qty=12.0,
    )

    assert not fill_eligible(quote, as_of=now)
    assert fill_eligible(depth, as_of=now + timedelta(milliseconds=20))


def test_qualification_reports_lag_stale_and_reconnect_gaps_fail_closed() -> None:
    start = datetime(2026, 1, 2, 4, 30, tzinfo=UTC)
    first = _event(
        1,
        start + timedelta(seconds=3),
        source=start,
        frame="depth",
        bid=100.0,
        ask=100.1,
        bid_qty=10.0,
        ask_qty=10.0,
    )
    report = evaluate_qualification(
        [first],
        thresholds=QualificationThresholds(
            max_source_receive_lag_ms=1000,
            max_stale_gap_ms=1000,
            max_reconnect_gap_ms=1000,
        ),
        capture_end_utc=start + timedelta(seconds=5),
        reconnect_gaps_ms=[2000],
    )

    assert not report.ok
    assert report.metrics["max_source_receive_lag_ms"] == 3000.0
    assert report.metrics["max_stale_gap_ms"] == 2000.0
    assert report.metrics["reconnect_gaps_ms"] == [2000.0]
    assert {
        "source_receive_lag",
        "stale_gap",
        "reconnect_gap",
    }.issubset(report.blocked_reasons)


def test_quote_with_explicit_touchline_is_allowed_without_inference() -> None:
    now = datetime(2026, 1, 2, 4, 30, tzinfo=UTC)
    quote = _event(
        1,
        now,
        source=now,
        frame="quote",
        bid=100.0,
        ask=100.1,
        bid_qty=10.0,
        ask_qty=10.0,
    )
    assert fill_eligible(quote, as_of=now)


def test_missing_source_timestamp_is_a_blocking_metric() -> None:
    now = datetime(2026, 1, 2, 4, 30, tzinfo=UTC)
    report = evaluate_qualification(
        [_event(1, now, source=None, frame="depth", bid=100, ask=100.1, bid_qty=1, ask_qty=1)]
    )
    assert not report.ok
    assert report.metrics["missing_source_timestamps"] == 1
    assert "missing_source_timestamp" in report.blocked_reasons
