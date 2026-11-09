"""Offline contract tests for the OpenAlgo data-only adapter."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from jev_imrp.ingest.openalgo_client import (
    OpenAlgoClient,
    OpenAlgoConfig,
    OpenAlgoProtocolError,
    parse_websocket_message,
)
from jev_imrp.ingest.quota import QuotaLimits, QuotaTracker


class FakeTransport:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = iter(responses)
        self.calls: list[tuple[str, dict[str, object]]] = []

    def __call__(self, path: str, payload: dict[str, object]) -> dict[str, object]:
        self.calls.append((path, payload))
        return next(self.responses)


def test_config_reads_openalgo_environment_without_exposing_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENALGO_HOST", "http://127.0.0.1:5000/")
    monkeypatch.setenv("OPENALGO_API_KEY", "secret-key")
    monkeypatch.setenv("OPENALGO_WS_URL", "ws://127.0.0.1:8765")

    config = OpenAlgoConfig.from_env()

    assert config.host == "http://127.0.0.1:5000"
    assert config.ws_url == "ws://127.0.0.1:8765"
    assert "secret-key" not in repr(config)


def test_quote_request_uses_only_read_only_openalgo_contract() -> None:
    transport = FakeTransport([{"status": "success", "data": {"ltp": 1422.5}}])
    client = OpenAlgoClient(
        OpenAlgoConfig(host="http://openalgo", api_key="secret-key", ws_url="ws://openalgo"),
        transport=transport,
    )

    quote = client.quote(symbol="RELIANCE", exchange="NSE")

    assert quote == {"ltp": 1422.5}
    assert transport.calls == [
        ("/api/v1/quotes", {"apikey": "secret-key", "symbol": "RELIANCE", "exchange": "NSE"})
    ]


def test_depth_request_uses_only_read_only_openalgo_contract() -> None:
    transport = FakeTransport([{"status": "success", "data": {"bids": [], "asks": []}}])
    client = OpenAlgoClient(
        OpenAlgoConfig(host="http://openalgo", api_key="secret-key", ws_url="ws://openalgo"),
        transport=transport,
    )

    depth = client.depth(symbol="RELIANCE", exchange="NSE")

    assert depth == {"bids": [], "asks": []}
    assert transport.calls == [
        ("/api/v1/depth", {"apikey": "secret-key", "symbol": "RELIANCE", "exchange": "NSE"})
    ]


def test_history_request_normalizes_interval_and_rejects_error_response() -> None:
    transport = FakeTransport([{"status": "success", "data": [{"time": 1, "close": 10.0}]}])
    client = OpenAlgoClient(
        OpenAlgoConfig(host="http://openalgo", api_key="secret-key", ws_url="ws://openalgo"),
        transport=transport,
    )

    candles = client.history(
        symbol="RELIANCE",
        exchange="NSE",
        interval="1m",
        start_date="2026-01-01",
        end_date="2026-01-02",
    )

    assert candles == [{"time": 1, "close": 10.0}]
    assert transport.calls[0] == (
        "/api/v1/history",
        {
            "apikey": "secret-key",
            "symbol": "RELIANCE",
            "exchange": "NSE",
            "interval": "1m",
            "start_date": "2026-01-01",
            "end_date": "2026-01-02",
        },
    )

    failing = OpenAlgoClient(
        OpenAlgoConfig(host="http://openalgo", api_key="secret-key", ws_url="ws://openalgo"),
        transport=FakeTransport([{"status": "error", "message": "bad request"}]),
    )
    with pytest.raises(OpenAlgoProtocolError, match="bad request"):
        failing.history(
            symbol="RELIANCE",
            exchange="NSE",
            interval="1d",
            start_date="2026-01-01",
            end_date="2026-01-02",
        )


def test_provider_error_does_not_echo_api_key() -> None:
    key = "secret-key"
    client = OpenAlgoClient(
        OpenAlgoConfig(host="http://openalgo", api_key=key, ws_url="ws://openalgo"),
        transport=FakeTransport(
            [{"status": "error", "message": f"api_key={key} rejected"}]
        ),
    )

    with pytest.raises(OpenAlgoProtocolError) as excinfo:
        client.symbol(symbol="RELIANCE", exchange="NSE")

    assert key not in str(excinfo.value)
    assert "REDACTED" in str(excinfo.value)


@pytest.mark.parametrize("code", [429, 500, 502])
def test_provider_rate_and_server_failures_are_marked_retryable(code: int) -> None:
    client = OpenAlgoClient(
        OpenAlgoConfig(host="http://openalgo", api_key="secret-key", ws_url="ws://openalgo"),
        transport=FakeTransport(
            [{"status": "error", "code": code, "message": "temporary failure"}]
        ),
    )

    with pytest.raises(OpenAlgoProtocolError) as excinfo:
        client.quote(symbol="RELIANCE", exchange="NSE")

    assert excinfo.value.retryable is True
    assert excinfo.value.status_code == code


def test_client_marks_non_retryable_provider_failures() -> None:
    client = OpenAlgoClient(
        OpenAlgoConfig(host="http://openalgo", api_key="secret-key", ws_url="ws://openalgo"),
        transport=FakeTransport(
            [{"status": "error", "code": 400, "message": "invalid request"}]
        ),
    )

    with pytest.raises(OpenAlgoProtocolError) as excinfo:
        client.quote(symbol="RELIANCE", exchange="NSE")

    assert excinfo.value.retryable is False
    assert excinfo.value.status_code == 400


def test_client_reserves_quota_before_transport() -> None:
    transport = FakeTransport([{"status": "success", "data": {"ltp": 1}}])
    tracker = QuotaTracker(QuotaLimits(1, 10, 10, 50, 100))
    now = datetime(2026, 1, 2, 10, 0, tzinfo=UTC)
    client = OpenAlgoClient(
        OpenAlgoConfig(host="http://openalgo", api_key="secret-key", ws_url="ws://openalgo"),
        transport=transport,
        quota=tracker,
        clock=lambda: now,
    )

    client.quote(symbol="RELIANCE", exchange="NSE")
    with pytest.raises(OpenAlgoProtocolError, match="quota"):
        client.quote(symbol="TCS", exchange="NSE")

    assert len(transport.calls) == 1


def test_websocket_parser_accepts_auth_subscription_and_quote_messages() -> None:
    assert parse_websocket_message(json.dumps({"type": "auth", "status": "success"})) == (
        "auth",
        {"type": "auth", "status": "success"},
    )
    assert parse_websocket_message(
        json.dumps({"type": "quote", "exchange": "NSE", "symbol": "RELIANCE", "ltp": 1422.5})
    ) == ("quote", {"type": "quote", "exchange": "NSE", "symbol": "RELIANCE", "ltp": 1422.5})


def test_websocket_parser_accepts_live_market_data_and_error_frames() -> None:
    market = {"type": "market_data", "mode": 3, "exchange": "NSE", "symbol": "RELIANCE", "data": {}}
    assert parse_websocket_message(json.dumps(market)) == ("market_data", market)

    error = {"status": "error", "code": "INVALID_PARAMETERS", "message": "bad symbol"}
    assert parse_websocket_message(json.dumps(error)) == ("error", error)


def test_websocket_parser_rejects_malformed_or_unknown_messages() -> None:
    with pytest.raises(OpenAlgoProtocolError, match="valid JSON object"):
        parse_websocket_message("not-json")
    with pytest.raises(OpenAlgoProtocolError, match="unsupported message type"):
        parse_websocket_message(json.dumps({"type": "order_update"}))
