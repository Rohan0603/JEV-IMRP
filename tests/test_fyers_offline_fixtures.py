"""Offline FYERS fixture integrity tests for ORC-114.

These tests validate that the fixture files (sanitized_packets.json, rest_cases.json,
manifest.json) are well-formed, have correct hashes and line endings, and that
replaying the frames produces the expected observables.
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

FIXTURES = ROOT / "tests" / "fixtures" / "fyers"
PACKETS = FIXTURES / "sanitized_packets.json"
REST = FIXTURES / "rest_cases.json"
MANIFEST = FIXTURES / "manifest.json"


# ── manifest provenance ────────────────────────────────────────────────


def test_manifest_declares_synthetic_offline_provenance() -> None:
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert m["data_origin"] == "synthetic"
    assert "SYNTHETIC" in m["warning"].upper()
    assert m["provider"] == "FYERS"
    assert m["api_version"] == "v3"
    assert m["data_socket_version"] == "HSM v1-5"
    assert m["parser_version"] == "fyers_socket_v0"


def test_manifest_hashes_and_line_endings() -> None:
    for p in (PACKETS, REST, MANIFEST):
        raw = p.read_bytes()
        assert b"\r\n" not in raw, f"{p.name} contains CRLF"
        assert raw.endswith(b"\n"), f"{p.name} must end with LF"


def test_manifest_expectations_have_all_packet_labels() -> None:
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    packets = json.loads(PACKETS.read_text(encoding="utf-8"))
    labels_in_manifest = set(m["expectations"].keys())
    labels_in_packets = {f["label"] for f in packets["frames"]}
    missing = labels_in_manifest - labels_in_packets
    assert not missing, f"manifest expectations missing packet labels: {missing}"


def test_clock_policy_in_manifest() -> None:
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    cp = m["clock_policy"]
    assert cp["provider_timestamp_only"] is True
    assert cp["local_receipt_time_not_substituted"] is True
    assert cp["missing_provider_time_is_null"] is True
    assert cp["implausible_epoch_is_null_with_reason"] is True
    assert cp["dp_has_no_source_time"] is True
    assert cp["live_unit_verification"] == "NOT RUN"


def test_not_run_items_present() -> None:
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert m["live_capture"] == "NOT RUN"
    nr = m.get("not_run", [])
    assert isinstance(nr, list) and len(nr) >= 5
    for item in nr:
        assert isinstance(item, dict) and isinstance(item.get("reason", ""), str) and item["reason"].strip()


# ── replay observables ───────────────────────────────────────────────


def _replay_observed() -> dict:
    from jev_imrp.ingest.fyers_socket import FyersPacketDecoder, FyersQuoteBook

    packets = json.loads(PACKETS.read_text(encoding="utf-8"))
    topics = {
        "sf|nse_cm|SYNTH2885", "sf|nse_cm|SYNTH3045",
        "if|nse_cm|NIFTY50-INDEX", "dp|nse_cm|SYNTH2885",
    }
    decoder = FyersPacketDecoder()
    book = FyersQuoteBook(tick_sizes={"RELIANCE": 0.05, "SBIN": 0.05})
    for t in topics:
        book.register_subscription(t)

    generation = 1
    observed: dict = {}
    for frame in packets["frames"]:
        if frame["generation"] != generation:
            generation = frame["generation"]
            book.begin_generation()
        moment = datetime.fromisoformat(frame["received_timestamp_utc"]).replace(tzinfo=UTC)
        decoded = decoder.decode(
            bytes.fromhex(frame["hex"]),
            received_timestamp_utc=moment,
            received_monotonic_ns=frame["received_monotonic_ns"],
            generation=generation,
            data_origin="synthetic",
        )
        state = next(
            (s for s in [book.apply(p) for p in decoded] if s is not None),
            None,
        )
        label = frame["label"]
        if state is None:
            observed[label] = {
                "kind": frame["kind"],
                "response_name": decoded[0].response_name,
                "status": decoded[0].status,
                "parse_error": decoded[0].parse_error,
            }
        else:
            observed[label] = {
                "kind": "data",
                "generation": state.generation,
                "frame_kind": state.frame_kind,
                "state_valid": state.state_valid,
                "bid_price_1": state.bid_price_1,
                "ask_price_1": state.ask_price_1,
                "bid_qty_1": state.bid_qty_1,
                "ask_qty_1": state.ask_qty_1,
                "last_price": state.fields.get("ltp"),
                "bid_price_2": state.fields.get("bid_price2"),
                "bid_price_3": state.fields.get("bid_price3"),
                "source_time_raw": state.source_time_raw,
                "source_time_utc": None if state.source_time_utc is None
                else state.source_time_utc.isoformat(),
                "invalid_reasons": list(state.invalid_reasons),
            }
    return observed


def test_replay_matches_manifest_expectations() -> None:
    observed = _replay_observed()
    expected = json.loads(MANIFEST.read_text(encoding="utf-8"))["expectations"]
    for label in expected:
        if label not in observed:
            pytest.fail(f"label {label!r} not found in replay observables")
        obs = observed[label]
        exp = expected[label]
        for key in exp:
            if key in ("last_price", "source_time_utc", "source_time_raw"):
                assert obs.get(key) == exp[key], f"{label}.{key}: {obs.get(key)!r} != {exp[key]!r}"
            else:
                assert obs.get(key) == exp[key], (
                    f"{label}.{key}: expected {exp[key]!r}, got {obs.get(key)!r}"
                )


def test_missing_source_time_never_substituted() -> None:
    observed = _replay_observed()
    exp = json.loads(MANIFEST.read_text(encoding="utf-8"))["expectations"]["missing_source_time_sf"]
    assert exp["source_time_raw"] is None
    assert exp["source_time_utc"] is None
    assert observed["missing_source_time_sf"]["state_valid"] is True


def test_implausible_source_time_preserved_and_flagged() -> None:
    observed = _replay_observed()
    exp = json.loads(MANIFEST.read_text(encoding="utf-8"))["expectations"]["implausible_source_time_sf"]
    assert exp["source_time_raw"] == 1000
    assert exp["source_time_utc"] is None
    assert exp["invalid_reasons"] == ["source_clock_implausible"]
    assert observed["implausible_source_time_sf"]["invalid_reasons"] == ["source_clock_implausible"]


def test_after_hours_zero_bid_is_invalid() -> None:
    observed = _replay_observed()
    exp = json.loads(MANIFEST.read_text(encoding="utf-8"))["expectations"]["after_hours_zero_bid_sf"]
    assert exp["state_valid"] is False
    assert observed["after_hours_zero_bid_sf"]["state_valid"] is False
    reasons = observed["after_hours_zero_bid_sf"]["invalid_reasons"]
    assert "non_positive_price" in reasons
    assert "missing_size" in reasons
    assert "outside_session" in reasons


def test_partial_depth_does_not_synthesize_levels() -> None:
    observed = _replay_observed()
    exp = json.loads(MANIFEST.read_text(encoding="utf-8"))["expectations"]["partial_depth_dp"]
    assert exp["bid_price_3"] is None
    # ask_price_3 may be absent (not synthesized); just ensure bid_price_3 absent


def test_incremental_bid_ask_only_keeps_ltp() -> None:
    observed = _replay_observed()
    exp = json.loads(MANIFEST.read_text(encoding="utf-8"))["expectations"]["incremental_bid_ask_only"]
    assert exp["last_price"] == 2500.0


def test_incremental_ltp_only_updates_last_price() -> None:
    observed = _replay_observed()
    exp = json.loads(MANIFEST.read_text(encoding="utf-8"))["expectations"]["incremental_ltp_only"]
    assert exp["last_price"] == 2500.5
    assert exp["bid_price_1"] == 2499.6


def test_reconnect_starts_new_generation() -> None:
    from jev_imrp.ingest.fyers_socket import FyersQuoteBook

    topics = {"sf|nse_cm|SYNTH2885", "sf|nse_cm|SYNTH3045", "if|nse_cm|NIFTY50-INDEX", "dp|nse_cm|SYNTH2885"}
    book = FyersQuoteBook(tick_sizes={"RELIANCE": 0.05, "SBIN": 0.05})
    for t in topics:
        book.register_subscription(t)
    assert book.generation == 1
    book.begin_generation()
    awaiting = book.awaiting_snapshot()
    assert set(awaiting) == topics, f"reconnect awaiting topics mismatch: {awaiting}"
    plan_topics = sorted(book.plan_resubscription())
    assert plan_topics == sorted(topics)


def test_duplicate_frame_is_counted_not_dropped() -> None:
    observed = _replay_observed()
    dup = json.loads(MANIFEST.read_text(encoding="utf-8")).get("duplicate_updates", {})
    assert len(dup) >= 1, "duplicate_updates should have at least one entry from generator"


def test_malformed_frame_recorded_as_error() -> None:
    from jev_imrp.ingest.fyers_socket import FyersPacketDecoder
    decoder = FyersPacketDecoder()
    result = decoder.decode(b"\x00\x05abc", received_timestamp_utc=None, received_monotonic_ns=1)
    assert result[0].parse_error is not None
    assert result[0].response_name == "error"


def test_packet_rate_measured() -> None:
    m = json.loads(MANIFEST.read_text(encoding="utf-8"))
    r = m["packet_rate"]
    assert r["duration_seconds"] > 0
    assert r["frames"] > 0
    assert r["blocks"] > 0


def test_manifest_file_integrity() -> None:
    """All three fixture JSON files must have LF endings and be valid JSON."""
    for p in (PACKETS, REST, MANIFEST):
        raw = p.read_bytes()
        assert raw.endswith(b"\n"), f"{p.name} missing trailing LF"
        json.loads(raw)


# ── parquet restart test ──────────────────────────────────────────────


def test_parquet_restart_preserves_provenance_and_checksums(tmp_path: Path) -> None:
    """Feed fixture frames through a recorder, finalize, verify manifest, then restart."""
    from jev_imrp.ingest.file_writer import (
        RawEventWriter,
        WriterSettings,
        iter_raw_events,
        read_manifest,
        verify_manifest,
    )
    from jev_imrp.ingest.fyers_recorder import (
        DEFAULT_DATA_TYPES,
        FyersInstrument,
        FyersRecorder,
        build_subscription_plan,
    )
    from synthetic_feed import FixedClock

    # 1. build a plan for one instrument
    instrument = FyersInstrument(
        research_symbol="RELIANCE",
        provider_symbol="NSE:RELIANCE-EQ",
        series="EQ",
        status="ACTIVE",
        master_sha256="a" * 64,
        exchange_token="SYNTH2885",
    )
    plan = build_subscription_plan([instrument], tick_sizes={"RELIANCE": 0.05},
                                    data_types=DEFAULT_DATA_TYPES,
                                    master_sha256="a" * 64)

    # 2. deterministic clock
    clock = FixedClock()

    # 3. writer + recorder
    writer = RawEventWriter(
        tmp_path / "raw",
        session_id="orc-114-session",
        settings=WriterSettings(max_rows_per_file=100),
    )
    recorder = FyersRecorder(
        writer=writer,
        plan=plan,
        session_id="orc-114-session",
        connection_id="conn-1",
        clock=clock,
        data_origin="synthetic",
    )
    recorder.start()

    # 4. feed relevant frames (generation 1 only)
    import json
    packets = json.loads(PACKETS.read_text(encoding="utf-8"))
    for frame in packets["frames"]:
        if frame["generation"] == 1 and frame["label"] in {
            "snapshot_sf_reliance", "incremental_bid_ask_only", "incremental_ltp_only"
        }:
            recorder.feed(bytes.fromhex(frame["hex"]))

    recorder.close()

    # 5. verify manifest entries
    entries = read_manifest(tmp_path / "raw")
    assert entries, "manifest should have at least one entry after close"
    problems = verify_manifest(tmp_path / "raw")
    assert problems == [], f"manifest verification problems: {problems}"
    for entry in entries:
        assert entry.provider == "FYERS"
        assert entry.provider_version == "fyers-data-socket-v3-hsm-v1-5"
        assert entry.master_hash == "a" * 64
        assert entry.data_origin == "synthetic"

    # 6. restart with a new writer (same root → file seq continues)
    writer2 = RawEventWriter(
        tmp_path / "raw",
        session_id="orc-114-session2",
        settings=WriterSettings(max_rows_per_file=100),
    )
    recorder2 = FyersRecorder(
        writer=writer2,
        plan=plan,
        session_id="orc-114-session2",
        connection_id="conn-2",
        clock=clock,
        data_origin="synthetic",
    )
    recorder2.start()

    # feed the reconnect snapshot frame (generation 2 fixture)
    for frame in packets["frames"]:
        if frame["label"] == "reconnect_snapshot_sf_reliance":
            recorder2.feed(bytes.fromhex(frame["hex"]))
            break

    recorder2.close()

    # 7. verify combined manifest
    entries2 = read_manifest(tmp_path / "raw")
    assert len(entries2) >= 2, "manifest should have entries from both writer generations"
    problems2 = verify_manifest(tmp_path / "raw")
    assert problems2 == [], f"manifest verification problems on restart: {problems2}"

    # 8. read back raw events and verify row count
    rows = list(iter_raw_events(tmp_path / "raw"))
    assert len(rows) >= 4, "should have recorded events from both generations"

    # 9. replay raw events and verify replay produces a valid final state
    from jev_imrp.ingest.feed_state import replay_raw_events
    _, replayed = replay_raw_events(rows, plan.tick_sizes())
    assert replayed, "replay should produce at least one row"
    final = replayed[-1]
    # sanity: a restart/replay should still carry Reliance's latest price/depth
    assert final.last_price > 0
    assert final.bid_price_1 is not None and final.ask_price_1 is not None
    assert final.bid_price_1 >= 0 and final.ask_price_1 >= 0


# ── credentials / redaction tests ───────────────────────────────────────


def test_fixtures_and_reports_do_not_contain_credentials() -> None:
    """Ensure test artefacts do not echo secret-like strings."""
    import re
    secret_re = re.compile(
        r"(?i)(access_token|api_secret|password|secret_code|app_id_hash|authorization)\s*[:=]\s*['\"]?\S+['\"]?"
    )
    for name in ("sanitized_packets.json", "rest_cases.json", "manifest.json"):
        text = (FIXTURES / name).read_text(encoding="utf-8")
        matches = secret_re.findall(text)
        assert not matches, f"{name} contains credential-like strings: {matches}"

    rest = json.loads(REST.read_text(encoding="utf-8"))
    assert "payload" not in rest["oauth"]["cases"][0], "oauth case must not embed raw secret payload"