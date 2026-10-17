"""Timestamp semantics (plan 5.3) and the time-dependent data layers.

Covers: UTC storage, IST derivation, session boundaries, feed-clock caveats, bar
bucket/availability/completeness separation, and the point-in-time context join.
"""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jev_imrp.common.timeutils import (
    IST,
    SESSION_OPEN,
    ensure_aware,
    from_ist,
    is_within_session,
    ist_session_date,
    minutes_since_open,
    session_bounds_utc,
    to_ist,
    to_utc,
)
from jev_imrp.data.bars import build_bars_1m
from jev_imrp.data.context_join import load_market_context
from jev_imrp.data.quality import build_health_report
from jev_imrp.ingest.feed_state import QuoteStateBook
from jev_imrp.ingest.file_writer import RawEventWriter
from jev_imrp.ingest.raw_event import parse_feed_timestamp
from jev_imrp.ingest.shoonya_recorder import Recorder
from synthetic_feed import FixedClock, load_fixture, tick_sizes


# --- UTC storage and IST derivation --------------------------------------------
def test_naive_datetimes_are_rejected():
    with pytest.raises(ValueError):
        ensure_aware(datetime(2026, 7, 1, 9, 15))
    with pytest.raises(ValueError):
        to_utc(datetime(2026, 7, 1, 9, 15))  # type: ignore[arg-type]


def test_utc_storage_and_ist_derivation():
    moment = datetime(2026, 7, 1, 4, 5, tzinfo=UTC)
    assert to_ist(moment) == datetime(2026, 7, 1, 9, 35, tzinfo=IST)
    assert to_utc(to_ist(moment)) == moment
    assert to_ist(moment).utcoffset() == timedelta(hours=5, minutes=30)


def test_session_boundaries():
    day = date(2026, 7, 1)
    open_utc, close_utc = session_bounds_utc(day)
    assert open_utc == datetime(2026, 7, 1, 3, 45, tzinfo=UTC)
    assert close_utc == datetime(2026, 7, 1, 10, 0, tzinfo=UTC)
    assert is_within_session(open_utc)
    assert is_within_session(close_utc)
    assert not is_within_session(open_utc - timedelta(seconds=1))
    assert not is_within_session(close_utc + timedelta(seconds=1))
    assert minutes_since_open(open_utc + timedelta(minutes=30)) == 30.0
    assert ist_session_date(open_utc) == day
    assert ist_session_date(close_utc) == day


def test_session_boundaries_around_ist_midnight():
    """IST session dates must not drift into the neighbouring UTC day."""
    late = from_ist(date(2026, 7, 1), time(15, 29, 59))
    assert ist_session_date(late) == date(2026, 7, 1)
    early = from_ist(date(2026, 7, 2), SESSION_OPEN)
    assert ist_session_date(early) == date(2026, 7, 2)
    assert early.astimezone(UTC).hour == 3


# --- feed clock caveat ----------------------------------------------------------
def test_feed_timestamp_parsed_from_epoch_seconds_and_never_defaulted():
    parsed = parse_feed_timestamp("1790200000")
    assert parsed == datetime.fromtimestamp(1790200000, tz=UTC)
    assert parsed.tzinfo is UTC
    assert parse_feed_timestamp(None) is None
    assert parse_feed_timestamp("") is None
    assert parse_feed_timestamp("2026-07-01") is None, "only epoch seconds are documented"
    assert parse_feed_timestamp("not-a-number") is None


def test_feed_and_receive_time_are_independent_columns(tmp_path):
    fixture = load_fixture("session_normal")
    writer = RawEventWriter(tmp_path / "raw", session_id="synthetic")
    recorder = Recorder(writer=writer, book=QuoteStateBook(tick_sizes=tick_sizes(fixture)),
                        session_id="synthetic", clock=FixedClock(), data_origin="synthetic")
    recorder.start()
    event, state = recorder.feed(fixture["messages"][1])
    recorder.close()

    row = state.to_row()
    assert row["feed_timestamp_utc"].tzinfo is UTC
    assert row["received_timestamp_utc"].tzinfo is UTC
    assert row["feed_timestamp_utc"] != row["received_timestamp_utc"]
    assert event.received_monotonic_ns > 0  # local duration/latency source


# --- bars: bucket time, availability and completeness are distinct -------------
#: Completeness horizon used by the bar tests: after the last observed update.
COMPLETE_THROUGH = datetime(2026, 7, 1, 3, 49, tzinfo=UTC)


def states_frame() -> pd.DataFrame:
    """Quote-state rows for one symbol across three minutes, all valid."""
    base = datetime(2026, 7, 1, 3, 45, tzinfo=UTC)
    rows = []
    prices = [
        (0, "100.00", "500"),
        (0, "101.00", "600"),
        (1, "102.00", "700"),
        (3, "103.00", "900"),
    ]
    for minute, price, volume in prices:
        moment = base + timedelta(minutes=minute)
        rows.append(
            {
                "event_id": f"e{minute}{price}",
                "symbol": "SYNTHETIC-A",
                "exchange": "NSE",
                "state_valid": True,
                "invalid_reason": None,
                "message_type": "tf",
                "last_price": float(price),
                "volume": float(volume),
                "feed_timestamp_utc": moment,
                "received_timestamp_utc": moment + timedelta(milliseconds=250),
                "quote_age_ms": 250.0,
            }
        )
    return pd.DataFrame(rows)


def test_bars_bucket_by_source_time_and_report_availability():
    bars = build_bars_1m(states_frame(), complete_through_utc=COMPLETE_THROUGH)
    assert len(bars) == 3, "three distinct minutes of data"

    first = bars.iloc[0]
    assert first["bar_start_utc"] == datetime(2026, 7, 1, 3, 45, tzinfo=UTC)
    assert first["bar_end_utc"] == datetime(2026, 7, 1, 3, 46, tzinfo=UTC)
    assert first["open_price"] == 100.0
    assert first["close_price"] == 101.0
    assert first["trade_updates"] == 2
    # bar time and availability are different facts
    assert first["available_at_utc"] == datetime(2026, 7, 1, 3, 45, 0, 250000, tzinfo=UTC)
    assert first["source_time_basis"] == "feed_timestamp_utc"


def test_bars_completeness_follows_the_horizon_not_the_bar_time():
    frame = states_frame()
    early = build_bars_1m(frame, complete_through_utc=datetime(2026, 7, 1, 3, 45, 30, tzinfo=UTC))
    assert early["bar_complete"].tolist() == [False, False, False]

    late = build_bars_1m(frame, complete_through_utc=datetime(2026, 7, 1, 3, 49, tzinfo=UTC))
    assert late["bar_complete"].all()
    # incomplete bars are still written and flagged, never dropped
    assert len(late) == 3


def test_bars_flag_gaps_and_volume_convention():
    bars = build_bars_1m(states_frame(), complete_through_utc=COMPLETE_THROUGH)
    assert bars["minutes_since_previous_bar"].tolist() == [1, 1, 2]
    assert bars["gap_before"].tolist() == [False, False, True]

    # minute volume is a difference of the cumulative field; the first bar has no
    # earlier observation, so its volume is null rather than zero
    assert pd.isna(bars.iloc[0]["volume"])
    assert bars.iloc[1]["volume"] == 100.0
    assert bars.iloc[2]["volume"] == 200.0
    assert bars.iloc[1]["volume_basis"] == "cumulative_v_difference"


def test_bars_reject_invalid_states_and_count_them():
    frame = states_frame()
    frame.loc[1, "state_valid"] = False
    bars = build_bars_1m(frame)
    assert bars.attrs["rows_rejected_invalid"] == 1
    assert bars.iloc[0]["trade_updates"] == 1, "the rejected row contributed no trade"
    assert bars.iloc[0]["updates_total"] == 1, "counts describe contributing (valid) updates"


def test_bars_ignore_updates_without_a_price_but_count_them():
    frame = states_frame()
    frame.loc[1, "last_price"] = None
    bars = build_bars_1m(frame)
    assert bars.iloc[0]["trade_updates"] == 1
    assert bars.iloc[0]["updates_without_price"] == 1
    assert bars.iloc[0]["close_price"] == 100.0


# --- market context: availability time, not source time, gates the join ---------
def context_rows() -> list[dict]:
    base = datetime(2026, 7, 1, 3, 45, tzinfo=UTC)
    return [
        {
            "context_name": "india_vix",
            "source_timestamp_utc": base,  # published at 03:45
            "available_at_utc": base + timedelta(seconds=30),  # delivered at 03:45:30
            "value": 14.2,
            "context_version": "v1",
            "source": "synthetic",
        },
        {
            "context_name": "india_vix",
            "source_timestamp_utc": base + timedelta(minutes=5),
            "available_at_utc": base + timedelta(minutes=5, seconds=30),
            "value": 15.1,
            "context_version": "v1",
            "source": "synthetic",
        },
        {
            "context_name": "nifty_close",
            "source_timestamp_utc": base + timedelta(minutes=5),
            "available_at_utc": base + timedelta(minutes=6),
            "value": 25000.0,
            "context_version": "v1",
            "source": "synthetic",
        },
    ]


def test_context_join_excludes_values_not_yet_available():
    context = load_market_context(context_rows())
    base = datetime(2026, 7, 1, 3, 45, tzinfo=UTC)

    assert context.asof("india_vix", base + timedelta(seconds=10)) is None
    first = context.asof("india_vix", base + timedelta(seconds=45))
    assert first["value"] == 14.2
    later = context.asof("india_vix", base + timedelta(minutes=6))
    assert later["value"] == 15.1


def test_context_staleness_is_flagged_against_the_decision_time():
    context = load_market_context(context_rows())
    base = datetime(2026, 7, 1, 3, 45, tzinfo=UTC)

    fresh = context.asof("india_vix", base + timedelta(seconds=45), max_staleness_seconds=900)
    assert fresh["context_stale"] is False
    stale = context.asof("india_vix", base + timedelta(minutes=30), max_staleness_seconds=60)
    assert stale["context_stale"] is True
    assert stale["context_age_seconds"] > 60


def test_context_rejects_rows_without_an_availability_timestamp():
    rows = context_rows()
    rows[0]["available_at_utc"] = None
    with pytest.raises(ValueError, match="availability"):
        load_market_context(rows)


def test_context_join_on_decisions_never_reads_the_future():
    context = load_market_context(context_rows())
    base = datetime(2026, 7, 1, 3, 45, tzinfo=UTC)
    decisions = pd.DataFrame(
        {
            "decision_timestamp_utc": [
                base + timedelta(seconds=20),
                base + timedelta(minutes=6, seconds=10),
            ]
        }
    )
    joined = context.join(decisions, max_staleness_seconds=900)

    assert pd.isna(joined["context_india_vix_value"].iloc[0])
    assert joined["context_india_vix_value"].iloc[1] == 15.1
    assert pd.isna(joined["context_nifty_close_value"].iloc[0])
    assert joined["context_nifty_close_value"].iloc[1] == 25000.0


def test_context_join_rejects_a_naive_decision_timestamp():
    context = load_market_context(context_rows())
    with pytest.raises(ValueError, match="timezone-aware"):
        context.asof("india_vix", datetime(2026, 7, 1, 3, 45))


# --- quality report -------------------------------------------------------------
def test_health_report_counts_coverage_and_anomalies():
    frame = states_frame()
    frame.loc[2, "state_valid"] = False
    frame.loc[2, "invalid_reason"] = "crossed_quote"
    frame.loc[3, "quote_age_ms"] = 9_000.0

    report = build_health_report(frame, max_quote_age_ms=5_000)
    assert report.rows_total == 4
    assert report.rows_valid == 3
    assert report.rows_rejected_invalid == 1
    assert report.rows_stale == 1
    assert report.invalid_reason_counts == {"crossed_quote": 1}
    assert report.sessions_covered == ["2026-07-01"]
    assert report.coverage_by_symbol_session == {"SYNTHETIC-A|2026-07-01": 3}
    assert "1 quote-state row(s) were invalid" in report.blocking_problems()


def test_health_report_on_empty_input_is_explicit():
    report = build_health_report(pd.DataFrame())
    assert report.rows_total == 0
    assert report.notes == ["no quote states supplied"]
    assert report.blocking_problems() == ["no symbol produced a valid quote state"]


def test_health_report_includes_bar_gaps():
    frame = states_frame()
    bars = build_bars_1m(frame)
    report = build_health_report(frame, bars)
    assert report.minutes_since_previous_bar_total == 1
    assert report.bars_missing_price == 0
    assert report.thresholds["max_quote_age_ms"] == 5_000.0


