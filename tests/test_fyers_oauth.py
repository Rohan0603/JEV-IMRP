from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from jev_imrp.ingest.fyers_oauth import (
    FyersOAuthState,
    OAuthStateError,
    app_id_hash,
    authorization_url,
    token_request,
)


def test_oauth_state_is_single_use_and_expiry_aware() -> None:
    now = datetime(2026, 1, 2, 10, 0, tzinfo=UTC)
    state = FyersOAuthState(clock=lambda: now, token_factory=lambda: "state-1")

    issued = state.issue(ttl=timedelta(minutes=5))
    assert issued == "state-1"
    state.consume("state-1")
    with pytest.raises(OAuthStateError, match="unknown or already used"):
        state.consume("state-1")

    state.issue(ttl=timedelta(seconds=1))
    state._clock = lambda: now + timedelta(seconds=2)  # type: ignore[method-assign]
    with pytest.raises(OAuthStateError, match="expired"):
        state.consume("state-1")


def test_authorization_url_preserves_exact_redirect_and_state() -> None:
    url = authorization_url(
        "client-1", "http://127.0.0.1:5000/fyers/callback", "state value"
    )

    assert "client_id=client-1" in url
    assert "redirect_uri=http%3A%2F%2F127.0.0.1%3A5000%2Ffyers%2Fcallback" in url
    assert "response_type=code" in url
    assert "state=state+value" in url


def test_token_request_hashes_app_credentials_without_echoing_secret() -> None:
    secret = "private-secret"
    payload = token_request("client-1", secret, "auth-code")

    assert payload["grant_type"] == "authorization_code"
    assert payload["code"] == "auth-code"
    assert payload["appIdHash"] == app_id_hash("client-1", secret)
    assert secret not in str(payload)
