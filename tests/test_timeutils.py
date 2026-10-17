"""Timezone helpers: IST<->UTC, session boundaries, naive rejection."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from jev_imrp.common.timeutils import (
    IST,
    SESSION_CLOSE,
    SESSION_OPEN,
    ensure_aware,
    from_ist,
    is_within_session,
    ist_session_date,
    minutes_since_open,
    session_bounds_utc,
    to_ist,
    to_utc,
    utc_now,
)

SESSION_DAY = date(2026, 10, 16)  # a Friday


def test_ist_offset_is_fixed_and_aware() -> None:
    assert from_ist(SESSION_DAY, SESSION_OPEN).utcoffset() == timedelta(hours=5, minutes=30)
    assert to_ist(from_ist(SESSION_DAY, SESSION_OPEN)).tzinfo is IST


def test_ist_to_utc_conversion() -> None:
    assert to_utc(from_ist(SESSION_DAY, SESSION_OPEN)) == datetime(
        2026, 10, 16, 3, 45, tzinfo=UTC
    )


def test_utc_to_ist_conversion() -> None:
    assert to_ist(datetime(2026, 10, 16, 3, 45, tzinfo=UTC)) == from_ist(
        SESSION_DAY, SESSION_OPEN
    )


def test_session_boundary_is_the_critical_case() -> None:
    """09:15 IST is 03:45 UTC; a decision 1ms before open is a different UTC day."""
    just_before = to_utc(from_ist(SESSION_DAY, time(9, 14, 59, 999_000)))
    assert just_before == datetime(2026, 10, 16, 3, 44, 59, 999_000, tzinfo=UTC)

    open_utc, close_utc = session_bounds_utc(SESSION_DAY)
    assert open_utc == just_before + timedelta(milliseconds=1)
    assert close_utc == datetime(2026, 10, 16, 10, 0, tzinfo=UTC)

    # 20:00 UTC on the 16th is already 01:30 IST on the 17th.
    late = datetime(2026, 10, 16, 20, 0, tzinfo=UTC)
    assert ist_session_date(late) == date(2026, 10, 17)


def test_session_membership() -> None:
    assert is_within_session(from_ist(SESSION_DAY, SESSION_OPEN))
    assert is_within_session(from_ist(SESSION_DAY, SESSION_CLOSE))
    assert not is_within_session(from_ist(SESSION_DAY, time(9, 14, 59)))
    assert not is_within_session(from_ist(SESSION_DAY, time(15, 31)))


def test_minutes_since_open() -> None:
    assert minutes_since_open(from_ist(SESSION_DAY, SESSION_OPEN)) == 0.0
    assert minutes_since_open(from_ist(SESSION_DAY, time(9, 45))) == 30.0
    assert minutes_since_open(from_ist(SESSION_DAY, SESSION_CLOSE)) == 375.0


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(ValueError, match="naive datetime rejected"):
        ensure_aware(datetime(2026, 10, 16, 9, 15))
    with pytest.raises(ValueError, match="naive datetime rejected"):
        to_utc(datetime(2026, 10, 16, 9, 15))


def test_utc_now_is_aware_utc() -> None:
    now = utc_now()
    assert now.tzinfo is UTC
    assert now.utcoffset() == timedelta(0)


@settings(max_examples=50, deadline=None)
@given(
    offset_minutes=st.integers(min_value=-24 * 60, max_value=24 * 60),
    day=st.integers(min_value=1, max_value=28),
)
def test_ist_utc_round_trip(offset_minutes: int, day: int) -> None:
    ist_time = from_ist(date(2026, 10, day), time(0, 0)) + timedelta(minutes=offset_minutes)
    assert to_ist(to_utc(ist_time)) == ist_time
    assert to_utc(ist_time).tzinfo is UTC
