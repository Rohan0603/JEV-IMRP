from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from jev_imrp.data.exchange_calendar import CalendarError, calendar_from_records


def _calendar():
    return calendar_from_records(
        [
            {"date": "2026-01-02", "status": "open", "open_local": "09:15", "close_local": "15:30"},
            {"date": "2026-01-03", "status": "open", "open_local": "09:15", "close_local": "13:00"},
            {"date": "2026-01-26", "status": "closed"},
        ],
        name="NSE cash equity test calendar",
        timezone="Asia/Kolkata",
        source_uri="https://example.invalid/nse-calendar",
        source_sha256="a" * 64,
    )


def test_calendar_handles_regular_half_day_and_holiday() -> None:
    calendar = _calendar()

    assert calendar.is_trading_day(date(2026, 1, 2))
    assert calendar.bounds_utc(date(2026, 1, 3))[1] == datetime(
        2026, 1, 3, 7, 30, tzinfo=UTC
    )
    assert not calendar.is_trading_day(date(2026, 1, 26))
    assert calendar.contains(datetime(2026, 1, 2, 4, 30, tzinfo=UTC))
    assert not calendar.contains(datetime(2026, 1, 3, 8, 0, tzinfo=UTC))


def test_calendar_fails_closed_for_missing_or_closed_records() -> None:
    calendar = _calendar()

    with pytest.raises(CalendarError, match="no dated calendar record"):
        calendar.is_trading_day(date(2026, 1, 4))
    with pytest.raises(CalendarError, match="closed exchange session"):
        calendar.bounds_utc(date(2026, 1, 26))


@pytest.mark.parametrize(
    "records, match",
    [
        ([{"date": "2026-01-02", "status": "open"}], "both local bounds"),
        ([{"date": "2026-01-02", "status": "closed", "open_local": "09:15"}], "closed session"),
        (
            [
                {
                    "date": "2026-01-02",
                    "status": "open",
                    "open_local": "09:15",
                    "close_local": "09:00",
                }
            ],
            "precede",
        ),
    ],
)
def test_calendar_rejects_invalid_records(records: list[dict[str, str]], match: str) -> None:
    with pytest.raises(CalendarError, match=match):
        _calendar_from_records(records)


def _calendar_from_records(records: list[dict[str, str]]):
    return calendar_from_records(
        records,
        name="test",
        timezone="Asia/Kolkata",
        source_uri="https://example.invalid/calendar",
        source_sha256="b" * 64,
    )


def test_calendar_rejects_invalid_lineage() -> None:
    with pytest.raises(CalendarError, match="SHA-256"):
        calendar_from_records(
            [],
            name="test",
            timezone="Asia/Kolkata",
            source_uri="https://example.invalid/calendar",
            source_sha256="not-a-hash",
        )
