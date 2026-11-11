"""Read-only OpenAlgo gateway client for the FYERS-backed data boundary.

Only symbol, quote, depth, history, and already-received WebSocket data are
supported.  Order and account-mutation endpoints are intentionally absent.
"""

from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from jev_imrp.common.logging import redact
from jev_imrp.ingest.quota import QuotaExceeded, QuotaTracker


class OpenAlgoProtocolError(ValueError):
    """The gateway returned an invalid or unsuccessful data response."""

    def __init__(
        self, message: str, *, retryable: bool = False, status_code: int | None = None
    ) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class OpenAlgoConfig:
    """Connection settings for the FYERS-backed OpenAlgo gateway."""

    host: str
    api_key: str = field(repr=False)
    ws_url: str
    upstream_provider: str = "fyers"

    def __post_init__(self) -> None:
        if not self.host or not self.ws_url or not self.api_key:
            raise ValueError("OPENALGO_HOST, OPENALGO_API_KEY and OPENALGO_WS_URL are required")
        if self.upstream_provider.lower() != "fyers":
            raise ValueError("active OpenAlgo gateway must be pinned to upstream provider fyers")
        object.__setattr__(self, "host", self.host.rstrip("/"))
        object.__setattr__(self, "ws_url", self.ws_url.rstrip("/"))
        object.__setattr__(self, "upstream_provider", "fyers")

    @classmethod
    def from_env(cls) -> OpenAlgoConfig:
        return cls(
            host=os.getenv("OPENALGO_HOST", "http://127.0.0.1:5000"),
            api_key=os.getenv("OPENALGO_API_KEY", ""),
            ws_url=os.getenv("OPENALGO_WS_URL", "ws://127.0.0.1:8765"),
            upstream_provider=os.getenv("OPENALGO_UPSTREAM_PROVIDER", "fyers"),
        )


Transport = Callable[[str, dict[str, object]], Mapping[str, object]]


def _urllib_transport(host: str) -> Transport:
    def send(path: str, payload: dict[str, object]) -> Mapping[str, object]:
        request = urllib.request.Request(
            f"{host}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            body = json.loads(response.read())
        if not isinstance(body, Mapping):
            raise OpenAlgoProtocolError("OpenAlgo response must be a JSON object")
        return body

    return send


class OpenAlgoClient:
    """Small injectable client for OpenAlgo's read-only data endpoints."""

    def __init__(
        self,
        config: OpenAlgoConfig,
        transport: Transport | None = None,
        quota: QuotaTracker | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.config = config
        self._transport = transport or _urllib_transport(config.host)
        self._quota = quota
        self._clock = clock or (lambda: datetime.now(UTC))

    def _post(self, path: str, payload: dict[str, object]) -> Any:
        if self._quota is not None:
            try:
                self._quota.acquire(self._clock())
            except QuotaExceeded as exc:
                raise OpenAlgoProtocolError("market-data quota exceeded") from exc
        response = self._transport(path, {"apikey": self.config.api_key, **payload})
        if response.get("status") != "success":
            status_code = _status_code(response)
            raise OpenAlgoProtocolError(
                redact(str(response.get("message", "unsuccessful OpenAlgo response"))),
                retryable=status_code == 429 or (status_code is not None and status_code >= 500),
                status_code=status_code,
            )
        return response.get("data")

    def symbol(self, *, symbol: str, exchange: str) -> Any:
        return self._post("/api/v1/symbol", {"symbol": symbol, "exchange": exchange})

    def quote(self, *, symbol: str, exchange: str) -> Mapping[str, object]:
        data = self._post("/api/v1/quotes", {"symbol": symbol, "exchange": exchange})
        if not isinstance(data, Mapping):
            raise OpenAlgoProtocolError("OpenAlgo quote data must be an object")
        return data

    def depth(self, *, symbol: str, exchange: str) -> Mapping[str, object]:
        data = self._post("/api/v1/depth", {"symbol": symbol, "exchange": exchange})
        if not isinstance(data, Mapping):
            raise OpenAlgoProtocolError("OpenAlgo depth data must be an object")
        return data

    def history(
        self,
        *,
        symbol: str,
        exchange: str,
        interval: str,
        start_date: str,
        end_date: str,
    ) -> list[Mapping[str, object]]:
        data = self._post(
            "/api/v1/history",
            {
                "symbol": symbol,
                "exchange": exchange,
                "interval": interval,
                "start_date": start_date,
                "end_date": end_date,
            },
        )
        if not isinstance(data, list) or not all(isinstance(row, Mapping) for row in data):
            raise OpenAlgoProtocolError("OpenAlgo history data must be a list of objects")
        return data


def parse_websocket_message(raw: str | bytes) -> tuple[str, dict[str, Any]]:
    try:
        value = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise OpenAlgoProtocolError("WebSocket message must be valid JSON object") from exc
    if not isinstance(value, dict):
        raise OpenAlgoProtocolError("WebSocket message must be a valid JSON object")
    kind = value.get("type")
    if kind is None and value.get("status") == "error":
        # Live gateways emit error frames without a ``type`` discriminator.
        kind = "error"
    if kind not in {"auth", "subscribe", "market_data", "quote", "depth", "error", "pong"}:
        raise OpenAlgoProtocolError(f"unsupported message type: {kind!r}")
    return str(kind), value


def _status_code(response: Mapping[str, object]) -> int | None:
    raw_code = response.get("status_code", response.get("code"))
    if isinstance(raw_code, bool):
        return None
    if isinstance(raw_code, int):
        return raw_code
    if isinstance(raw_code, str):
        try:
            return int(raw_code)
        except ValueError:
            return None
    return None


__all__ = ["OpenAlgoClient", "OpenAlgoConfig", "OpenAlgoProtocolError", "parse_websocket_message"]
