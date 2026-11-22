"""Direct FYERS -> JEV read-only smoke test.

This script fetches latest FYERS quotes, selects one valid L1 observation, and
sends only the declared JEV feature state to the existing live JEV runner.  It
never imports an execution gateway and has no order, position, or account call.
Required environment variables are ``FYERS_APP_ID``, ``FYERS_ACCESS_TOKEN``
and ``TYPESAFE_API_KEY``.  Credentials are read from the process environment
only and are never printed or written to artifacts.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from jev_imrp.common.timeutils import is_within_session, minutes_since_open, to_ist, utc_now
from jev_imrp.experiments.attempt_store import AttemptStore
from jev_imrp.experiments.runner import JevRunner
from jev_imrp.ingest.fyers_oauth import (
    FyersAuthClient,
    FyersAuthError,
    FyersOAuthState,
    authorization_url,
    httpx_auth_transport,
    parse_callback_query,
    replace_env_setting,
)
from jev_imrp.ingest.fyers_quotes import FyersQuoteClient, httpx_quote_transport
from jev_imrp.ingest.fyers_symbol_master import to_fyers_symbol
from jev_imrp.models.jev_client import (
    CONFIRMATORY_MODEL_ID,
    Candidate,
    LiveConfig,
    LiveJevClient,
    accept_reject_question,
)


def _number(values: dict[str, object], *names: str) -> float | None:
    for name in names:
        value = values.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return float(value)
    return None


def _features(quote: dict[str, object], now: datetime) -> dict[str, object]:
    last = _number(quote, "lp", "ltp")
    bid = _number(quote, "bid", "bid_price")
    ask = _number(quote, "ask", "ask_price")
    bid_size = _number(quote, "bid_size", "bq1")
    ask_size = _number(quote, "ask_size", "sq1")
    result: dict[str, object] = {
        "tod_minutes_since_open": minutes_since_open(now),
        "tod_bucket": "live_quote",
    }
    if bid is not None and ask is not None and last is not None and last > 0 and ask >= bid > 0:
        result["spread_bps"] = 10_000.0 * (ask - bid) / ((ask + bid) / 2.0)
    if bid_size is not None and ask_size is not None and bid_size + ask_size > 0:
        result["touch_size_imbalance"] = (bid_size - ask_size) / (bid_size + ask_size)
    return result


def _load_env_file(path: Path = Path(".env")) -> None:
    """Load local ignored settings without printing or logging their values."""

    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip():
            os.environ[name.strip()] = value


def _interactive_reauthenticate() -> str:
    """Run one user-mediated FYERS login and persist the returned token."""

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
        login_url = authorization_url(app_id, redirect_uri, state)
        if not webbrowser.open(login_url, new=1):
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


async def run(symbol: str) -> dict[str, Any]:
    now = utc_now()
    if to_ist(now).weekday() >= 5 or not is_within_session(now):
        raise RuntimeError(
            f"direct FYERS smoke requires NSE cash-market hours; current UTC is {now}"
        )

    fyers = FyersQuoteClient.from_env(
        transport=httpx_quote_transport,
        reauthenticate=_interactive_reauthenticate,
    )
    fyers_symbol = to_fyers_symbol(symbol)
    quotes = fyers.quotes([fyers_symbol])
    quote = quotes.get(fyers_symbol)
    if quote is None:
        raise RuntimeError(f"FYERS returned no quote for {fyers_symbol}")
    features = _features(dict(quote), now)
    if "spread_bps" not in features:
        raise RuntimeError("FYERS quote did not contain a valid positive bid/ask touchline")

    candidate_id = f"fyers-live-{now.strftime('%Y%m%dT%H%M%S%fZ')}"
    candidate = Candidate(
        candidate_id=candidate_id,
        symbol=fyers_symbol,
        side="BUY",
        decision_timestamp_utc=now.isoformat(),
        horizon_minutes=30,
        features=features,
        feature_set_version="live_fyers_l1_v1",
    )
    artifact_root = Path("data/live/fyers_jev") / now.strftime("%Y%m%dT%H%M%SZ")
    store = AttemptStore(artifact_root / "attempts.jsonl")
    client = LiveJevClient(LiveConfig(model_id=CONFIRMATORY_MODEL_ID))
    try:
        runner = JevRunner(
            client,
            store,
            experiment_id="fyers_live_read_only",
            questions=(accept_reject_question(),),
            model_id=CONFIRMATORY_MODEL_ID,
            failure_policy="reject",
            max_concurrency=1,
            raw_response_dir=str(artifact_root / "responses"),
        )
        result = await runner.run([candidate])
    finally:
        await client.aclose()

    artifact_root.mkdir(parents=True, exist_ok=True)
    output = {
        "provider": "fyers-direct",
        "symbol": fyers_symbol,
        "quote": dict(quote),
        "candidate": candidate_id,
        "jev": result.to_record(),
        "live_orders": {
            "authorized": False,
            "placed": False,
            "modified": False,
            "cancelled": False,
        },
        "credentials_written": False,
    }
    (artifact_root / "result.json").write_text(
        json.dumps(output, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    return output


def main() -> int:
    _load_env_file()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--symbol", default="RELIANCE", help="frozen-universe symbol, e.g. RELIANCE"
    )
    args = parser.parse_args()
    required = ("FYERS_APP_ID", "FYERS_ACCESS_TOKEN", "TYPESAFE_API_KEY")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        print(
            json.dumps(
                {
                    "ok": False,
                    "error": "missing required environment setting(s)",
                    "settings": missing,
                }
            )
        )
        return 2
    try:
        result = asyncio.run(run(args.symbol))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc), "live_orders": False}, sort_keys=True))
        return 1
    print(
        json.dumps(
            {
                "ok": True,
                "provider": result["provider"],
                "symbol": result["symbol"],
                "live_orders": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
