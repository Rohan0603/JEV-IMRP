from __future__ import annotations

from pathlib import Path

import pytest

from jev_imrp.ingest.fyers_oauth import (
    FyersAuthClient,
    FyersAuthError,
    replace_env_setting,
)


def test_auth_client_exchanges_code_without_echoing_secret() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def transport(endpoint: str, payload: dict[str, object]) -> dict[str, object]:
        calls.append((endpoint, payload))
        return {"s": "ok", "code": 200, "access_token": "fresh-token"}

    client = FyersAuthClient(
        app_id="APP-100",
        app_secret="private-secret",
        transport=transport,
    )

    assert client.exchange_auth_code("auth-code") == "fresh-token"
    assert calls[0][0] == "https://api-t1.fyers.in/api/v3/validate-authcode"
    assert calls[0][1]["code"] == "auth-code"
    assert "private-secret" not in str(calls[0][1])


def test_auth_client_rejects_unsuccessful_exchange_without_secret_in_error() -> None:
    client = FyersAuthClient(
        app_id="APP-100",
        app_secret="private-secret",
        transport=lambda *_: {"s": "error", "code": -5, "message": "invalid app id hash"},
    )

    with pytest.raises(FyersAuthError, match="invalid app id hash") as error:
        client.exchange_auth_code("auth-code")
    assert "private-secret" not in str(error.value)


def test_replace_env_setting_updates_only_requested_key(tmp_path: Path) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("FYERS_APP_ID=APP-100\nFYERS_ACCESS_TOKEN=old-token\nOTHER=value\n")

    replace_env_setting(env_path, "FYERS_ACCESS_TOKEN", "fresh-token")

    assert env_path.read_text() == (
        "FYERS_APP_ID=APP-100\nFYERS_ACCESS_TOKEN=fresh-token\nOTHER=value\n"
    )
