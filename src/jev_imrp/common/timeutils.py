"""Timezone-aware time helpers (plan section 5.3).

Storage is always UTC and always timezone-aware. Naive datetimes are rejected on
the way in rather than silently assumed to be UTC or IST.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta, timezone, tzinfo

IST: tzinfo = timezone(timedelta(hours=5, minutes=30))  # Asia/Kolkata, no DST since 1945

#: NSE cash-equity continuous session, IST.
SESSION_OPEN: time = time(9, 15)
SESSION_CLOSE: time = time(15, 30)


def ensure_aware(value: datetime) -> datetime:
    """Reject naive datetimes; return an aware one otherwise."""
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError(f"naive datetime rejected: {value!r}; attach UTC or IST explicitly")
    return value


def utc_now() -> datetime:
    """Current time as an aware UTC datetime."""
    return datetime.now(UTC)


def to_utc(value: datetime) -> datetime:
    """Convert any aware datetime to UTC."""
    return ensure_aware(value).astimezone(UTC)


def to_ist(value: datetime) -> datetime:
    """Convert any aware datetime to IST (fixed +05:30)."""
    return ensure_aware(value).astimezone(IST)


def from_ist(day: date, clock: time) -> datetime:
    """Build an aware IST datetime for a session day and wall-clock time."""
    return datetime.combine(day, clock, tzinfo=IST)


def ist_session_date(value: datetime) -> date:
    """Trading-session date (IST calendar date) of an aware timestamp."""
    return to_ist(value).date()


def is_within_session(value: datetime, clock_open: time = SESSION_OPEN,
                      clock_close: time = SESSION_CLOSE) -> bool:
    """True when the timestamp falls inside the IST continuous session."""
    local = to_ist(value)
    return clock_open <= local.timetz().replace(tzinfo=None) <= clock_close


def session_bounds_utc(day: date) -> tuple[datetime, datetime]:
    """UTC bounds of one IST session day, for as-of joins and purge logic."""
    return to_utc(from_ist(day, SESSION_OPEN)), to_utc(from_ist(day, SESSION_CLOSE))


def minutes_since_open(value: datetime) -> float:
    """Minutes elapsed since the IST session open."""
    local = to_ist(value)
    return (local - from_ist(local.date(), SESSION_OPEN)).total_seconds() / 60.0
