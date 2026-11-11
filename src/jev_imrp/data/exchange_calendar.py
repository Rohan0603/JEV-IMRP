"""Effective-dated exchange calendar boundary.

A calendar is a set of dated session records plus non-dated source lineage. Any
missing date, malformed record, or absent lineage fails closed: the calendar
never invents a holiday or a shortened session.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CalendarError(ValueError):
    """The exchange calendar is missing, malformed, or lacks source lineage."""


@dataclass(frozen=True, slots=True)
class SessionRecord:
    """One dated exchange session, with bounds already converted to UTC."""

    day: date
    is_open: bool
    open_utc: datetime | None
    close_utc: datetime | None


@dataclass(frozen=True, slots=True)
class ExchangeCalendar:
    """Immutable, effective-dated exchange calendar."""

    name: str
    timezone: str
    source_uri: str
    source_sha256: str
    sessions: Mapping[date, SessionRecord]

    def is_trading_day(self, day: date) -> bool:
        record = self.sessions.get(day)
        if record is None:
            raise CalendarError(f"no dated calendar record for {day.isoformat()}")
        return record.is_open

    def bounds_utc(self, day: date) -> tuple[datetime, datetime]:
        record = self.sessions.get(day)
        if record is None:
            raise CalendarError(f"no dated calendar record for {day.isoformat()}")
        if not record.is_open or record.open_utc is None or record.close_utc is None:
            raise CalendarError(f"closed exchange session on {day.isoformat()}")
        return record.open_utc, record.close_utc

    def contains(self, moment: datetime) -> bool:
        if moment.tzinfo is None:
            raise CalendarError("calendar range checks require a timezone-aware datetime")
        local_day = moment.astimezone(ZoneInfo(self.timezone)).date()
        try:
            start, end = self.bounds_utc(local_day)
        except CalendarError:
            return False
        return start <= moment.astimezone(UTC) <= end


def calendar_from_records(
    records: Iterable[Mapping[str, str]],
    *,
    name: str,
    timezone: str,
    source_uri: str,
    source_sha256: str,
) -> ExchangeCalendar:
    """Build a calendar, rejecting malformed records and absent lineage."""

    if not _SHA256.fullmatch(source_sha256):
        raise CalendarError("source_sha256 must be a 64-character lowercase SHA-256 digest")
    if not name or not source_uri:
        raise CalendarError("calendar name and source_uri are required")
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise CalendarError(f"unknown calendar timezone {timezone!r}") from exc

    sessions: dict[date, SessionRecord] = {}
    for record in records:
        raw_day = record.get("date")
        try:
            day = date.fromisoformat(str(raw_day))
        except ValueError as exc:
            raise CalendarError(f"invalid calendar date {raw_day!r}") from exc
        if day in sessions:
            raise CalendarError(f"duplicate calendar record for {day.isoformat()}")

        status = record.get("status")
        open_local = record.get("open_local")
        close_local = record.get("close_local")
        if status == "closed":
            if open_local or close_local:
                raise CalendarError(
                    f"closed session for {day.isoformat()} must not define local bounds"
                )
            sessions[day] = SessionRecord(day, is_open=False, open_utc=None, close_utc=None)
        elif status == "open":
            if not open_local or not close_local:
                raise CalendarError(
                    f"open session for {day.isoformat()} requires both local bounds"
                )
            start = _local_to_utc(day, open_local, zone)
            end = _local_to_utc(day, close_local, zone)
            if end <= start:
                raise CalendarError(
                    f"open_local must precede close_local for {day.isoformat()}"
                )
            sessions[day] = SessionRecord(day, is_open=True, open_utc=start, close_utc=end)
        else:
            raise CalendarError(
                f"unsupported calendar status {status!r} for {day.isoformat()}"
            )

    return ExchangeCalendar(
        name=name,
        timezone=timezone,
        source_uri=source_uri,
        source_sha256=source_sha256,
        sessions=sessions,
    )


def _local_to_utc(day: date, value: str, zone: ZoneInfo) -> datetime:
    try:
        local_time = time.fromisoformat(value)
    except ValueError as exc:
        raise CalendarError(f"invalid local time {value!r}") from exc
    return datetime.combine(day, local_time, tzinfo=zone).astimezone(UTC)


__all__ = ["CalendarError", "ExchangeCalendar", "SessionRecord", "calendar_from_records"]
