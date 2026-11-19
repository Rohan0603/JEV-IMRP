"""Credential-free helpers for the FYERS v3 authorization-code boundary."""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any
from urllib.parse import urlencode


class OAuthStateError(ValueError):
    """The OAuth callback state is missing, reused, or expired."""


class FyersAuthError(ValueError):
    """A direct FYERS authorization-code exchange failed."""


MappingLike = dict[str, Any]
AuthTransport = Callable[[str, dict[str, str]], MappingLike]


class FyersAuthClient:
    """Exchange a one-time FYERS auth code without exposing app secrets."""

    def __init__(
        self,
        *,
        app_id: str,
        app_secret: str,
        transport: AuthTransport,
        endpoint: str = "https://api-t1.fyers.in/api/v3/validate-authcode",
    ) -> None:
        if not app_id or not app_secret:
            raise FyersAuthError("FYERS app credentials are required")
        self._app_id = app_id
        self._app_secret = app_secret
        self._transport = transport
        self._endpoint = endpoint

    def exchange_auth_code(self, auth_code: str) -> str:
        if not auth_code:
            raise FyersAuthError("FYERS auth code is required")
        response = self._transport(
            self._endpoint,
            {
                "grant_type": "authorization_code",
                "appIdHash": app_id_hash(self._app_id, self._app_secret),
                "code": auth_code,
            },
        )
        if response.get("s") != "ok":
            message = response.get("message", response.get("code", "unknown error"))
            raise FyersAuthError(f"FYERS token exchange failed: {message}")
        token = response.get("access_token")
        if not isinstance(token, str) or not token:
            raise FyersAuthError("FYERS token exchange returned no access token")
        return token


def httpx_auth_transport(endpoint: str, payload: dict[str, str]) -> MappingLike:
    """POST one auth-code exchange without including credentials in errors."""

    import httpx

    response = httpx.post(endpoint, json=payload, timeout=20.0)
    try:
        body = response.json()
    except ValueError as exc:
        raise FyersAuthError("FYERS token exchange returned invalid JSON") from exc
    if not isinstance(body, dict):
        raise FyersAuthError("FYERS token exchange returned a non-object response")
    return body


def replace_env_setting(path: str | Path, key: str, value: str) -> None:
    """Atomically replace one local env setting without logging its value."""

    if not key or "=" in key or "\n" in key or "\r" in key:
        raise ValueError("env key must be a single setting name")
    if "\n" in value or "\r" in value:
        raise ValueError("env value must be a single line")
    target = Path(path)
    original = target.read_text(encoding="utf-8") if target.exists() else ""
    newline = "\r\n" if "\r\n" in original else "\n"
    lines = original.splitlines()
    replaced = False
    output: list[str] = []
    for line in lines:
        if line.startswith(f"{key}="):
            if not replaced:
                output.append(f"{key}={value}")
                replaced = True
            continue
        output.append(line)
    if not replaced:
        output.append(f"{key}={value}")
    encoded = newline.join(output) + newline
    target.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="", dir=target.parent, delete=False
    ) as handle:
        temporary = Path(handle.name)
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, target)


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
    endpoint: str = "https://api-t1.fyers.in/api/v3/generate-authcode",
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


def parse_callback_query(query: MappingLike, expected_state: str) -> str:
    """Validate a FYERS callback query and return its one-time auth code."""

    if query.get("state") != expected_state:
        raise OAuthStateError("FYERS callback state does not match the pending login")
    status = query.get("s")
    code = query.get("auth_code") or query.get("code")
    if status == "error" or not isinstance(code, str) or not code:
        raise FyersAuthError("FYERS callback did not contain an authorization code")
    return code


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("OAuth clock must return a timezone-aware datetime")
    return value.astimezone(UTC)


__all__ = [
    "FyersAuthClient",
    "FyersAuthError",
    "FyersOAuthState",
    "OAuthStateError",
    "app_id_hash",
    "authorization_url",
    "httpx_auth_transport",
    "parse_callback_query",
    "replace_env_setting",
    "token_request",
]
