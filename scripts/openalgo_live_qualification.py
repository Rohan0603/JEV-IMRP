"""Capture and qualify OpenAlgo -> FYERS frames without placing orders."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import secrets
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import websockets

from jev_imrp.common.config import load_universe, load_yaml
from jev_imrp.common.timeutils import is_within_session, to_ist
from jev_imrp.ingest.feed_state import replay_raw_events
from jev_imrp.ingest.file_writer import (
    RawEventWriter,
    iter_raw_events,
    read_manifest,
    verify_manifest,
)
from jev_imrp.ingest.openalgo_client import OpenAlgoConfig, parse_websocket_message
from jev_imrp.ingest.openalgo_recorder import OpenAlgoRecorder
from jev_imrp.ingest.qualification import QualificationThresholds, evaluate_qualification
from jev_imrp.ingest.raw_event import RawEvent


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _gateway_metadata() -> dict[str, str | None]:
    configured = os.getenv("OPENALGO_GATEWAY_PATH")
    default = Path(os.getenv("LOCALAPPDATA", "")) / "OpenAlgo Desktop" / "openalgo-desktop.exe"
    path = Path(configured) if configured else default
    return {
        "path": str(path) if path.exists() else None,
        "version": os.getenv("OPENALGO_GATEWAY_VERSION", "not_declared"),
        "sha256": _sha256(path) if path.exists() else None,
    }


def _state_signature(book: Any) -> dict[str, tuple[Any, ...]]:
    return {
        f"{exchange}|{token}": (
            state.subscription_generation,
            state.event_id,
            state.last_price,
            state.bid_price_1,
            state.ask_price_1,
            state.bid_qty_1,
            state.ask_qty_1,
            state.invalid_reasons,
        )
        for (exchange, token), state in sorted(book.states.items())
    }


async def _receive_connection(
    ws: Any,
    recorder: OpenAlgoRecorder,
    api_key: str,
    symbols: list[str],
    duration_s: float,
) -> tuple[datetime, datetime]:
    started = datetime.now(UTC)
    await ws.send(json.dumps(recorder.authenticate_frame(api_key)))
    auth_ok = False
    deadline = asyncio.get_running_loop().time() + 10
    while asyncio.get_running_loop().time() < deadline and not auth_ok:
        raw = await asyncio.wait_for(ws.recv(), timeout=10)
        kind, payload = parse_websocket_message(raw)
        recorder.feed(payload)
        auth_ok = kind == "auth" and str(payload.get("status", "")).lower() in {
            "success",
            "authenticated",
        }
        if kind == "error":
            raise RuntimeError("OpenAlgo authentication failed")
    if not auth_ok:
        raise RuntimeError("OpenAlgo authentication timed out")
    await ws.send(json.dumps(recorder.subscribe_batch_frame("NSE", symbols, mode=2)))
    await ws.send(json.dumps(recorder.subscribe_batch_frame("NSE", symbols, mode=3)))
    end = asyncio.get_running_loop().time() + duration_s
    while asyncio.get_running_loop().time() < end:
        timeout = max(0.05, end - asyncio.get_running_loop().time())
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        except TimeoutError:
            break
        kind, payload = parse_websocket_message(raw)
        recorder.feed(payload)
        if kind == "error":
            raise RuntimeError("OpenAlgo returned a gateway error")
    return started, datetime.now(UTC)


async def capture(args: argparse.Namespace) -> Path:
    config = OpenAlgoConfig.from_env()
    if not os.getenv("OPENALGO_API_KEY"):
        raise RuntimeError("OPENALGO_API_KEY is not available to this process")
    now_utc = datetime.now(UTC)
    now_ist = to_ist(now_utc)
    if now_ist.weekday() >= 5 or not is_within_session(now_utc):
        raise RuntimeError(
            "qualification capture requires NSE cash-market hours (09:15-15:30 IST); "
            f"current IST is {now_ist.isoformat()}"
        )
    universe = load_universe()
    symbols = [str(row["symbol"]) for row in universe["instruments"]]
    if args.symbol:
        symbols = args.symbol
    qualification_config = load_yaml("config/openalgo_fyers_qualification.yaml")
    thresholds = QualificationThresholds(**qualification_config["thresholds"])
    gateway_version = os.getenv("OPENALGO_GATEWAY_VERSION", "not_declared")
    gateway_metadata = _gateway_metadata()
    run_id = f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(4)}"
    root = Path(args.output) / run_id
    writer = RawEventWriter(
        root,
        session_id=run_id,
        provider="openalgo:fyers",
        provider_version=gateway_version,
        raw_timestamp_semantics=(
            "OpenAlgo market_data timestamp milliseconds; receipt UTC and monotonic ns local"
        ),
        master_hash=_sha256(Path("config/universe.yaml")),
    )
    shared_book = None
    reconnect_gaps: list[float] = []
    capture_start: datetime | None = None
    capture_end: datetime | None = None
    previous_end: datetime | None = None
    for attempt in range(args.reconnects + 1):
        recorder = OpenAlgoRecorder(
            writer,
            book=shared_book,
            session_id=run_id,
            connection_id=f"connection-{attempt + 1}",
        )
        shared_book = recorder.book
        async with websockets.connect(config.ws_url, open_timeout=10, close_timeout=5) as ws:
            started, ended = await _receive_connection(
                ws, recorder, config.api_key, symbols, args.duration
            )
        writer.flush_batch()
        capture_start = started if capture_start is None else min(capture_start, started)
        capture_end = ended if capture_end is None else max(capture_end, ended)
        if attempt and previous_end is not None:
            reconnect_gaps.append(max(0.0, (started - previous_end).total_seconds() * 1000))
        previous_end = ended
    writer.finalize()
    entries = read_manifest(root)
    manifest_problems = verify_manifest(root)
    rows = list(iter_raw_events(root, entries))
    replay_book, _replay_states = replay_raw_events(rows)
    online_signature = _state_signature(shared_book)
    replay_signature = _state_signature(replay_book)
    events = [RawEvent.from_row(row) for row in rows]
    report = evaluate_qualification(
        events,
        thresholds=thresholds,
        capture_end_utc=capture_end,
        reconnect_gaps_ms=reconnect_gaps,
    )
    manifest_path = root / "manifest.jsonl"
    result: dict[str, Any] = {
        "run_id": run_id,
        "provider": config.upstream_provider,
        "gateway": "openalgo",
        "gateway_version": gateway_version,
        "gateway_protocol": "openalgo-data-api-v1",
        "gateway_executable": gateway_metadata,
        "upstream_provider_version": "not_exposed_by_openalgo_gateway",
        "symbols": symbols,
        "capture_start_utc": None if capture_start is None else capture_start.isoformat(),
        "capture_end_utc": None if capture_end is None else capture_end.isoformat(),
        "manifest": [
            {
                "path": entry.path,
                "sha256": entry.sha256,
                "row_count": entry.row_count,
                "provider": entry.provider,
                "provider_version": entry.provider_version,
            }
            for entry in entries
        ],
        "manifest_sha256": _sha256(manifest_path) if manifest_path.exists() else None,
        "manifest_verification": {"ok": not manifest_problems, "problems": manifest_problems},
        "qualification": report.to_dict(),
        "replay_parity": {
            "ok": not manifest_problems and online_signature == replay_signature,
            "online_state_count": len(online_signature),
            "replay_state_count": len(replay_signature),
            "state_signature_match": online_signature == replay_signature,
            "source": "manifest-listed-parquet-only",
        },
        "approval_gates": qualification_config["approval_gates"],
        "live_orders": {"analyze_mode": True, "authorized": False, "placed": False},
        "secrets_written": False,
    }
    report_path = root / "qualification_report.json"
    report_path.write_text(
        json.dumps(result, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8"
    )
    report_sha256 = _sha256(report_path)
    (root / "qualification_report.sha256").write_text(
        f"{report_sha256}  qualification_report.json\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "root": str(root),
                "report": str(report_path),
                "manifest_sha256": result["manifest_sha256"],
                "report_sha256": report_sha256,
                "manifest_ok": not manifest_problems,
                "replay_parity": result["replay_parity"]["ok"],
                "live_orders": False,
            },
            sort_keys=True,
        )
    )
    return report_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=float, default=10.0)
    parser.add_argument("--reconnects", type=int, default=1)
    parser.add_argument("--output", default="data/qualification/openalgo_fyers")
    parser.add_argument("--symbol", action="append")
    args = parser.parse_args()
    try:
        asyncio.run(capture(args))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc), "live_orders": False}, sort_keys=True))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
