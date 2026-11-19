from __future__ import annotations

import pytest

from jev_imrp.ingest.fyers_quotes import FyersQuoteClient, FyersQuoteError


def test_quotes_builds_authorized_read_only_request() -> None:
    calls: list[tuple[str, dict[str, str], dict[str, object]]] = []

    def transport(
        path: str, headers: dict[str, str], payload: dict[str, object]
    ) -> dict[str, object]:
        calls.append((path, headers, payload))
        return {
            "s": "ok",
            "d": [{"n": "NSE:RELIANCE-EQ", "v": {"lp": 2500.5}}],
        }

    client = FyersQuoteClient("APP-100", "token", transport)
    result = client.quotes(["NSE:RELIANCE-EQ"])

    assert result["NSE:RELIANCE-EQ"]["lp"] == 2500.5
    assert calls == [
        (
            "https://api-t1.fyers.in/data/quotes",
            {"Authorization": "APP-100:token"},
            {"symbols": "NSE:RELIANCE-EQ"},
        )
    ]


def test_quotes_rejects_provider_error_without_fallback() -> None:
    client = FyersQuoteClient("APP-100", "token", lambda *_: {"s": "error"})
    with pytest.raises(FyersQuoteError, match="request failed"):
        client.quotes(["NSE:RELIANCE-EQ"])


def test_quotes_rejects_more_than_fyers_limit() -> None:
    client = FyersQuoteClient("APP-100", "token", lambda *_: {"s": "ok", "d": []})
    with pytest.raises(FyersQuoteError, match="1 to 50"):
        client.quotes([f"NSE:S{i}-EQ" for i in range(51)])


def test_quotes_reauthenticates_once_after_expired_token() -> None:
    calls = 0

    def transport(
        _path: str, _headers: dict[str, str], _payload: dict[str, object]
    ) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise FyersQuoteError("expired", auth_expired=True)
        return {"s": "ok", "d": [{"n": "NSE:RELIANCE-EQ", "v": {"lp": 1.0}}]}

    refreshes = 0

    def reauthenticate() -> str:
        nonlocal refreshes
        refreshes += 1
        return "fresh-token"

    client = FyersQuoteClient("APP-100", "old-token", transport, reauthenticate=reauthenticate)

    assert client.quotes(["NSE:RELIANCE-EQ"])["NSE:RELIANCE-EQ"]["lp"] == 1.0
    assert calls == 2
    assert refreshes == 1
    assert client.access_token == "fresh-token"


def test_http_transport_marks_401_as_auth_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    from jev_imrp.ingest.fyers_quotes import httpx_quote_transport

    class Response:
        status_code = 401

    monkeypatch.setattr("httpx.get", lambda *_args, **_kwargs: Response())

    with pytest.raises(FyersQuoteError) as error:
        httpx_quote_transport("https://example.invalid", {}, {})
    assert error.value.auth_expired is True
