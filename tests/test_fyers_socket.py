"""Offline qualification tests for the FYERS v3 data socket.

Mapping of the ORC-65 acceptance items to tests:

===  ==========================================================================
1    documented packet format decodes              test_control_frame_parses
                                                    test_snapshot_scales_prices
                                                    test_topic_id_roundtrip
2    quote/source clock semantics                  test_quote_age_uses_monotonic
                                                    test_clock_implausible_not_reinterpreted
                                                    test_outside_session_is_soft
                                                    test_timestamp_regression_flagged
                                                    test_depth_has_no_source_time
3    reconnect/resubscription behaviour            test_reconnect_requires_resubscription
4    sanitized packet evidence + manifest          test_fixture_is_synthetic_and_hashed
                                                    test_manifest_records_open_items
5    packet rate measurement                       test_packet_rate_from_replay
6    fixture replay end to end                     test_full_fixture_replay
===  ==========================================================================
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fyers_packets as fp
from jev_imrp.ingest.fyers_socket import (
    ABSENT,
    DEFAULT_MAX_QUOTE_AGE_MS,
    PARSER_VERSION,
    REASON_CLOCK_IMPLAUSIBLE,
    REASON_CROSSED,
    REASON_LOCKED,
    REASON_MISSING_PRICE,
    REASON_MISSING_SIZE,
    REASON_NO_SNAPSHOT,
    REASON_OUT_OF_TICK,
    REASON_OUTSIDE_SESSION,
    REASON_RECONNECT,
    REASON_TIMESTAMP_REGRESSION,
    FyersPacketDecoder,
    FyersQuoteBook,
    decode_frame,
    measure_packet_rate,
    parse_source_time,
)
from synthetic_feed import FixedClock

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "fyers_socket"


def decode(decoder: FyersPacketDecoder, raw: bytes, clock: FixedClock, generation: int = 1):
    moment, monotonic_ns = clock()
    return decoder.decode(
        raw,
        received_timestamp_utc=moment,
        received_monotonic_ns=monotonic_ns,
        generation=generation,
        data_origin="synthetic",
    )


def snapshot_frame(**sf_overrides: int) -> bytes:
    return fp.data_frame(
        1,
        [
            fp.snapshot_block(
                101,
                fp.SF_TOPIC,
                fp.sf_values(**sf_overrides),
                exchange_token="SYNTH2885",
                symbol="RELIANCE",
            )
        ],
    )


def book_with_snapshot(**sf_overrides: int) -> tuple[FyersQuoteBook, FixedClock]:
    book = FyersQuoteBook(tick_sizes={"RELIANCE": 0.05})
    book.register_subscription(fp.SF_TOPIC, "RELIANCE")
    clock = FixedClock()
    decoder = FyersPacketDecoder()
    for packet in decode(decoder, snapshot_frame(**sf_overrides), clock):
        book.apply(packet)
    return book, clock


def test_control_frame_parses() -> None:
    frame = decode_frame(fp.auth_ok())
    assert frame.response_type == 1
    assert frame.response_name == "cn"
    assert frame.status == "K"
    assert frame.blocks == ()


def test_snapshot_scales_prices() -> None:
    frame = decode_frame(snapshot_frame())
    assert frame.scrip_count == 1
    block = frame.blocks[0]
    assert block.frame_kind == "snapshot"
    assert block.topic_name == fp.SF_TOPIC
    assert block.exchange == "NSE"
    assert block.symbol == "RELIANCE"
    assert block.multiplier == 1
    assert block.precision == 2
    # Prices are scaled; sizes, quantities and the clock stay integers.
    assert block.fields["ltp"] == pytest.approx(2500.00)
    assert block.fields["bid_price"] == pytest.approx(2499.50)
    assert block.fields["ask_price"] == pytest.approx(2500.50)
    assert block.fields["bid_size"] == 10
    assert isinstance(block.fields["vol_traded_today"], int)
    assert block.source_time_raw == fp.SOURCE_EPOCH
    assert block.source_time_utc == fp.SOURCE_TIME
    assert block.source_clock_reason is None


def test_topic_id_roundtrip() -> None:
    topic_id = 0x0102
    decoder = FyersPacketDecoder()
    clock = FixedClock()
    snapshot = fp.data_frame(
        1,
        [fp.snapshot_block(topic_id, "sf|nse_cm|X", fp.sf_values(), symbol="X")],
    )
    packets = decode(decoder, snapshot, clock)
    assert packets[0].block is not None
    assert packets[0].block.topic_id == topic_id
    incremental = fp.data_frame(2, [fp.full_block(topic_id, fp.only(ltp=250_100))])
    packets = decode(decoder, incremental, clock)
    assert packets[0].block is not None
    assert packets[0].block.topic_name == "sf|nse_cm|X"
    assert packets[0].block.fields["ltp"] == pytest.approx(2501.00)


def test_incremental_requires_prior_snapshot() -> None:
    decoder = FyersPacketDecoder()
    packets = decode(
        decoder, fp.data_frame(1, [fp.full_block(101, fp.only(ltp=250_000))]), FixedClock()
    )
    assert packets[0].parse_error is not None
    assert packets[0].block is None
    assert "unknown topic id" in packets[0].parse_error


def test_incremental_merges_sentinel_fields() -> None:
    book, clock = book_with_snapshot()
    decoder = FyersPacketDecoder()
    decode(decoder, snapshot_frame(), clock)
    update = fp.data_frame(
        2,
        [
            fp.full_block(
                101,
                fp.only(ltp=250_050, bid_price=250_000, ask_price=250_100, bid_size=11),
            )
        ],
    )
    state = None
    for packet in decode(decoder, update, clock):
        state = book.apply(packet)
    assert state is not None
    assert state.fields["ltp"] == pytest.approx(2500.50)
    assert state.fields["bid_price"] == pytest.approx(2500.00)
    assert state.fields["bid_size"] == 11
    # Untouched fields survive the sparse incremental frame.
    assert state.fields["ask_size"] == 12
    assert state.fields["prev_close_price"] == pytest.approx(2475.00)
    assert state.state_valid


def test_lite_updates_ltp_only() -> None:
    book = FyersQuoteBook()
    decoder = FyersPacketDecoder()
    clock = FixedClock()
    for packet in decode(decoder, snapshot_frame(), clock):
        book.apply(packet)
    lite = fp.data_frame(2, [fp.lite_block(101, 250_100)])
    state = None
    for packet in decode(decoder, lite, clock):
        state = book.apply(packet)
    assert state is not None
    assert state.fields["ltp"] == pytest.approx(2501.00)
    assert state.fields["bid_price"] == pytest.approx(2499.50)


def test_depth_has_no_source_time() -> None:
    decoder = FyersPacketDecoder()
    clock = FixedClock()
    depth = fp.data_frame(
        1,
        [fp.snapshot_block(104, fp.DP_TOPIC, fp.dp_values(), symbol="RELIANCE")],
    )
    packets = decode(decoder, depth, clock)
    block = packets[0].block
    assert block is not None
    assert block.source_time_raw is None
    assert block.source_time_utc is None
    assert block.source_clock_reason is None
    assert block.fields["bid_price1"] == pytest.approx(2499.50)
    assert block.fields["ask_size1"] == 12


def test_quote_age_uses_monotonic() -> None:
    book, clock = book_with_snapshot()
    state = book.states[fp.SF_TOPIC]
    assert state.last_valid_monotonic_ns is not None
    now = state.last_valid_monotonic_ns + 2_500_000_000
    assert state.quote_age_ms(now) == pytest.approx(2500.0)
    # A wall-clock jump must not change the age: only monotonic time is used.
    clock.advance(3600)
    assert state.quote_age_ms(now) == pytest.approx(2500.0)


def test_stale_keys_threshold() -> None:
    book, _ = book_with_snapshot()
    state = book.states[fp.SF_TOPIC]
    assert state.last_valid_monotonic_ns is not None
    fresh = state.last_valid_monotonic_ns + int(DEFAULT_MAX_QUOTE_AGE_MS * 1_000_000) - 1
    assert book.stale_keys(fresh) == {}
    stale = state.last_valid_monotonic_ns + int(DEFAULT_MAX_QUOTE_AGE_MS * 1_000_000) + 1
    assert fp.SF_TOPIC in book.stale_keys(stale)


def test_crossed_quote_is_invalid() -> None:
    book, _ = book_with_snapshot(bid_price=250_100, ask_price=250_000)
    state = book.states[fp.SF_TOPIC]
    assert REASON_CROSSED in state.invalid_reasons
    assert not state.state_valid


def test_locked_quote_is_soft() -> None:
    book, _ = book_with_snapshot(bid_price=250_000, ask_price=250_000)
    state = book.states[fp.SF_TOPIC]
    assert REASON_LOCKED in state.invalid_reasons
    assert state.state_valid


def test_missing_size_and_price_are_flagged() -> None:
    book, _ = book_with_snapshot(bid_size=ABSENT)
    assert REASON_MISSING_SIZE in book.states[fp.SF_TOPIC].invalid_reasons
    book, _ = book_with_snapshot(bid_price=ABSENT)
    assert REASON_MISSING_PRICE in book.states[fp.SF_TOPIC].invalid_reasons


def test_out_of_tick_price_is_flagged() -> None:
    book, _ = book_with_snapshot(bid_price=250_001)
    assert REASON_OUT_OF_TICK in book.states[fp.SF_TOPIC].invalid_reasons


def test_clock_implausible_not_reinterpreted() -> None:
    # A millisecond epoch (1.7e12) cannot even fit the int32 field, so the
    # remaining risk is a small bogus value. It must be flagged, not rescaled.
    out_of_window = 1_234_567
    book, _ = book_with_snapshot(exch_feed_time=out_of_window)
    state = book.states[fp.SF_TOPIC]
    assert state.source_time_raw == out_of_window
    assert state.source_time_utc is None
    assert REASON_CLOCK_IMPLAUSIBLE in state.invalid_reasons
    assert state.state_valid  # soft: the price is fine, the clock unit is not trusted
    utc, reason = parse_source_time(out_of_window)
    assert utc is None and reason == REASON_CLOCK_IMPLAUSIBLE
    assert parse_source_time(fp.SOURCE_EPOCH)[0] == fp.SOURCE_TIME


def test_outside_session_is_soft() -> None:
    outside = int(datetime(2026, 7, 1, 12, 0, tzinfo=UTC).timestamp())
    book, _ = book_with_snapshot(exch_feed_time=outside)
    state = book.states[fp.SF_TOPIC]
    assert REASON_OUTSIDE_SESSION in state.invalid_reasons
    assert state.state_valid


def test_timestamp_regression_flagged() -> None:
    book, clock = book_with_snapshot()
    decoder = FyersPacketDecoder()
    decode(decoder, snapshot_frame(), clock)
    older = snapshot_frame(exch_feed_time=fp.SOURCE_EPOCH - 10)
    state = None
    for packet in decode(decoder, older, clock):
        state = book.apply(packet)
    assert state is not None
    assert REASON_TIMESTAMP_REGRESSION in state.invalid_reasons


def test_incremental_before_snapshot_is_flagged() -> None:
    book = FyersQuoteBook()
    decoder = FyersPacketDecoder()
    clock = FixedClock()
    # The decoder learns the topic id but the book never sees a snapshot.
    decode(decoder, snapshot_frame(), clock)
    state = None
    for packet in decode(
        decoder, fp.data_frame(2, [fp.full_block(101, fp.only(ltp=250_100))]), clock
    ):
        state = book.apply(packet)
    assert state is not None
    assert REASON_NO_SNAPSHOT in state.invalid_reasons
    assert not state.state_valid
    assert any("before snapshot" in anomaly for anomaly in book.connection_anomalies)


def test_reconnect_requires_resubscription() -> None:
    book, clock = book_with_snapshot()
    book.register_subscription(fp.SBIN_TOPIC, "SBIN")
    decoder = FyersPacketDecoder()
    decode(decoder, snapshot_frame(), clock)

    generation = book.begin_generation()
    assert generation == 2
    assert all(REASON_RECONNECT in state.invalid_reasons for state in book.states.values())
    assert book.plan_resubscription() == sorted([fp.SF_TOPIC, fp.SBIN_TOPIC])
    assert book.awaiting_snapshot() == sorted([fp.SF_TOPIC, fp.SBIN_TOPIC])

    reconnect_snapshot = snapshot_frame(exch_feed_time=fp.SOURCE_EPOCH + 1)
    for packet in decode(decoder, reconnect_snapshot, clock, generation=2):
        book.apply(packet)
    assert book.states[fp.SF_TOPIC].state_valid
    assert book.states[fp.SF_TOPIC].generation == 2
    assert book.awaiting_snapshot() == [fp.SBIN_TOPIC]


def test_duplicate_update_counted() -> None:
    book = FyersQuoteBook()
    decoder = FyersPacketDecoder()
    clock = FixedClock()
    raw = snapshot_frame()
    for packet in decode(decoder, raw, clock):
        book.apply(packet)
    for packet in decode(decoder, raw, clock):
        book.apply(packet)
    assert book.duplicate_updates[fp.SF_TOPIC] == 1


def test_truncated_and_unknown_block_are_parse_errors() -> None:
    decoder = FyersPacketDecoder()
    clock = FixedClock()
    truncated = fp.data_frame(1, [fp.full_block(101, fp.only(ltp=1))])[:-4]
    packets = decode(decoder, truncated, clock)
    assert packets[0].parse_error is not None
    unknown = fp.data_frame(2, [bytes([99]) + b"\x69\x00"])
    packets = decode(decoder, unknown, clock)
    assert packets[0].parse_error is not None
    assert "unknown scrip block type" in packets[0].parse_error


def test_measure_packet_rate_from_replay() -> None:
    fixture = json.loads((FIXTURE_DIR / "sanitized_packets.json").read_text(encoding="utf-8"))
    decoder = FyersPacketDecoder()
    clock = FixedClock(step=timedelta(milliseconds=100))
    packets = []
    for entry in fixture["frames"]:
        packets.extend(decode(decoder, bytes.fromhex(entry["hex"]), clock, entry["generation"]))
    rate = measure_packet_rate(packets)
    assert rate.frames == 6
    assert rate.blocks == 9
    assert rate.duration_seconds == pytest.approx(0.5)
    assert rate.per_topic_type["sf"] == 6
    assert rate.per_topic[fp.SF_TOPIC] == 4


def test_fixture_is_synthetic_and_hashed() -> None:
    payload = (FIXTURE_DIR / "sanitized_packets.json").read_bytes()
    fixture = json.loads(payload)
    manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert fixture["data_origin"] == "synthetic"
    assert "warning" in fixture and "SYNTHETIC" in fixture["warning"]
    assert manifest["data_origin"] == "synthetic"
    assert manifest["fixture_sha256"] == hashlib.sha256(payload).hexdigest()
    assert manifest["fixture_file"] == "sanitized_packets.json"
    for entry in fixture["frames"]:
        assert isinstance(bytes.fromhex(entry["hex"]), bytes)


def test_manifest_records_open_items() -> None:
    manifest = json.loads((FIXTURE_DIR / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["live_capture"] == "NOT RUN"
    assert manifest["quote_clock"]["live_unit_verification"] == "NOT RUN"
    assert manifest["packet_rate"]["live_measurement"] == "NOT RUN"
    assert manifest["protocol"]["topic_id_byte_order"]["status"].startswith("OPEN")
    assert manifest["reconnect"]["server_persists_subscriptions"] is False
    assert manifest["l1_availability"]["dp"]["source_time"] is False
    assert manifest["packet_rate"]["data_origin"] == "synthetic"


def test_full_fixture_replay() -> None:
    fixture = json.loads((FIXTURE_DIR / "sanitized_packets.json").read_text(encoding="utf-8"))
    decoder = FyersPacketDecoder()
    book = FyersQuoteBook(tick_sizes={"RELIANCE": 0.05, "SBIN": 0.05})
    for topic, symbol in (
        (fp.SF_TOPIC, "RELIANCE"),
        (fp.SBIN_TOPIC, "SBIN"),
        (fp.INDEX_TOPIC, "NIFTY50-INDEX"),
        (fp.DP_TOPIC, "RELIANCE"),
    ):
        book.register_subscription(topic, symbol)
    clock = FixedClock(step=timedelta(milliseconds=100))
    generation = 1
    packets = []
    for entry in fixture["frames"]:
        if entry["generation"] != generation:
            generation = entry["generation"]
            book.begin_generation()
        batch = decode(decoder, bytes.fromhex(entry["hex"]), clock, generation)
        packets.extend(batch)
        for packet in batch:
            book.apply(packet)

    reliance = book.states[fp.SF_TOPIC]
    assert reliance.generation == 2
    assert reliance.fields["ltp"] == pytest.approx(2501.00)
    assert reliance.fields["bid_price"] == pytest.approx(2500.50)
    assert reliance.state_valid
    assert book.duplicate_updates[fp.SF_TOPIC] == 1
    assert book.awaiting_snapshot() == sorted([fp.SBIN_TOPIC, fp.INDEX_TOPIC, fp.DP_TOPIC])
    assert sum(1 for p in packets if p.parse_error) == 2
    assert len(book.connection_anomalies) >= 2


def test_state_row_and_parser_version() -> None:
    book, _ = book_with_snapshot()
    row = book.states[fp.SF_TOPIC].to_row()
    assert row["provider"] == "FYERS"
    assert row["parser_version"] == PARSER_VERSION
    assert row["symbol"] == "RELIANCE"
    assert row["bid_price_1"] == pytest.approx(2499.50)
    assert row["state_valid"] is True


def test_no_shoonya_vocabulary_leaks() -> None:
    """The FYERS path must not import the Shoonya state machine."""
    module = Path(__file__).resolve().parents[1] / "src/jev_imrp/ingest/fyers_socket.py"
    source = module.read_text(encoding="utf-8")
    assert "feed_state" not in source
    assert "shoonya_feed_client" not in source


def test_invalid_frame_does_not_mutate_state() -> None:
    book = FyersQuoteBook()
    decoder = FyersPacketDecoder()
    clock = FixedClock()
    for packet in decode(decoder, snapshot_frame(), clock):
        book.apply(packet)
    before = book.states[fp.SF_TOPIC]
    for packet in decode(decoder, b"\x00\x01\x06", clock):
        book.apply(packet)
    assert book.states[fp.SF_TOPIC] == before
    assert book.connection_anomalies
