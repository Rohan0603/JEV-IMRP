from __future__ import annotations

import json

import pytest

from jev_imrp.ingest.openalgo_client import (
    OpenAlgoClient,
    OpenAlgoConfig,
    OpenAlgoProtocolError,
    parse_websocket_message,
)


class FakeTransport:
    def __init__(self, response: dict[str, object]) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, object]]] = []

    def __call__(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append((path, payload))
        return self.response


def config() -> OpenAlgoConfig:
    return OpenAlgoConfig("http://openalgo", "secret-key", "ws://openalgo")


def test_quote_uses_read_only_openalgo_route_and_fyers_pin() -> None:
    transport = FakeTransport({"status": "success", "data": {"ltp": 1422.5}})
    result = OpenAlgoClient(config(), transport=transport).quote(
        symbol="RELIANCE", exchange="NSE"
    )

    assert result == {"ltp": 1422.5}
    assert transport.calls == [
        (
            "/api/v1/quotes",
            {"apikey": "secret-key", "symbol": "RELIANCE", "exchange": "NSE"},
        )
    ]


def test_history_requires_a_list_and_errors_are_redacted() -> None:
    client = OpenAlgoClient(
        config(),
        transport=FakeTransport(
            {"status": "error", "message": "api_key=secret-key rejected", "code": 401}
        ),
    )
    with pytest.raises(OpenAlgoProtocolError) as excinfo:
        client.history(
            symbol="RELIANCE",
            exchange="NSE",
            interval="1m",
            start_date="2026-01-01",
            end_date="2026-01-02",
        )
    assert "secret-key" not in str(excinfo.value)


def test_websocket_parser_is_data_only() -> None:
    assert parse_websocket_message(
        json.dumps({"type": "market_data", "exchange": "NSE", "symbol": "RELIANCE"})
    )[0] == "market_data"
    with pytest.raises(OpenAlgoProtocolError, match="unsupported message type"):
        parse_websocket_message(json.dumps({"type": "order_update"}))
