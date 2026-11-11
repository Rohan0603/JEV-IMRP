"""Explicitly gated OpenAlgo execution gateway.

This is separate from ``MarketDataProvider`` so data consumers cannot gain
order capability accidentally. Analyze mode is the default and routes to
OpenAlgo's sandbox behavior. Live broker execution requires both
``analyze_mode=False`` and ``allow_live_orders=True`` at construction time.
"""

from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from jev_imrp.common.logging import redact


class OpenAlgoOrderError(RuntimeError):
    """OpenAlgo rejected or could not safely process an order request."""


@dataclass(frozen=True, slots=True)
class OpenAlgoExecutionConfig:
    host: str
    api_key: str = field(repr=False)
    analyze_mode: bool = True
    allow_live_orders: bool = False

    @classmethod
    def from_env(cls) -> OpenAlgoExecutionConfig:
        return cls(
            host=os.getenv("OPENALGO_HOST", "http://127.0.0.1:5000"),
            api_key=os.getenv("OPENALGO_API_KEY", ""),
            analyze_mode=os.getenv("OPENALGO_ANALYZE_MODE", "true").lower() == "true",
            allow_live_orders=os.getenv("OPENALGO_ALLOW_LIVE_ORDERS", "false").lower() == "true",
        )


OrderTransport = Callable[[str, Mapping[str, object]], Mapping[str, object]]


def _urllib_transport(host: str) -> OrderTransport:
    def send(path: str, payload: Mapping[str, object]) -> Mapping[str, object]:
        request = urllib.request.Request(
            f"{host.rstrip('/')}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            body = json.loads(response.read())
        if not isinstance(body, Mapping):
            raise OpenAlgoOrderError("OpenAlgo order response must be a JSON object")
        return body

    return send


class OpenAlgoExecutionGateway:
    """Place/cancel through OpenAlgo with an explicit safety gate."""

    def __init__(
        self,
        config: OpenAlgoExecutionConfig,
        transport: OrderTransport | None = None,
    ) -> None:
        if not config.host or not config.api_key:
            raise ValueError("OPENALGO_HOST and OPENALGO_API_KEY are required")
        if not config.analyze_mode and not config.allow_live_orders:
            raise ValueError("live OpenAlgo orders require allow_live_orders=True")
        self.config = config
        self._transport = transport or _urllib_transport(config.host)

    def place_order(
        self,
        *,
        strategy: str,
        symbol: str,
        exchange: str,
        action: str,
        product: str,
        price_type: str,
        quantity: int,
        price: float = 0.0,
        trigger_price: float = 0.0,
        disclosed_quantity: int = 0,
    ) -> Mapping[str, object]:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        if action not in {"BUY", "SELL"}:
            raise ValueError("action must be BUY or SELL")
        payload = {
            "apikey": self.config.api_key,
            "strategy": strategy,
            "symbol": symbol,
            "exchange": exchange,
            "action": action,
            "product": product,
            "pricetype": price_type,
            "quantity": str(quantity),
            "price": str(price),
            "trigger_price": str(trigger_price),
            "disclosed_quantity": str(disclosed_quantity),
        }
        return self._send("/api/v1/placeorder", payload)

    def cancel_order(self, *, strategy: str, order_id: str) -> Mapping[str, object]:
        if not order_id:
            raise ValueError("order_id is required")
        return self._send(
            "/api/v1/cancelorder",
            {"apikey": self.config.api_key, "strategy": strategy, "orderid": order_id},
        )

    def _send(self, path: str, payload: Mapping[str, object]) -> Mapping[str, object]:
        response = self._transport(path, payload)
        if response.get("status") != "success":
            raise OpenAlgoOrderError(redact(str(response.get("message", "order request failed"))))
        return response


__all__ = ["OpenAlgoExecutionConfig", "OpenAlgoExecutionGateway", "OpenAlgoOrderError"]
