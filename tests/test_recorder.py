"""Recorder persistence, rotation, manifest and recovery tests.

Plan coverage in this file:

* 5.6.8  a forced process stop does not silently lose already completed batches
         -> ``test_hard_stop_keeps_completed_batches``, ``test_unfinalized_temp_is_quarantined``
* 5.5    bounded batches, rotation by rows/bytes/time, never one file per message
         -> ``test_batches_are_bounded_not_one_file_per_message``, ``test_rotation_*``
* 5.5    durable manifest with counts, ranges, checksums, parser/schema version
         -> ``test_manifest_records_provenance_and_checksums``, ``test_manifest_detects_tampering``
* 5.5    a file is complete only after write + close, no destructive overwrite
         -> ``test_existing_raw_file_is_never_overwritten``
* 5.5    restart detects and quarantines unfinalized files
         -> ``test_unfinalized_temp_is_quarantined``, ``test_manifest_read_after_restart``
* 5.6.9  feed time and receive time remain separately queryable in Parquet
         -> ``test_feed_and_receive_time_are_separately_queryable``
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jev_imrp.ingest.feed_state import QuoteStateBook
from jev_imrp.ingest.file_writer import (
    FEED_TYPE,
    RawEventWriter,
    WriterSettings,
    find_unfinalized,
    quarantine_unfinalized,
    read_manifest,
    read_raw_events,
    verify_manifest,
)
from jev_imrp.ingest.shoonya_recorder import Recorder
from synthetic_feed import FixedClock, load_fixture, tick_sizes

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"


def make_recorder(root: Path, settings: WriterSettings | None = None) -> Recorder:
    fixture = load_fixture("session_normal")
    writer = RawEventWriter(root, session_id="synthetic-session", settings=settings)
    recorder = Recorder(
        writer=writer,
        book=QuoteStateBook(tick_sizes=tick_sizes(fixture)),
        session_id="synthetic-session",
        connection_id="conn-1",
        clock=FixedClock(),
        data_origin="synthetic",
    )
    recorder.start()
    return recorder


def feed_messages(recorder: Recorder, count: int | None = None) -> None:
    messages = load_fixture("session_normal")["messages"]
    for message in messages[:count]:
        recorder.feed(message)


# --- bounded batches and rotation ------------------------------------------------
def test_batches_are_bounded_not_one_file_per_message(tmp_path):
    recorder = make_recorder(tmp_path / "raw", WriterSettings(max_rows_per_file=5))
    feed_messages(recorder)
    recorder.close()

    files = sorted((tmp_path / "raw").rglob("*.parquet"))
    assert len(files) > 1, "rotation must produce several files"
    assert all(path.stat().st_size > 0 for path in files)
    rows = sum(entry.row_count for entry in read_manifest(tmp_path / "raw"))
    assert rows == len(load_fixture("session_normal")["messages"])


def test_rotation_by_size_bound(tmp_path):
    recorder = make_recorder(
        tmp_path / "raw", WriterSettings(max_rows_per_file=10_000, max_bytes_per_file=200)
    )
    feed_messages(recorder)
    recorder.close()
    files = sorted((tmp_path / "raw").rglob("*.parquet"))
    assert len(files) > 1
    assert all(entry.row_count < 13 for entry in read_manifest(tmp_path / "raw"))


def test_single_event_never_rotates_on_age_alone(tmp_path):
    recorder = make_recorder(tmp_path / "raw", WriterSettings(max_seconds_per_file=0.0))
    recorder.feed(load_fixture("session_normal")["messages"][1])
    assert recorder.writer.pending_rows == 1
    assert read_manifest(tmp_path / "raw") == []
    recorder.close()


def test_partition_is_trading_date_and_feed_type(tmp_path):
    recorder = make_recorder(tmp_path / "raw", WriterSettings(max_rows_per_file=3))
    feed_messages(recorder)
    recorder.close()
    entries = read_manifest(tmp_path / "raw")
    assert entries, "at least one file must be finalized"
    for entry in entries:
        assert entry.feed_type == FEED_TYPE
        assert entry.trading_date == "2026-07-01"  # IST session date of the synthetic clock


# --- manifest durability --------------------------------------------------------
def test_manifest_records_provenance_and_checksums(tmp_path):
    recorder = make_recorder(tmp_path / "raw", WriterSettings(max_rows_per_file=4))
    feed_messages(recorder)
    recorder.close()

    entries = read_manifest(tmp_path / "raw")
    assert entries
    for entry in entries:
        assert entry.session_id == "synthetic-session"
        assert entry.subscription_generation >= 1
        assert entry.row_count > 0
        assert entry.first_received_utc <= entry.last_received_utc
        assert len(entry.sha256) == 64
        assert entry.parser_version.startswith("parser_")
        assert entry.schema_version == "raw_events_v1"
    assert verify_manifest(tmp_path / "raw") == []

    # the manifest is append-only JSONL: one line per completed file
    lines = (tmp_path / "raw" / "manifest.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == len(entries)
    assert json.loads(lines[0])["row_count"] == entries[0].row_count


def test_manifest_detects_tampering(tmp_path):
    recorder = make_recorder(tmp_path / "raw", WriterSettings(max_rows_per_file=4))
    feed_messages(recorder)
    recorder.close()

    target = sorted((tmp_path / "raw").rglob("*.parquet"))[0]
    target.write_bytes(target.read_bytes() + b"tampered")
    problems = verify_manifest(tmp_path / "raw")
    assert problems and "mismatch" in problems[0]


def test_existing_raw_file_is_never_overwritten(tmp_path):
    recorder = make_recorder(tmp_path / "raw", WriterSettings(max_rows_per_file=3))
    feed_messages(recorder)
    recorder.close()
    before = {path: path.read_bytes() for path in (tmp_path / "raw").rglob("*.parquet")}

    # a second recorder on the same root continues numbering and adds files only
    second = make_recorder(tmp_path / "raw", WriterSettings(max_rows_per_file=3))
    feed_messages(second)
    second.close()
    after = {path: path.read_bytes() for path in (tmp_path / "raw").rglob("*.parquet")}
    for path, payload in before.items():
        assert after[path] == payload, "existing finalized raw data must be immutable"


# --- forced stop and recovery ---------------------------------------------------
HARD_STOP_SCRIPT = textwrap.dedent(
    """
    import sys
    sys.path.insert(0, {src!r})
    sys.path.insert(0, {tests!r})
    from synthetic_feed import FixedClock, load_fixture, tick_sizes
    from jev_imrp.ingest.feed_state import QuoteStateBook
    from jev_imrp.ingest.file_writer import RawEventWriter, WriterSettings
    from jev_imrp.ingest.shoonya_recorder import Recorder
    import os

    fixture = load_fixture("session_normal")
    root = {root!r}
    writer = RawEventWriter(root, session_id="hard-stop",
                            settings=WriterSettings(max_rows_per_file=4))
    recorder = Recorder(writer=writer, book=QuoteStateBook(tick_sizes=tick_sizes(fixture)),
                        session_id="hard-stop", clock=FixedClock(), data_origin="synthetic")
    recorder.start()
    for message in fixture["messages"][:10]:
        recorder.feed(message)
    # one batch is durable here...
    writer.finalize()
    # ...and the process dies without closing anything else.
    os._exit(9)
    """
)


def test_hard_stop_keeps_completed_batches(tmp_path):
    root = tmp_path / "raw"
    script = HARD_STOP_SCRIPT.format(
        src=str(SRC), tests=str(Path(__file__).resolve().parent), root=str(root)
    )
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
    assert result.returncode == 9, result.stderr

    entries = read_manifest(root)
    assert entries, "a completed batch must survive a forced stop"
    total = sum(entry.row_count for entry in entries)
    assert total >= 4
    assert verify_manifest(root) == []
    # the events after the durable batch were never finalized and are not claimed
    table = read_raw_events(root)
    assert table.num_rows == total


def test_unfinalized_temp_is_quarantined(tmp_path):
    root = tmp_path / "raw"
    recorder = make_recorder(root, WriterSettings(max_rows_per_file=3))
    feed_messages(recorder)
    recorder.close()

    # simulate a write that never finished: a temp file with no manifest entry
    partition = sorted((root).rglob("*.parquet"))[0].parent
    orphan = partition / "part-009-999999.parquet.tmp"
    orphan.write_bytes(b"PAR1 truncated")

    assert find_unfinalized(root) == [orphan]
    moved = quarantine_unfinalized(root)
    assert moved == [tmp_path / "raw" / "_quarantine" / partition.name / orphan.name]
    assert not orphan.exists()
    assert find_unfinalized(root) == [], "quarantined files must not be re-detected"
    assert moved[0].read_bytes() == b"PAR1 truncated", "quarantine keeps the bytes"
    # finalized data is untouched and still verifies
    assert verify_manifest(root) == []


def test_manifest_read_after_restart(tmp_path):
    root = tmp_path / "raw"
    first = make_recorder(root, WriterSettings(max_rows_per_file=4))
    feed_messages(first)
    first.close()
    before = read_manifest(root)

    moved = quarantine_unfinalized(root)  # startup recovery step
    assert moved == []

    second = make_recorder(root, WriterSettings(max_rows_per_file=4))
    feed_messages(second)
    second.close()
    after = read_manifest(root)

    assert len(after) > len(before)
    assert [e.path for e in after[: len(before)]] == [e.path for e in before]
    # file numbering continues after restart: no existing path is reused
    assert len({e.path for e in after}) == len(after)
    assert verify_manifest(root) == []


# --- timestamps in the persisted table ------------------------------------------
def test_feed_and_receive_time_are_separately_queryable(tmp_path):
    recorder = make_recorder(tmp_path / "raw", WriterSettings(max_rows_per_file=100))
    feed_messages(recorder, count=3)
    recorder.close()

    table = read_raw_events(tmp_path / "raw").to_pylist()
    quote_rows = [row for row in table if row["message_type"] == "tk"]
    assert quote_rows
    row = quote_rows[0]
    assert row["feed_timestamp_raw"] == "1790200000"
    assert row["feed_timestamp_utc"] is not None
    assert row["received_timestamp_utc"] is not None
    # feed time is 2026-05-22-ish epoch seconds, receive time is the synthetic
    # clock: they must be independently readable and must not be conflated
    assert row["feed_timestamp_utc"] != row["received_timestamp_utc"]
    assert row["received_monotonic_ns"] > 0
    assert str(row["feed_timestamp_utc"].tzinfo) == "UTC"
    assert str(row["received_timestamp_utc"].tzinfo) == "UTC"


def test_raw_payload_is_stored_exactly_as_received(tmp_path):
    recorder = make_recorder(tmp_path / "raw", WriterSettings(max_rows_per_file=100))
    feed_messages(recorder, count=2)
    recorder.close()

    table = read_raw_events(tmp_path / "raw").to_pylist()
    sent = load_fixture("session_normal")["messages"][1]
    stored = next(row for row in table if row["message_type"] == "tk")
    assert json.loads(stored["raw_payload_json"]) == sent
    assert stored["data_origin"] == "synthetic", "synthetic captures must stay labelled"
    assert stored["ingestion_status"] == "ok"


def test_unparseable_message_is_recorded_not_dropped(tmp_path):
    recorder = make_recorder(tmp_path / "raw", WriterSettings(max_rows_per_file=100))
    _, state = recorder.feed("{not json at all")
    recorder.feed("[1, 2, 3]")
    recorder.close()

    assert state is None
    rows = read_raw_events(tmp_path / "raw").to_pylist()
    assert len(rows) == 2
    assert {row["ingestion_status"] for row in rows} == {"parse_error"}
    assert json.loads(rows[0]["raw_payload_json"])["__unparsed__"] == "{not json at all"
    assert recorder.counters.parse_errors == 2


def test_write_after_close_is_refused(tmp_path):
    recorder = make_recorder(tmp_path / "raw")
    recorder.close()
    with pytest.raises(RuntimeError):
        recorder.feed(load_fixture("session_normal")["messages"][1])


# --- online recorder health checks (plan 14.1, 14.2) ---------------------------
def test_health_report_is_clean_for_a_healthy_capture(tmp_path):

    from jev_imrp.ingest import health_checks

    fixture = load_fixture("session_normal")
    recorder = make_recorder(tmp_path / "raw", WriterSettings(max_rows_per_file=100))
    recorder.register_subscribe_frame({"t": "t", "k": "NSE|9900001#NSE|9900002"})
    recorder.run(fixture["messages"][:6])  # clean updates, before the crossed quote
    as_of = recorder.counters.last_received_utc

    report = health_checks.evaluate(recorder, as_of_utc=as_of, max_quote_age_ms=60_000)
    failed = {check.name for check in report.failures}
    assert "raw_writes_recorded" not in failed
    assert "parser_exceptions" not in failed
    assert "subscriptions_awaiting_snapshot" not in failed
    # the crossed quote in the fixture is a real, flagged anomaly
    assert report.to_dict()["checks"], "the report is structured data, not a bool"


def test_health_report_stops_decisions_on_a_missing_snapshot(tmp_path):
    from jev_imrp.ingest import health_checks

    fixture = load_fixture("session_normal")
    recorder = make_recorder(tmp_path / "raw")
    recorder.register_subscribe_frame({"t": "t", "k": "NSE|9900001#NSE|9900002"})
    recorder.run(fixture["messages"][:2])  # only the equity snapshot arrived

    report = health_checks.evaluate(recorder, as_of_utc=recorder.counters.last_received_utc)
    stop, reasons = health_checks.should_stop_emitting(report)
    assert stop, "a subscribed token without a snapshot must stop decisions"
    assert any("awaiting_snapshot" in reason or "subscriptions" in reason for reason in reasons)


def test_health_report_flags_stale_quotes_and_unknown_types(tmp_path):
    from datetime import timedelta

    from jev_imrp.ingest import health_checks

    fixture = load_fixture("session_normal")
    recorder = make_recorder(tmp_path / "raw")
    recorder.run(fixture["messages"])

    as_of = recorder.counters.last_received_utc + timedelta(seconds=30)
    report = health_checks.evaluate(recorder, as_of_utc=as_of, max_quote_age_ms=1_000)
    names = {check.name for check in report.failures}
    assert "stale_quotes" in names
    assert "unknown_message_types" in names
    stop, reasons = health_checks.should_stop_emitting(report)
    assert stop and len(reasons) >= 2

    fresh = health_checks.evaluate(
        recorder, as_of_utc=recorder.counters.last_received_utc, max_quote_age_ms=60_000
    )
    assert "stale_quotes" not in {check.name for check in fresh.failures}


def test_clock_skew_is_measured_not_corrected():
    from jev_imrp.common.timeutils import utc_now
    from jev_imrp.ingest import health_checks

    stamped = utc_now()
    assert health_checks.clock_skew_seconds(stamped, stamped) == 0.0
    assert health_checks.clock_skew_seconds(stamped - timedelta(seconds=30), stamped) == 30.0


# --- no fabricated depth, no invented instrument data --------------------------
def test_no_depth_levels_beyond_level_1_are_created(tmp_path):
    """Plan 6.1/7.3: this feed is L1 only, so levels 2-5 must not exist."""
    fixture = load_fixture("session_normal")
    recorder = make_recorder(tmp_path / "raw", WriterSettings(max_rows_per_file=100))
    _, state = recorder.feed(fixture["messages"][1])
    row = state.to_row()

    forbidden = [name for name in row if name.endswith(("_2", "_3", "_4", "_5"))]
    assert forbidden == [], f"unexpected depth columns: {forbidden}"
    assert set(row) >= {"bid_price_1", "bid_qty_1", "ask_price_1", "ask_qty_1"}


def test_instrument_master_reports_unresolved_tokens():
    from jev_imrp.data.instrument_master import load_instrument_master

    master = load_instrument_master()
    assert master.symbols(), "the frozen universe must load"
    assert master.universe_version == "v1"
    unresolved = master.unresolved_tokens()
    assert len(unresolved) == len(master.symbols()), (
        "Sprint 0 left every broker token null; they must be read from the live "
        "instrument master, never invented"
    )
    assert master.tick_sizes() == {}, "no tick check is possible without a token"




