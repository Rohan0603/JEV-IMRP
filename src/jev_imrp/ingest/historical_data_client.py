"""Client for fetching historical data from Shoonya API.

This module provides functionality to fetch historical intraday and EOD data
using the Shoonya API endpoints `TPSeries` and `EODChartData`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from jev_imrp.ingest.symbol_resolver import _get_credentials, _form_request, _parse_response

logger = logging.getLogger(__name__)

#: REST base for authenticated endpoints
REST_BASE = "https://api.shoonya.com/NorenWClientAPI/"


class HistoricalDataError(Exception):
    """Raised when historical data cannot be fetched."""
    pass


class HistoricalDataFetchError(Exception):
    """Raised when historical data fetch fails after retries."""
    pass


MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 1.0


def _fetch_historical_data(
    endpoint: str, payload: Dict[str, str], access_token: str,
) -> Tuple[Dict[str, Any], bool]:
    """Fetch historical data from Shoonya API with retry logic.

    Args:
        endpoint: API endpoint to call (e.g., 'TPSeries', 'EODChartData').
        payload: Dictionary of parameters for the request.
        access_token: Access token for authentication.

    Returns:
        Tuple of (parsed response data, has_more flag indicating if pagination is needed).

    Raises:
        HistoricalDataError: If the request fails after retries.
    """
    req = _form_request(payload, access_token, endpoint=endpoint)

    for attempt in range(MAX_RETRIES):
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                data = _parse_response(response.read())
                has_more = data.get("_has_more", False)
                return data, bool(has_more)
        except urllib.error.HTTPError as exc:
            if attempt < MAX_RETRIES - 1:
                # Check if this is a retryable error (rate limited, timeout, etc.)
                if exc.code in (429, 500, 502, 503, 504):
                    backoff = BASE_BACKOFF_SECONDS * (2 ** attempt)
                    logger.warning(
                        f"Retryable HTTP error {exc.code} fetching {endpoint}, "
                        f"retrying in {backoff}s (attempt {attempt + 1}/{MAX_RETRIES})"
                    )
                    time.sleep(backoff)
                    continue
                raise HistoricalDataError(f"HTTP error fetching {endpoint}: {exc}") from exc
            raise HistoricalDataError(f"HTTP error fetching {endpoint}: {exc}") from exc
        except urllib.request.URLError as exc:
            if attempt < MAX_RETRIES - 1:
                backoff = BASE_BACKOFF_SECONDS * (2 ** attempt)
                logger.warning(
                    f"Retryable URL error fetching {endpoint}, "
                    f"retrying in {backoff}s (attempt {attempt + 1}/{MAX_RETRIES})"
                )
                time.sleep(backoff)
                continue
            raise HistoricalDataError(f"URL error fetching {endpoint}: {exc}") from exc

    raise HistoricalDataError(f"Failed to fetch {endpoint} after {MAX_RETRIES} attempts")


def fetch_tp_series_data(
    exchange: str,
    symbol: str,
    from_date: str,
    to_date: str,
    interval: str = "1m",
    user_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Fetch intraday candles (TPSeries) from Shoonya API.

    Args:
        exchange: Exchange code (e.g., "NSE").
        symbol: Trading symbol (e.g., "SBIN-EQ").
        from_date: Start date in YYYY-MM-DD format.
        to_date: End date in YYYY-MM-DD format.
        interval: Time interval for candles (e.g., "1m", "5m", "15m", "60m", "240m").
        user_id: Shoonya user ID.

    Returns:
        List of candle data dictionaries.

    Raises:
        HistoricalDataError: If the request fails.
    """
    _, _, access_token = _get_credentials()

    all_data: List[Dict[str, Any]] = []
    payload = {
        "uid": user_id or "",
        "exch": exchange,
        "sym": symbol,
        "from": from_date,
        "to": to_date,
        "interval": interval,
    }

    try:
        page_count = 0
        max_pages = 10  # Safety limit to prevent infinite loops
        while page_count < max_pages:
            data, has_more = _fetch_historical_data("TPSeries", payload, access_token)

            # Handle both list and dict response formats
            if isinstance(data, list):
                all_data.extend(data)
            elif isinstance(data, dict):
                candles = data.get("data", [])
                if isinstance(candles, list):
                    all_data.extend(candles)
                else:
                    logger.warning(f"Unexpected 'data' field type in TPSeries response: {type(candles).__name__}")
            else:
                logger.warning(f"Unexpected TPSeries response type: {type(data).__name__}")

            page_count += 1

            if not has_more:
                break

    except HistoricalDataError as exc:
        logger.error(f"Failed to fetch TPSeries data: {exc}")
        raise

    return all_data


def fetch_eod_chart_data(
    exchange: str,
    symbol: str,
    from_date: str,
    to_date: str,
    user_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Fetch daily EOD candles (EODChartData) from Shoonya API.

    Args:
        exchange: Exchange code (e.g., "NSE").
        symbol: Trading symbol (e.g., "SBIN-EQ").
        from_date: Start date in YYYY-MM-DD format.
        to_date: End date in YYYY-MM-DD format.
        user_id: Shoonya user ID.

    Returns:
        List of EOD candle data dictionaries.

    Raises:
        HistoricalDataError: If the request fails.
    """
    _, _, access_token = _get_credentials()

    all_data: List[Dict[str, Any]] = []
    payload = {
        "uid": user_id or "",
        "exch": exchange,
        "sym": symbol,
        "from": from_date,
        "to": to_date,
    }

    try:
        page_count = 0
        max_pages = 10  # Safety limit to prevent infinite loops
        while page_count < max_pages:
            data, has_more = _fetch_historical_data("EODChartData", payload, access_token)

            # Handle both list and dict response formats
            if isinstance(data, list):
                all_data.extend(data)
            elif isinstance(data, dict):
                candles = data.get("data", [])
                if isinstance(candles, list):
                    all_data.extend(candles)
                else:
                    logger.warning(f"Unexpected 'data' field type in EODChartData response: {type(candles).__name__}")
            else:
                logger.warning(f"Unexpected EODChartData response type: {type(data).__name__}")

            page_count += 1

            if not has_more:
                break

    except HistoricalDataError as exc:
        logger.error(f"Failed to fetch EODChartData: {exc}")
        raise

    return all_data


__all__ = [
    "fetch_tp_series_data",
    "fetch_eod_chart_data",
    "HistoricalDataError",
    "HistoricalDataFetchError",
]
