"""Read-only FYERS v3 historical-candle adapter.

The transport is injected so request construction and candle validation can be
tested without credentials or network access.  Candles are never treated as
bid/ask observations.
"""

from __future__ import annotations

import hashlib
import json
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from jev_imrp.ingest.quota import QuotaExceeded, QuotaTracker


class FyersHistoryError(ValueError):
    """The historical response or request is invalid."""


@dataclass(frozen=True, slots=True)
class HistoryRequest:
    symbol: str
    resolution: str
    range_from: date
    range_to: date

    def __post_init__(self) -> None:
        if not self.symbol or ":" not in self.symbol:
            raise ValueError("FYERS symbol must use EXCHANGE:SYMBOL format")
        if self.range_to < self.range_from:
            raise ValueError("history range_to must not precede range_from")
        _window_days(self.resolution)


HistoryTransport = Callable[[str, dict[str, object]], Mapping[str, object]]


def chunk_history_window(
    range_from: date, range_to: date, *, resolution: str
) -> list[tuple[date, date]]:
    """Split an inclusive range into FYERS-sized windows with one-day overlap."""

    if range_to < range_from:
        raise ValueError("history range_to must not precede range_from")
    span = _window_days(resolution)
    chunks: list[tuple[date, date]] = []
    start = range_from
    while start <= range_to:
        end = min(range_to, start + timedelta(days=span - 1))
        chunks.append((start, end))
        if end == range_to:
            break
        start = end
    return chunks


def normalize_candles(
    response: Mapping[str, object],
    *,
    symbol: str,
    resolution: str,
    retrieved_at_utc: datetime | None = None,
) -> list[dict[str, object]]:
    status = response.get("s")
    if status == "no_data":
        return []
    if status != "ok":
        raise FyersHistoryError(f"FYERS history response failed: {response.get('message', status)}")
    raw_candles = response.get("candles")
    if not isinstance(raw_candles, list):
        raise FyersHistoryError("FYERS history candles must be a list")
    if retrieved_at_utc is not None and retrieved_at_utc.tzinfo is None:
        raise ValueError("retrieved_at_utc must be timezone-aware")
    response_hash = hashlib.sha256(
        json.dumps(response, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()

    by_timestamp: dict[datetime, dict[str, object]] = {}
    for raw in raw_candles:
        if not isinstance(raw, list) or len(raw) < 6:
            raise FyersHistoryError("FYERS candle must contain timestamp and OHLCV")
        timestamp = _epoch_utc(raw[0])
        values = [_number(value) for value in raw[1:6]]
        if any(value is None or not math.isfinite(value) for value in values):
            raise FyersHistoryError("FYERS candle contains non-finite OHLCV")
        open_price, high, low, close, volume = values
        assert open_price is not None
        assert high is not None
        assert low is not None
        assert close is not None
        assert volume is not None
        if low > high or not low <= open_price <= high or not low <= close <= high:
            raise FyersHistoryError("FYERS candle has invalid OHLCV bounds")
        if volume < 0:
            raise FyersHistoryError("FYERS candle volume must not be negative")
        by_timestamp[timestamp] = {
            "symbol": symbol,
            "resolution": resolution,
            "timestamp_utc": timestamp,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "source": "fyers",
            "source_response_sha256": response_hash,
        }
        if retrieved_at_utc is not None:
            by_timestamp[timestamp]["retrieved_at_utc"] = retrieved_at_utc.astimezone(UTC)
    return [by_timestamp[key] for key in sorted(by_timestamp)]


class FyersHistoryClient:
    """Construct and validate FYERS history requests without side effects."""

    def __init__(
        self,
        *,
        transport: HistoryTransport,
        quota: QuotaTracker | None = None,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] | None = None,
        max_attempts: int = 3,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        self._transport = transport
        self._quota = quota
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleep = sleep or time.sleep
        self._max_attempts = max_attempts

    def fetch_window(self, request: HistoryRequest) -> list[dict[str, object]]:
        payload = {
            "symbol": request.symbol,
            "resolution": request.resolution,
            "date_format": 1,
            "range_from": request.range_from.isoformat(),
            "range_to": request.range_to.isoformat(),
        }
        response = self._request(payload)
        return normalize_candles(
            response,
            symbol=request.symbol,
            resolution=request.resolution,
            retrieved_at_utc=self._clock(),
        )

    def fetch_range(
        self,
        *,
        symbol: str,
        resolution: str,
        range_from: date,
        range_to: date,
        as_of_utc: datetime | None = None,
    ) -> list[dict[str, object]]:
        merged: dict[datetime, dict[str, object]] = {}
        for chunk_from, chunk_to in chunk_history_window(
            range_from, range_to, resolution=resolution
        ):
            rows = self.fetch_window(
                HistoryRequest(
                    symbol=symbol,
                    resolution=resolution,
                    range_from=chunk_from,
                    range_to=chunk_to,
                )
            )
            for row in rows:
                timestamp = row["timestamp_utc"]
                if isinstance(timestamp, datetime):
                    merged[timestamp] = row
        rows = [merged[key] for key in sorted(merged)]
        if as_of_utc is not None:
            return drop_incomplete_candles(rows, resolution=resolution, as_of_utc=as_of_utc)
        return rows

    def _request(self, payload: dict[str, object]) -> Mapping[str, object]:
        for attempt in range(1, self._max_attempts + 1):
            if self._quota is not None:
                try:
                    self._quota.acquire(self._clock())
                except QuotaExceeded as exc:
                    raise FyersHistoryError("FYERS history quota exceeded") from exc
            response = self._transport("/data/history", payload)
            status_code = _status_code(response)
            if not _retryable(response, status_code) or attempt == self._max_attempts:
                return response
            self._sleep(min(30.0, 0.5 * float(2 ** (attempt - 1))))
        raise AssertionError("history retry loop must return")


def drop_incomplete_candles(
    rows: list[dict[str, object]], *, resolution: str, as_of_utc: datetime
) -> list[dict[str, object]]:
    """Keep only candles whose full nominal interval ended by ``as_of_utc``."""

    if as_of_utc.tzinfo is None:
        raise ValueError("as_of_utc must be timezone-aware")
    duration = _candle_duration(resolution)
    cutoff = as_of_utc.astimezone(UTC)
    return [
        row
        for row in rows
        if isinstance(row.get("timestamp_utc"), datetime)
        and row["timestamp_utc"] + duration <= cutoff  # type: ignore[operator]
    ]


def _window_days(resolution: str) -> int:
    if resolution in {"D", "1D", "1W", "1M"}:
        return 366
    if resolution in {"5S", "10S", "15S", "30S", "45S"}:
        return 30
    if resolution.isdigit() and int(resolution) > 0:
        return 100
    raise ValueError(f"unsupported FYERS history resolution: {resolution!r}")


def _candle_duration(resolution: str) -> timedelta:
    if resolution in {"D", "1D"}:
        return timedelta(days=1)
    if resolution == "1W":
        return timedelta(days=7)
    if resolution == "1M":
        return timedelta(days=31)
    if resolution.endswith("S") and resolution[:-1].isdigit():
        return timedelta(seconds=int(resolution[:-1]))
    if resolution.isdigit() and int(resolution) > 0:
        return timedelta(minutes=int(resolution))
    raise ValueError(f"unsupported FYERS history resolution: {resolution!r}")


def _status_code(response: Mapping[str, object]) -> int | None:
    raw = response.get("code", response.get("status_code"))
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        try:
            return int(raw)
        except ValueError:
            return None
    return None


def _retryable(response: Mapping[str, object], status_code: int | None) -> bool:
    if status_code == 429 or (status_code is not None and status_code >= 500):
        return True
    return response.get("s") in {"retry", "temporary_error"}


def _epoch_utc(value: Any) -> datetime:
    try:
        timestamp = float(value)
        result = datetime.fromtimestamp(timestamp, tz=UTC)
    except (TypeError, ValueError, OSError, OverflowError) as exc:
        raise FyersHistoryError("FYERS candle timestamp is invalid") from exc
    return result


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


__all__ = [
    "FyersHistoryClient",
    "FyersHistoryError",
    "HistoryRequest",
    "chunk_history_window",
    "drop_incomplete_candles",
    "normalize_candles",
]
