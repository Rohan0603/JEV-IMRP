"""Capture direct FYERS v3 SymbolUpdate and DepthUpdate packets read-only.

The capture persists raw packets through the canonical :class:`FyersRecorder`
and closes automatically after the requested duration. It has no trading
client, account, position, order, modify, or cancel method.
"""

from __future__ import annotations

import argparse
import json
import os
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from jev_imrp.common.timeutils import is_within_session, to_ist, utc_now
from jev_imrp.ingest.data_health import DataHealthThresholds, evaluate_events
from jev_imrp.ingest.file_writer import RawEventWriter, iter_raw_events, verify_manifest
from jev_imrp.ingest.fyers_market_data import FyersMarketDataBridge
from jev_imrp.ingest.fyers_socket import FyersRecorder
from jev_imrp.ingest.fyers_symbol_master import to_fyers_symbol
from jev_imrp.ingest.qualification import evaluate_qualification
from jev_imrp.ingest.raw_event import RawEvent

SDK_VERSION = "3.3.0"


def load_env(path: Path = Path(".env")) -> None:
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
    symbols = [item.get("symbol") for item in config.get("instruments", [])]
    if not all(isinstance(symbol, str) and symbol for symbol in symbols):
        raise RuntimeError("universe.yaml contains an invalid research symbol")
    return [to_fyers_symbol(symbol) for symbol in symbols]


def capture(*, duration_seconds: float, output: Path, reconnect_retries: int) -> dict[str, Any]:
    app_id = os.environ.get("FYERS_APP_ID", "").strip()
    token = os.environ.get("FYERS_ACCESS_TOKEN", "").strip()
    if not app_id or not token:
        raise RuntimeError("FYERS_APP_ID and FYERS_ACCESS_TOKEN are required")
    if duration_seconds <= 0:
        raise ValueError("duration_seconds must be positive")
    if reconnect_retries < 1:
        raise ValueError("reconnect_retries must be positive")
    now = utc_now()
    if to_ist(now).weekday() >= 5 or not is_within_session(now):
        raise RuntimeError(
            f"FYERS socket qualification requires NSE cash-market hours; current UTC is {now}"
        )

    session_id = uuid.uuid4().hex
    symbols = frozen_symbols()
    output.mkdir(parents=True, exist_ok=True)
    writer = RawEventWriter(
        output,
        session_id=session_id,
        provider="fyers",
        provider_version=f"fyers-apiv3-{SDK_VERSION}",
        raw_timestamp_semantics="last_traded_time_epoch_seconds_when_present",
    )
    bridge = FyersMarketDataBridge(
        writer,
        recorder=FyersRecorder(writer, session_id=session_id, data_origin="live"),
    )
    finished = threading.Event()
    errors: list[str] = []
    connection = None
    connected_once = False

    def on_connect() -> None:
        nonlocal connected_once
        assert connection is not None
        if connected_once:
            bridge.recorder.begin_connection()
        else:
            connected_once = True
        connection.subscribe(symbols=symbols, data_type="SymbolUpdate")
        connection.subscribe(symbols=symbols, data_type="DepthUpdate")

    def on_message(message: Any) -> None:
        try:
            bridge.feed(message)
        except (TypeError, ValueError) as exc:
            errors.append(str(exc))

    def on_error(message: Any) -> None:
        errors.append(f"socket_error:{type(message).__name__}")

    def on_close(_message: Any) -> None:
        finished.set()

    from fyers_apiv3.FyersWebsocket import data_ws

    connection = data_ws.FyersDataSocket(
        access_token=f"{app_id}:{token}",
        log_path=str(output),
        litemode=False,
        write_to_file=False,
        reconnect=True,
        reconnect_retry=reconnect_retries,
        on_connect=on_connect,
        on_close=on_close,
        on_error=on_error,
        on_message=on_message,
    )
    timer = threading.Timer(duration_seconds, connection.close_connection)
    started = datetime.now(UTC)
    try:
        timer.start()
        connection.connect()
        finished.wait(duration_seconds + 10)
    finally:
        timer.cancel()
        connection.close_connection()
        writer.finalize()
    capture_end = datetime.now(UTC)
    events = [RawEvent.from_row(row) for row in iter_raw_events(output)]
    qualification = evaluate_qualification(events, capture_end_utc=capture_end)
    health = evaluate_events(
        events,
        thresholds=DataHealthThresholds(required_symbols=frozenset(symbols)),
    )
    manifest_problems = verify_manifest(output)
    report = {
        "provider": "fyers-direct",
        "provider_version": f"fyers-apiv3-{SDK_VERSION}",
        "session_id": session_id,
        "started_at_utc": started.isoformat(),
        "ended_at_utc": datetime.now(UTC).isoformat(),
        "duration_seconds": duration_seconds,
        "symbols": symbols,
        "counters": {
            "received": bridge.recorder.counters.received,
            "written": bridge.recorder.counters.written,
            "by_message_type": dict(bridge.recorder.counters.by_message_type),
            "validated_quote_rows": bridge.quote_rows,
        },
        "socket_errors": errors,
        "qualification": qualification.to_dict(),
        "data_health": health.to_dict(),
        "manifest_problems": manifest_problems,
        "ok": qualification.ok and health.ok and not manifest_problems,
        "live_orders": {
            "authorized": False,
            "placed": False,
            "modified": False,
            "cancelled": False,
        },
    }
    (output / "qualification_capture.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def main() -> int:
    load_env()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=300.0)
    parser.add_argument("--reconnect-retries", type=int, default=5)
    parser.add_argument("--output", default="data/qualification/fyers_direct")
    args = parser.parse_args()
    try:
        report = capture(
            duration_seconds=args.duration,
            output=Path(args.output) / datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"),
            reconnect_retries=args.reconnect_retries,
        )
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc), "live_orders": False}))
        return 1
    print(json.dumps({"ok": True, **report}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
