"""Credential-free helpers for the FYERS v3 authorization-code boundary."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode


class OAuthStateError(ValueError):
    """The OAuth callback state is missing, reused, or expired."""


class FyersOAuthState:
    """Single-use, expiry-aware CSRF state store with injectable clocks."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._token_factory = token_factory or (lambda: secrets.token_urlsafe(32))
        self._pending: dict[str, datetime] = {}

    def issue(self, *, ttl: timedelta = timedelta(minutes=10)) -> str:
        if ttl <= timedelta(0):
            raise ValueError("OAuth state TTL must be positive")
        token = self._token_factory()
        if not token:
            raise ValueError("OAuth state token must not be empty")
        now = _aware_utc(self._clock())
        self._purge(now)
        self._pending[token] = now + ttl
        return token

    def consume(self, returned_state: str) -> None:
        now = _aware_utc(self._clock())
        matched = next(
            (
                candidate
                for candidate in self._pending
                if hmac.compare_digest(candidate, returned_state)
            ),
            None,
        )
        if matched is None:
            raise OAuthStateError("OAuth state is unknown or already used")
        expires_at = self._pending.pop(matched)
        if expires_at <= now:
            raise OAuthStateError("OAuth state is expired")
        self._purge(now)

    def _purge(self, now: datetime) -> None:
        for token, expires_at in list(self._pending.items()):
            if expires_at <= now:
                del self._pending[token]


def authorization_url(
    app_id: str,
    redirect_uri: str,
    state: str,
    *,
    endpoint: str = "https://api.fyers.in/api/v3/generate-authcode",
) -> str:
    if not app_id or not redirect_uri or not state:
        raise ValueError("app_id, redirect_uri and state are required")
    params = {
        "client_id": app_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
    }
    return f"{endpoint}?{urlencode(params)}"


def app_id_hash(app_id: str, app_secret: str) -> str:
    if not app_id or not app_secret:
        raise ValueError("app_id and app_secret are required")
    return hashlib.sha256(f"{app_id}:{app_secret}".encode()).hexdigest()


def token_request(app_id: str, app_secret: str, auth_code: str) -> dict[str, str]:
    if not auth_code:
        raise ValueError("auth_code is required")
    return {
        "grant_type": "authorization_code",
        "appIdHash": app_id_hash(app_id, app_secret),
        "code": auth_code,
    }


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("OAuth clock must return a timezone-aware datetime")
    return value.astimezone(UTC)


__all__ = [
    "FyersOAuthState",
    "OAuthStateError",
    "app_id_hash",
    "authorization_url",
    "token_request",
]
