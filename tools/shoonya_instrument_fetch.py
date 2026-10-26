"""Sprint 1 helper: resolve Shoonya broker instrument tokens for the frozen universe.

Shoonya now requires OAuth-based login (no TOTP auto-login). This script:
  1. Starts a local HTTP server on localhost:8080 to receive the OAuth redirect
  2. Opens the browser to the Shoonya authorize URL
  3. Captures the auth code from the redirect automatically
  4. Exchanges code + checksum for an access token via the SDK
  5. Searches every universe symbol via SearchScrip and resolves tokens
  6. Prints resolved rows for pasting into config/universe.yaml

Usage:
    python tools/shoonya_instrument_fetch.py

Research-only. Credentials come from the local ``.env`` file (git-ignored).
"""

from __future__ import annotations

import hashlib
import json
import sys
import threading
import urllib.parse
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import yaml
from NorenRestApiPy.NorenApi import NorenApi

REPO_ROOT = Path(__file__).resolve().parents[1]
UNIVERSE_PATH = REPO_ROOT / "config" / "universe.yaml"
OAUTH_AUTHORIZE = "https://api.shoonya.com/OAuthlogin/authorize/oauth"
REDIRECT_HOST = "localhost"
REDIRECT_PORT = 8080


def _load_env(key: str) -> str:
    env_path = REPO_ROOT / ".env"
    values: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            values[k.strip()] = v.strip()
    result = values.get(key) or __import__("os").environ.get(key)
    if not result:
        raise SystemExit(f"missing credential {key!r} in .env or environment")
    return result


class _OAuthCaptureHandler(BaseHTTPRequestHandler):
    """Captures the ?code= query param from the OAuth redirect and replies OK."""

    auth_code: str | None = None

    def do_GET(self):  # noqa: N802 - http.server convention
        parsed = urllib.parse.urlparse(self.path)
        params = urllib.parse.parse_qs(parsed.query)
        code = params.get("code", [None])[0]
        if code:
            _OAuthCaptureHandler.auth_code = code
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            b"<html><body><h1>JEV-IMRP</h1>"
            b"<p>OAuth code captured. You may close this window.</p></body></html>"
        )

    def log_message(self, _format, *args):  # silence request logging
        pass


def _capture_auth_code(client_id: str) -> str:
    server = HTTPServer((REDIRECT_HOST, REDIRECT_PORT), _OAuthCaptureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    auth_url = f"{OAUTH_AUTHORIZE}?client_id={urllib.parse.quote(client_id)}"
    print(f"1. Opening browser to Shoonya OAuth login page...")
    print(f"   {auth_url}")
    print("   (If the browser doesn't open automatically, paste the URL above manually)")
    webbrowser.open(auth_url)

    print("2. Waiting for OAuth redirect to localhost:8080 ...")
    while _OAuthCaptureHandler.auth_code is None:
        thread.join(timeout=0.5)

    server.shutdown()
    thread.join(timeout=1)
    code = _OAuthCaptureHandler.auth_code
    print(f"3. Auth code captured ({len(code)} chars).")
    return code


def main() -> int:
    client_id = _load_env("SHOONYA_USER_ID")
    secret_code = _load_env("SHOONYA_API_KEY")
    uid = _load_env("SHOONYA_USER_ID")

    auth_code = _capture_auth_code(client_id)

    print("Exchanging auth code for access token...")
    api = NorenApi(
        host="https://api.shoonya.com/NorenWClientAPI/",
        websocket="wss://api.shoonya.com/NorenWSAPI/",
    )
    result = api.getAccessToken(auth_code, secret_code, client_id, uid)
    if result is None:
        raise SystemExit("token exchange failed")
    print(f"   access token ok (acct={result[1]})")

    universe = yaml.safe_load(UNIVERSE_PATH.read_text(encoding="utf-8"))["instruments"]
    resolved: list[dict[str, str]] = []

    for entry in universe:
        symbol = str(entry["symbol"])
        exchange = str(entry.get("exchange", "NSE"))
        print(f"search {symbol} ({exchange})...", end=" ", flush=True)
        ret = api.searchscrip(exchange=exchange, searchtext=symbol)
        if not ret or ret.get("stat") != "Ok":
            print("no match")
            continue
        results = ret.get("values", [])
        if not isinstance(results, list) or not results:
            print("no match")
            continue
        # Exact match required: tsym must be SYMBOL-EQ, not a partial hit
        # (searchscrip is fuzzy; "SBIN" matches "SBINMID150-EQ" otherwise)
        expected_tsym = f"{symbol.upper()}-EQ"
        match = next(
            (r for r in results if str(r.get("tsym", "")).upper() == expected_tsym),
            None,
        )
        if match is None:
            match = next(
                (r for r in results if str(r.get("tsym", "")).upper() == symbol.upper()),
                None,
            )
        tok = str(match.get("token", ""))
        print(f"-> token={tok} tsym={match.get('tsym', '?')}")
        resolved.append({
            "symbol": symbol,
            "exchange": str(match.get("exch", exchange)),
            "instrument_token": tok,
            "tick_size_inr": entry.get("tick_size_inr", 0.05),
        })

    print("\n--- resolved rows (paste into config/universe.yaml) ---")
    for row in resolved:
        print(
            f"  - symbol: {row['symbol']}\n"
            f"    exchange: {row['exchange']}\n"
            f"    instrument_token: {row['instrument_token']}\n"
            f"    tick_size_inr: {row['tick_size_inr']}"
        )
    if len(resolved) < len(universe):
        print(f"\n!! only {len(resolved)}/{len(universe)} resolved — check above for failures")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())