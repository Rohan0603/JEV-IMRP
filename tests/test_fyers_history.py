from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from jev_imrp.ingest.fyers_history import (
    FyersHistoryClient,
    HistoryRequest,
    chunk_history_window,
    drop_incomplete_candles,
    normalize_candles,
)
from jev_imrp.ingest.quota import QuotaLimits, QuotaTracker


def test_intraday_window_is_chunked_at_100_days_with_deduplicating_boundary() -> None:
    chunks = chunk_history_window(
        date(2026, 1, 1), date(2026, 4, 15), resolution="1"
    )

    assert chunks == [
        (date(2026, 1, 1), date(2026, 4, 10)),
        (date(2026, 4, 10), date(2026, 4, 15)),
    ]


def test_daily_window_allows_366_days() -> None:
    chunks = chunk_history_window(date(2025, 1, 1), date(2026, 1, 1), resolution="1D")

    assert chunks == [(date(2025, 1, 1), date(2026, 1, 1))]


def test_seconds_resolution_uses_thirty_day_windows() -> None:
    chunks = chunk_history_window(date(2026, 1, 1), date(2026, 2, 1), resolution="5S")

    assert chunks == [
        (date(2026, 1, 1), date(2026, 1, 30)),
        (date(2026, 1, 30), date(2026, 2, 1)),
    ]


def test_normalize_candles_deduplicates_and_rejects_bad_ohlcv() -> None:
    retrieved_at = datetime(2026, 1, 2, 10, 5, tzinfo=UTC)
    rows = normalize_candles(
        {
            "s": "ok",
            "candles": [
                [1767348000, 10, 12, 9, 11, 100],
                [1767348000, 10, 12, 9, 11, 100],
            ],
        },
        symbol="NSE:RELIANCE-EQ",
        resolution="1",
        retrieved_at_utc=retrieved_at,
    )

    assert len(rows) == 1
    assert rows[0]["timestamp_utc"] == datetime(2026, 1, 2, 10, 0, tzinfo=UTC)
    assert rows[0]["source"] == "fyers"
    assert isinstance(rows[0]["source_response_sha256"], str)
    assert len(rows[0]["source_response_sha256"]) == 64
    assert rows[0]["retrieved_at_utc"] == retrieved_at

    with pytest.raises(ValueError, match="OHLCV"):
        normalize_candles(
            {"s": "ok", "candles": [[1767348000, 10, 8, 9, 11, 100]]},
            symbol="NSE:RELIANCE-EQ",
            resolution="1",
        )


def test_client_sends_documented_history_request_and_accepts_no_data() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def transport(path: str, payload: dict[str, object]) -> dict[str, object]:
        calls.append((path, payload))
        return {"s": "no_data", "candles": []}

    client = FyersHistoryClient(transport=transport)
    result = client.fetch_window(
        HistoryRequest(
            symbol="NSE:RELIANCE-EQ",
            resolution="1D",
            range_from=date(2026, 1, 1),
            range_to=date(2026, 1, 2),
        )
    )

    assert result == []
    assert calls == [
        (
            "/data/history",
            {
                "symbol": "NSE:RELIANCE-EQ",
                "resolution": "1D",
                "date_format": 1,
                "range_from": "2026-01-01",
                "range_to": "2026-01-02",
            },
        )
    ]


def test_client_retries_429_with_quota_reservation_and_aggregates_chunks() -> None:
    responses = iter(
        [
            {"s": "error", "code": 429, "message": "rate limited"},
            {"s": "ok", "candles": [[1767348000, 10, 12, 9, 11, 100]]},
            {"s": "ok", "candles": [[1767348000, 10, 12, 9, 11, 100]]},
        ]
    )
    calls: list[tuple[str, dict[str, object]]] = []
    sleeps: list[float] = []
    tracker = QuotaTracker(QuotaLimits(10, 10, 10, 50, 100))
    now = datetime(2026, 1, 2, 10, 0, tzinfo=UTC)

    def transport(path: str, payload: dict[str, object]) -> dict[str, object]:
        calls.append((path, payload))
        return next(responses)

    client = FyersHistoryClient(
        transport=transport,
        quota=tracker,
        clock=lambda: now,
        sleep=sleeps.append,
        max_attempts=2,
    )
    rows = client.fetch_range(
        symbol="NSE:RELIANCE-EQ",
        resolution="1",
        range_from=date(2026, 1, 1),
        range_to=date(2026, 4, 15),
    )

    assert len(rows) == 1
    assert len(calls) == 3
    assert sleeps == [0.5]
    assert tracker.remaining(now).day == 7


def test_incomplete_current_candle_is_excluded() -> None:
    rows = [
        {"timestamp_utc": datetime(2026, 1, 2, 10, 0, tzinfo=UTC)},
        {"timestamp_utc": datetime(2026, 1, 2, 10, 1, tzinfo=UTC)},
    ]

    completed = drop_incomplete_candles(
        rows,
        resolution="1",
        as_of_utc=datetime(2026, 1, 2, 10, 1, 30, tzinfo=UTC),
    )

    assert completed == rows[:1]
