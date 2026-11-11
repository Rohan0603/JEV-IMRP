"""Deterministic, fail-closed market-data quota budgeting."""

from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from threading import RLock


@dataclass(frozen=True, slots=True)
class QuotaLimits:
    """Independent Standard/Prime-style request and subscription limits."""

    requests_per_second: int
    requests_per_minute: int
    requests_per_day: int
    quote_symbols_per_request: int
    websocket_symbols_total: int


DEFAULT_STANDARD_LIMITS = QuotaLimits(5, 50, 5_000, 50, 100)


@dataclass(frozen=True, slots=True)
class QuotaRemaining:
    second: int
    minute: int
    day: int


class QuotaExceeded(RuntimeError):
    """Raised when a request would exceed a declared quota."""


class QuotaTracker:
    """In-memory request reservation with exact, injectable timestamps."""

    def __init__(self, limits: QuotaLimits, *, day_timezone: tzinfo = UTC) -> None:
        self.limits = limits
        self.day_timezone = day_timezone
        self._requests: deque[datetime] = deque()
        self._lock = RLock()

    def try_acquire(self, now: datetime, count: int = 1) -> bool:
        """Reserve requests atomically in memory, or leave state unchanged."""
        _validate_count(count)
        now = _utc(now)
        with self._lock:
            self._purge(now)
            remaining = self.remaining(now)
            if count > remaining.second or count > remaining.minute or count > remaining.day:
                return False
            self._requests.extend([now] * count)
            return True

    def acquire(self, now: datetime, count: int = 1) -> None:
        if not self.try_acquire(now, count):
            raise QuotaExceeded("declared market-data quota would be exceeded")

    def remaining(self, now: datetime) -> QuotaRemaining:
        now = _utc(now)
        with self._lock:
            self._purge(now)
            return self._remaining_unlocked(now)

    def report(self, now: datetime) -> dict[str, object]:
        """Return credential-free quota limits, usage, and remaining capacity."""
        now = _utc(now)
        with self._lock:
            self._purge(now)
            second = self._count_since(now - timedelta(seconds=1))
            minute = self._count_since(now - timedelta(minutes=1))
            day_start = self._day_start(now)
            day = self._count_since(day_start)
            return {
                "as_of_utc": now.isoformat(),
                "limits": {
                    "requests_per_second": self.limits.requests_per_second,
                    "requests_per_minute": self.limits.requests_per_minute,
                    "requests_per_day": self.limits.requests_per_day,
                    "quote_symbols_per_request": self.limits.quote_symbols_per_request,
                    "websocket_symbols_total": self.limits.websocket_symbols_total,
                },
                "used": {"second": second, "minute": minute, "day": day},
                "remaining": {
                    "second": max(0, self.limits.requests_per_second - second),
                    "minute": max(0, self.limits.requests_per_minute - minute),
                    "day": max(0, self.limits.requests_per_day - day),
                },
            }

    def snapshot(self, now: datetime) -> dict[str, object]:
        """Serialize active reservations for a process-restart handoff."""
        now = _utc(now)
        with self._lock:
            self._purge(now)
            return {"requests_utc": [timestamp.isoformat() for timestamp in self._requests]}

    @classmethod
    def from_snapshot(
        cls,
        limits: QuotaLimits,
        snapshot: Mapping[str, object],
        *,
        day_timezone: tzinfo = UTC,
    ) -> QuotaTracker:
        """Restore reservations without changing the declared limits."""
        raw_requests = snapshot.get("requests_utc")
        if not isinstance(raw_requests, list) or not all(
            isinstance(value, str) for value in raw_requests
        ):
            raise ValueError("quota snapshot requests_utc must be a list of ISO timestamps")
        tracker = cls(limits, day_timezone=day_timezone)
        try:
            timestamps = [datetime.fromisoformat(value) for value in raw_requests]
            tracker._requests.extend(_utc(value) for value in timestamps)
        except ValueError as exc:
            raise ValueError("quota snapshot contains an invalid timestamp") from exc
        if list(tracker._requests) != sorted(tracker._requests):
            raise ValueError("quota snapshot timestamps must be ordered")
        return tracker

    def validate_quote_batch(self, symbols: object) -> None:
        try:
            count = len(symbols)  # type: ignore[arg-type]
        except TypeError as exc:
            raise ValueError("quote symbols must be a sized collection") from exc
        if count > self.limits.quote_symbols_per_request:
            raise ValueError(
                f"quote request exceeds {self.limits.quote_symbols_per_request} symbols"
            )

    def validate_websocket_symbols(self, symbols: object) -> None:
        try:
            count = len(symbols)  # type: ignore[arg-type]
        except TypeError as exc:
            raise ValueError("websocket symbols must be a sized collection") from exc
        if count > self.limits.websocket_symbols_total:
            raise ValueError(
                "websocket subscription exceeds "
                f"{self.limits.websocket_symbols_total} symbols"
            )

    def _purge(self, now: datetime) -> None:
        cutoff = now - timedelta(days=1)
        while self._requests and self._requests[0] < cutoff:
            self._requests.popleft()

    def _count_since(self, cutoff: datetime) -> int:
        return sum(timestamp >= cutoff for timestamp in self._requests)

    def _remaining_unlocked(self, now: datetime) -> QuotaRemaining:
        second = self._count_since(now - timedelta(seconds=1))
        minute = self._count_since(now - timedelta(minutes=1))
        day_start = self._day_start(now)
        day = self._count_since(day_start)
        return QuotaRemaining(
            second=max(0, self.limits.requests_per_second - second),
            minute=max(0, self.limits.requests_per_minute - minute),
            day=max(0, self.limits.requests_per_day - day),
        )

    def _day_start(self, now: datetime) -> datetime:
        local_now = now.astimezone(self.day_timezone)
        return datetime(
            local_now.year,
            local_now.month,
            local_now.day,
            tzinfo=self.day_timezone,
        ).astimezone(UTC)


def _validate_count(count: int) -> None:
    if count < 1:
        raise ValueError("count must be positive")


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("quota timestamps must be timezone-aware")
    return value.astimezone(UTC)


__all__ = [
    "DEFAULT_STANDARD_LIMITS",
    "QuotaExceeded",
    "QuotaLimits",
    "QuotaRemaining",
    "QuotaTracker",
]
