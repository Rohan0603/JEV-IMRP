"""Read-only FYERS v3 latest-quote client.

This module owns only the FYERS market-data quotes endpoint.  It deliberately
has no account, order, or position methods.  The transport is injected so
request construction and response validation remain testable without network
access or credentials.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


class FyersQuoteError(ValueError):
    """The FYERS quote request or response is invalid."""

    def __init__(self, message: str, *, auth_expired: bool = False) -> None:
        super().__init__(message)
        self.auth_expired = auth_expired


QuoteTransport = Callable[[str, Mapping[str, str], Mapping[str, object]], Any]
DEFAULT_QUOTES_ENDPOINT = "https://api-t1.fyers.in/data/quotes"


@dataclass(slots=True)
class FyersQuoteClient:
    """Small, read-only FYERS latest-quote client."""

    app_id: str
    access_token: str
    transport: QuoteTransport
    endpoint: str = DEFAULT_QUOTES_ENDPOINT
    reauthenticate: Callable[[], str] | None = None

    @classmethod
    def from_env(
        cls,
        *,
        transport: QuoteTransport,
        endpoint: str | None = None,
        reauthenticate: Callable[[], str] | None = None,
    ) -> FyersQuoteClient:
        app_id = os.environ.get("FYERS_APP_ID", "").strip()
        access_token = os.environ.get("FYERS_ACCESS_TOKEN", "").strip()
        if not app_id or not access_token:
            raise FyersQuoteError(
                "FYERS_APP_ID and FYERS_ACCESS_TOKEN are required for direct read-only quotes"
            )
        return cls(
            app_id=app_id,
            access_token=access_token,
            transport=transport,
            endpoint=endpoint or DEFAULT_QUOTES_ENDPOINT,
            reauthenticate=reauthenticate,
        )

    @property
    def authorization(self) -> str:
        return f"{self.app_id}:{self.access_token}"

    def quotes(self, symbols: Sequence[str]) -> Mapping[str, Mapping[str, object]]:
        unique = list(dict.fromkeys(symbols))
        if not unique or len(unique) > 50:
            raise FyersQuoteError("FYERS quotes requires 1 to 50 unique symbols")
        if any(not isinstance(symbol, str) or not symbol for symbol in unique):
            raise FyersQuoteError("FYERS quote symbols must be non-empty strings")
        payload = {"symbols": ",".join(unique)}
        for attempt in range(2):
            try:
                response = self.transport(
                    self.endpoint,
                    {"Authorization": self.authorization},
                    payload,
                )
                break
            except FyersQuoteError as exc:
                if attempt or not exc.auth_expired or self.reauthenticate is None:
                    raise
                refreshed = self.reauthenticate()
                if not isinstance(refreshed, str) or not refreshed:
                    raise FyersQuoteError(
                        "FYERS re-authentication returned no access token"
                    ) from exc
                self.access_token = refreshed
        else:
            raise AssertionError("FYERS quote retry loop must return")
        if not isinstance(response, Mapping) or response.get("s") != "ok":
            status = response.get("s") if isinstance(response, Mapping) else None
            raise FyersQuoteError(f"FYERS quotes request failed: {status!r}")
        raw_d = response.get("d")
        if not isinstance(raw_d, list):
            raise FyersQuoteError("FYERS quotes response must contain a list in 'd'")
        result: dict[str, Mapping[str, object]] = {}
        for item in raw_d:
            if not isinstance(item, Mapping):
                raise FyersQuoteError("FYERS quote item must be an object")
            symbol = item.get("n")
            values = item.get("v")
            if not isinstance(symbol, str) or not isinstance(values, Mapping):
                raise FyersQuoteError("FYERS quote item must contain symbol 'n' and value 'v'")
            # The current FYERS response puts the success status on the
            # top-level object; per-symbol ``v`` objects do not include ``s``.
            # A per-symbol error is represented by a missing/non-mapping value,
            # which was rejected above.
            result[symbol] = dict(values)
        return result


def httpx_quote_transport(
    endpoint: str, headers: Mapping[str, str], payload: Mapping[str, object]
) -> Mapping[str, object]:
    """Perform one authenticated GET; never exposes the token in errors."""

    import httpx

    # FYERS currently sends only the comma-separated ``symbols`` query value,
    # but keep the transport boundary generic for injected test transports.
    query_params = {key: str(value) for key, value in payload.items()}
    response = httpx.get(endpoint, params=query_params, headers=dict(headers), timeout=15.0)
    if response.status_code == 401:
        raise FyersQuoteError(
            "FYERS rejected the access token (401); refresh FYERS_ACCESS_TOKEN and retry",
            auth_expired=True,
        )
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, Mapping):
        raise FyersQuoteError("FYERS quotes response must be a JSON object")
    return value


__all__ = [
    "DEFAULT_QUOTES_ENDPOINT",
    "FyersQuoteClient",
    "FyersQuoteError",
    "httpx_quote_transport",
]
