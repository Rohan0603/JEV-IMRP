"""Fetch a reproducible, read-only FYERS history slice for the frozen universe.

The command writes only research candles and a hashed manifest under ``data/``.
It never calls an account or order endpoint and fails closed when FYERS rejects
the access token; OAuth refresh remains an explicit user-mediated operation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

from jev_imrp.ingest.fyers_auth_flow import interactive_reauthenticate
from jev_imrp.ingest.fyers_history import FyersHistoryClient
from jev_imrp.ingest.fyers_symbol_master import to_fyers_symbol

HISTORY_ENDPOINT = "https://api-t1.fyers.in/data/history"


def load_env(path: Path = Path(".env")) -> None:
    """Load ignored local settings without logging their values."""

    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.lstrip().startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        if name.strip():
            os.environ[name.strip()] = value


def frozen_symbols(path: Path = Path("config/universe.yaml")) -> list[str]:
    config = yaml.safe_load(path.read_text(encoding="utf-8"))
    instruments = config.get("instruments")
    if not isinstance(instruments, list):
        raise RuntimeError("universe.yaml does not contain an instruments list")
    symbols = [item.get("symbol") for item in instruments if isinstance(item, dict)]
    if not all(isinstance(symbol, str) and symbol for symbol in symbols):
        raise RuntimeError("universe.yaml contains an invalid research symbol")
    return [to_fyers_symbol(symbol) for symbol in symbols]


def fetch_transport(path: str, payload: dict[str, object]) -> dict[str, object]:
    if path != "/data/history":
        raise RuntimeError(f"unexpected FYERS path: {path}")
    app_id = os.environ.get("FYERS_APP_ID", "").strip()
    token = os.environ.get("FYERS_ACCESS_TOKEN", "").strip()
    if not app_id or not token:
        raise RuntimeError("FYERS_APP_ID and FYERS_ACCESS_TOKEN are required")
    for attempt in range(2):
        response = httpx.get(
            HISTORY_ENDPOINT,
            headers={"Authorization": f"{app_id}:{os.environ['FYERS_ACCESS_TOKEN']}"},
            params=payload,
            timeout=30.0,
        )
        if response.status_code != 401:
            break
        if attempt == 0 and os.environ.get("FYERS_REAUTH_ON_EXPIRED") == "1":
            interactive_reauthenticate()
            continue
        raise RuntimeError("FYERS history rejected the access token; refresh it and retry")
    if response.status_code == 429:
        return {"s": "retry", "code": 429, "message": "FYERS rate limit"}
    response.raise_for_status()
    value = response.json()
    if not isinstance(value, dict):
        raise RuntimeError("FYERS history response must be a JSON object")
    return value


def run(
    *, start: date, end: date, resolution: str, output: Path, delay_seconds: float
) -> dict[str, Any]:
    client = FyersHistoryClient(transport=fetch_transport)
    rows: list[dict[str, object]] = []
    symbols = frozen_symbols()
    for index, symbol in enumerate(symbols):
        if index:
            time.sleep(delay_seconds)
        try:
            rows.extend(
                client.fetch_range(
                    symbol=symbol,
                    resolution=resolution,
                    range_from=start,
                    range_to=end,
                    as_of_utc=datetime.now(UTC),
                )
            )
        except Exception as exc:
            raise RuntimeError(f"{symbol} {resolution} {start}..{end}: {exc}") from exc
    output.mkdir(parents=True, exist_ok=True)
    candles_path = output / "candles.jsonl"
    with candles_path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
    digest = hashlib.sha256(candles_path.read_bytes()).hexdigest()
    manifest = {
        "provider": "fyers-direct",
        "endpoint": HISTORY_ENDPOINT,
        "resolution": resolution,
        "range_from": start.isoformat(),
        "range_to": end.isoformat(),
        "symbols_requested": symbols,
        "rows": len(rows),
        "candles_sha256": digest,
        "retrieved_at_utc": datetime.now(UTC).isoformat(),
        "live_orders": False,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return manifest


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from", dest="start", required=True, type=date.fromisoformat)
    parser.add_argument("--to", dest="end", required=True, type=date.fromisoformat)
    parser.add_argument("--resolution", default="1D")
    parser.add_argument("--output", default="data/research/fyers_history")
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    parser.add_argument(
        "--reauth",
        action="store_true",
        help="open the user-mediated loopback OAuth flow once if the token is expired",
    )
    args = parser.parse_args()
    if args.end < args.start:
        parser.error("--to must not precede --from")
    if args.delay_seconds < 0:
        parser.error("--delay-seconds must not be negative")
    if args.reauth:
        os.environ["FYERS_REAUTH_ON_EXPIRED"] = "1"
    try:
        manifest = run(
            start=args.start,
            end=args.end,
            resolution=args.resolution,
            output=Path(args.output),
            delay_seconds=args.delay_seconds,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc), "live_orders": False}))
        return 1
    print(json.dumps({"ok": True, **manifest}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
