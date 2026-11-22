"""Explicit user-mediated FYERS OAuth refresh for read-only research tools."""

from __future__ import annotations

import os
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from jev_imrp.ingest.fyers_oauth import (
    FyersAuthClient,
    FyersAuthError,
    FyersOAuthState,
    authorization_url,
    httpx_auth_transport,
    parse_callback_query,
    replace_env_setting,
)


def interactive_reauthenticate() -> str:
    """Complete one loopback OAuth flow and atomically persist the new token."""

    app_id = os.environ.get("FYERS_APP_ID", "").strip()
    app_secret = os.environ.get("FYERS_SECRET_KEY", "").strip()
    redirect_uri = os.environ.get("FYERS_REDIRECT_URI", "").strip()
    if not app_id or not app_secret or not redirect_uri:
        raise FyersAuthError(
            "FYERS_APP_ID, FYERS_SECRET_KEY and FYERS_REDIRECT_URI are required for re-auth"
        )
    parsed_redirect = urlsplit(redirect_uri)
    if parsed_redirect.scheme != "http" or parsed_redirect.hostname not in {
        "127.0.0.1",
        "localhost",
    }:
        raise FyersAuthError("FYERS_REDIRECT_URI must be an HTTP loopback callback")
    if parsed_redirect.port is None:
        raise FyersAuthError("FYERS_REDIRECT_URI must include a callback port")

    state_store = FyersOAuthState()
    state = state_store.issue()
    callback: dict[str, str] = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            request = urlsplit(self.path)
            if request.path != (parsed_redirect.path or "/"):
                self.send_error(404)
                return
            query = {
                key: values[-1]
                for key, values in parse_qs(request.query, keep_blank_values=True).items()
            }
            try:
                callback["code"] = parse_callback_query(query, state)
                state_store.consume(query.get("state", ""))
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"<p>FYERS authorization complete. You may close this tab.</p>")
            except (FyersAuthError, ValueError) as exc:
                callback["error"] = str(exc)
                self.send_error(400, "FYERS authorization failed")

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = HTTPServer((parsed_redirect.hostname, parsed_redirect.port), CallbackHandler)
    server.timeout = 300
    try:
        if not webbrowser.open(authorization_url(app_id, redirect_uri, state), new=1):
            raise FyersAuthError("could not open the FYERS login page")
        server.handle_request()
    finally:
        server.server_close()
    if "error" in callback:
        raise FyersAuthError(callback["error"])
    auth_code = callback.get("code")
    if not auth_code:
        raise FyersAuthError("FYERS login timed out before a callback was received")
    token = FyersAuthClient(
        app_id=app_id,
        app_secret=app_secret,
        transport=httpx_auth_transport,
    ).exchange_auth_code(auth_code)
    env_path = Path(os.environ.get("FYERS_ENV_FILE", ".env"))
    replace_env_setting(env_path, "FYERS_ACCESS_TOKEN", token)
    os.environ["FYERS_ACCESS_TOKEN"] = token
    return token


__all__ = ["interactive_reauthenticate"]
