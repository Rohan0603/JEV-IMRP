from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta, timezone

import pytest

from jev_imrp.ingest.quota import (
    DEFAULT_STANDARD_LIMITS,
    QuotaExceeded,
    QuotaTracker,
)


def test_standard_quota_rejects_sixth_request_in_one_second() -> None:
    tracker = QuotaTracker(DEFAULT_STANDARD_LIMITS)
    now = datetime(2026, 1, 2, 10, 0, tzinfo=UTC)

    for _ in range(5):
        assert tracker.try_acquire(now)
    assert not tracker.try_acquire(now)
    assert tracker.remaining(now).second == 0


def test_quota_resets_after_window_and_does_not_consume_rejected_request() -> None:
    tracker = QuotaTracker(DEFAULT_STANDARD_LIMITS)
    now = datetime(2026, 1, 2, 10, 0, tzinfo=UTC)

    for _ in range(5):
        tracker.try_acquire(now)
    with pytest.raises(QuotaExceeded):
        tracker.acquire(now)

    assert tracker.try_acquire(now + timedelta(seconds=1, microseconds=1))
    assert tracker.remaining(now + timedelta(seconds=1, microseconds=1)).second == 4


def test_day_boundary_discards_previous_day_reservations() -> None:
    tracker = QuotaTracker(DEFAULT_STANDARD_LIMITS)
    before_midnight = datetime(2026, 1, 2, 23, 59, 59, tzinfo=UTC)

    for _ in range(5):
        assert tracker.try_acquire(before_midnight)
    next_day = datetime(2026, 1, 3, 0, 0, 1, 1_000, tzinfo=UTC)

    assert tracker.try_acquire(next_day)
    assert tracker.remaining(next_day).day == 4_999


def test_day_boundary_uses_declared_provider_timezone() -> None:
    ist = timezone(timedelta(hours=5, minutes=30))
    tracker = QuotaTracker(DEFAULT_STANDARD_LIMITS, day_timezone=ist)
    before_midnight_utc = datetime(2026, 1, 2, 18, 29, tzinfo=UTC)
    after_midnight_utc = datetime(2026, 1, 2, 18, 31, tzinfo=UTC)

    assert tracker.try_acquire(before_midnight_utc)
    assert tracker.remaining(before_midnight_utc).day == 4_999
    assert tracker.remaining(after_midnight_utc).day == 5_000


def test_symbol_batch_limit_is_fail_closed() -> None:
    tracker = QuotaTracker(DEFAULT_STANDARD_LIMITS)

    with pytest.raises(ValueError, match="50 symbols"):
        tracker.validate_quote_batch(range(51))


def test_websocket_subscription_limit_is_fail_closed() -> None:
    tracker = QuotaTracker(DEFAULT_STANDARD_LIMITS)

    with pytest.raises(ValueError, match="100 symbols"):
        tracker.validate_websocket_symbols(range(101))


def test_quota_report_is_structured_and_credential_free() -> None:
    tracker = QuotaTracker(DEFAULT_STANDARD_LIMITS)
    now = datetime(2026, 1, 2, 10, 0, tzinfo=UTC)
    tracker.acquire(now, count=2)

    report = tracker.report(now)

    assert report["limits"] == {
        "requests_per_second": 5,
        "requests_per_minute": 50,
        "requests_per_day": 5_000,
        "quote_symbols_per_request": 50,
        "websocket_symbols_total": 100,
    }
    assert report["used"] == {"second": 2, "minute": 2, "day": 2}
    assert report["remaining"] == {"second": 3, "minute": 48, "day": 4_998}
    assert "api_key" not in str(report).lower()


def test_quota_snapshot_restores_usage_after_restart() -> None:
    tracker = QuotaTracker(DEFAULT_STANDARD_LIMITS)
    now = datetime(2026, 1, 2, 10, 0, tzinfo=UTC)
    tracker.acquire(now, count=2)

    restored = QuotaTracker.from_snapshot(DEFAULT_STANDARD_LIMITS, tracker.snapshot(now))

    assert restored.remaining(now).second == 3
    with pytest.raises(QuotaExceeded):
        restored.acquire(now, count=4)


def test_concurrent_reservations_cannot_exceed_second_limit() -> None:
    tracker = QuotaTracker(DEFAULT_STANDARD_LIMITS)
    now = datetime(2026, 1, 2, 10, 0, tzinfo=UTC)

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(lambda _: tracker.try_acquire(now), range(20)))

    assert sum(results) == DEFAULT_STANDARD_LIMITS.requests_per_second
