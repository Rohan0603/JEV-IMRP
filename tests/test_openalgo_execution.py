from __future__ import annotations

import pytest

from jev_imrp.execution.openalgo_gateway import (
    OpenAlgoExecutionConfig,
    OpenAlgoExecutionGateway,
)


def test_live_execution_requires_explicit_opt_in() -> None:
    with pytest.raises(ValueError, match="allow_live_orders"):
        OpenAlgoExecutionGateway(
            OpenAlgoExecutionConfig(
                host="http://openalgo", api_key="secret", analyze_mode=False
            )
        )


def test_analyze_order_uses_only_openalgo_order_route() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def transport(path: str, payload: dict[str, object]) -> dict[str, object]:
        calls.append((path, payload))
        return {"status": "success", "orderid": "sandbox-1"}

    gateway = OpenAlgoExecutionGateway(
        OpenAlgoExecutionConfig(host="http://openalgo", api_key="secret"),
        transport=transport,
    )
    result = gateway.place_order(
        strategy="JEV-IMRP",
        symbol="RELIANCE",
        exchange="NSE",
        action="BUY",
        product="MIS",
        price_type="MARKET",
        quantity=1,
    )

    assert result["orderid"] == "sandbox-1"
    assert calls == [
        (
            "/api/v1/placeorder",
            {
                "apikey": "secret",
                "strategy": "JEV-IMRP",
                "symbol": "RELIANCE",
                "exchange": "NSE",
                "action": "BUY",
                "product": "MIS",
                "pricetype": "MARKET",
                "quantity": "1",
                "price": "0.0",
                "trigger_price": "0.0",
                "disclosed_quantity": "0",
            },
        )
    ]


def test_order_error_does_not_echo_api_key() -> None:
    gateway = OpenAlgoExecutionGateway(
        OpenAlgoExecutionConfig(host="http://openalgo", api_key="secret"),
        transport=lambda path, payload: {
            "status": "error",
            "message": "api_key=secret rejected",
        },
    )
    with pytest.raises(RuntimeError) as excinfo:
        gateway.cancel_order(strategy="JEV-IMRP", order_id="x")
    assert "secret" not in str(excinfo.value)
