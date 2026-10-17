"""Quote-state reconstruction tests: plan section 5.6 acceptance items 1-7.

Mapping of plan items to tests in this file:

===  ==========================================================================
1    snapshot creates expected state                  test_tk_snapshot_creates_state
2    sparse incremental changes only listed fields   test_sparse_tf_changes_only_present_fields
3    unchanged fields remain unchanged                test_unchanged_fields_survive_updates
4    missing fields are not converted to zero        test_missing_fields_stay_null
5    an update without LTP still updates state        test_update_without_ltp_updates_quote_state
6    reconnect restores and reinitializes             test_reconnect_requires_new_snapshots
7    invalid/stale data is flagged, never accepted    test_crossed_quote_is_flagged_invalid
                                                        test_out_of_tick_price_is_flagged
                                                        test_missing_size_is_flagged
                                                        test_stale_state_is_reported_not_accepted
                                                        test_duplicate_and_regressed_updates_are_flagged
===  ==========================================================================
"""

from __future__ import annotations

import sys
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jev_imrp.common.timeutils import utc_now
from jev_imrp.ingest.feed_state import (
    REASON_CROSSED,
    REASON_DUPLICATE,
    REASON_MISSING_SIZE,
    REASON_NO_SNAPSHOT,
    REASON_OUT_OF_TICK,
    REASON_RECONNECT,
    REASON_STALE,
    REASON_TIMESTAMP_REGRESSION,
    QuoteStateBook,
    stale_states,
)
from jev_imrp.ingest.file_writer import RawEventWriter
from jev_imrp.ingest.shoonya_recorder import Recorder
from synthetic_feed import FixedClock, load_fixture, tick_sizes

A = ("NSE", "9900001")
IDX = ("NSE", "9900002")


@pytest.fixture()
def normal():
    return load_fixture("session_normal")


@pytest.fixture()
def book(normal):
    return QuoteStateBook(tick_sizes=tick_sizes(normal))


@pytest.fixture()
def normal_recorder(tmp_path, normal):
    """A recorder over the normal fixture, wired to a real writer in tmp_path."""
    writer = RawEventWriter(tmp_path / "raw", session_id="synthetic-session")
    recorder = Recorder(
        writer=writer,
        book=QuoteStateBook(tick_sizes=tick_sizes(normal)),
        session_id="synthetic-session",
        connection_id="conn-1",
        clock=FixedClock(),
        data_origin="synthetic",
    )
    recorder.start()
    return recorder


def feed_all(recorder: Recorder, messages: list[dict]) -> list:
    return recorder.run(messages)


def snapshot_a(ft: str = "1790200100") -> dict:
    """A clean full snapshot for the synthetic equity token."""
    return {
        "t": "tk", "e": "NSE", "tk": "9900001", "ti": "SYNTHETIC-A", "ft": ft,
        "lp": "1000.00", "ls": "10", "v": "1000", "o": "995.00", "h": "1005.00", "l": "994.00",
        "c": "999.50", "ap": "999.80", "pp": "998.00", "pc": "997.50",
        "bq1": "50", "bp1": "999.90", "sq1": "40", "sp1": "1000.10",
    }


# --- 1: the initial snapshot creates the expected state ------------------------
def test_tk_snapshot_creates_state(normal_recorder, normal):
    messages = normal["messages"]
    _, state = normal_recorder.feed(messages[1])
    expected = normal["expected"]["after_snapshot_A"]

    assert state is not None
    assert state.message_type == "tk"
    assert state.last_price == expected["last_price"]
    assert state.bid_price_1 == expected["bid_price_1"]
    assert state.bid_qty_1 == expected["bid_qty_1"]
    assert state.ask_price_1 == expected["ask_price_1"]
    assert state.ask_qty_1 == expected["ask_qty_1"]
    assert state.volume == expected["volume"]
    assert state.state_valid, "a clean snapshot must be valid"
    assert state.invalid_reason is None


def test_second_token_gets_its_own_state(normal_recorder, normal):
    messages = normal["messages"]
    normal_recorder.feed(messages[1])
    _, state = normal_recorder.feed(messages[2])
    assert state is not None
    assert state.key == IDX
    assert normal_recorder.book.states[A].last_price == 1000.0
    assert normal_recorder.book.states[IDX].last_price == 5000.0


# --- 2 and 3: sparse merge, unchanged fields survive --------------------------
def test_sparse_tf_changes_only_present_fields(normal_recorder, normal):
    messages = normal["messages"]
    normal_recorder.feed(messages[1])  # snapshot
    expected = normal["expected"]["sparse_bp1_only"]
    _, state = normal_recorder.feed(messages[3])  # bp1 only

    assert state is not None
    assert state.bid_price_1 == expected["bid_price_1"]
    # unchanged fields keep their snapshot values, they are not reset
    assert state.ask_price_1 == expected["ask_price_1"]
    assert state.last_price == expected["last_price"]
    assert state.bid_qty_1 == 50.0
    assert state.fields_present == ("bp1",)


def test_unchanged_fields_survive_updates(normal_recorder, normal):
    messages = normal["messages"]
    normal_recorder.feed(messages[1])
    for index in (3, 4, 5):
        _, state = normal_recorder.feed(messages[index])
        assert state is not None
    assert state.open_price == 995.0
    assert state.high_price == 1005.0
    assert state.low_price == 994.0
    assert state.close_price == 999.5
    assert state.average_price == 999.8
    assert state.prev_close_price == 998.0
    assert state.bid_qty_1 == 75.0
    assert state.ask_qty_1 == 35.0
    assert state.last_traded_qty == 10.0


# --- 4: absent fields stay absent, never zero-filled ---------------------------
def test_missing_fields_stay_null(normal_recorder, normal):
    messages = normal["messages"]
    normal_recorder.feed(messages[1])
    _, equity = normal_recorder.feed(messages[1])
    _, index = normal_recorder.feed(messages[2])

    assert equity.open_interest is None, "equity tokens have no oi; it must not become 0.0"
    assert equity.previous_open_interest is None
    assert equity.total_open_interest is None
    # index-like token: no touchline at all, and no zeros invented for it
    assert index.bid_price_1 is None
    assert index.ask_price_1 is None
    assert index.bid_qty_1 is None
    assert index.ask_qty_1 is None
    row = index.to_row()
    assert row["bid_ask_spread"] is None


# --- 5: an update without LTP still updates quote state -----------------------
def test_update_without_ltp_updates_quote_state(normal_recorder, normal):
    messages = normal["messages"]
    normal_recorder.feed(messages[1])
    index_no_ltp = normal["expected"]["no_ltp_message_index"]
    event, state = normal_recorder.feed(messages[index_no_ltp])

    assert event.ingestion_status == "ok"
    assert state is not None, "a field-less update is still a valid, recorded event"
    assert state.state_valid
    assert state.fields_present == ()
    assert state.last_price == 1000.0, "no LTP in the message means no LTP change"
    # a later LTP-only update also keeps the touchline intact
    _, after = normal_recorder.feed(messages[8])
    assert after.last_price == 1003.0
    assert after.bid_price_1 == 999.9, "the LTP update carried no touchline field"
    assert after.volume == 1500.0
    assert after.state_valid


def test_touchline_only_update_is_not_invalidated(normal_recorder, normal):
    messages = normal["messages"]
    normal_recorder.feed(messages[1])
    _, state = normal_recorder.feed(messages[3])
    assert state.state_valid, "a quote-only change is a normal update, not an anomaly"


# --- 7a: crossed / locked quotes ---------------------------------------------
def test_crossed_quote_is_flagged_invalid(normal_recorder, normal):
    messages = normal["messages"]
    normal_recorder.feed(messages[1])
    index = normal["expected"]["crossed_message_index"]
    _, state = normal_recorder.feed(messages[index])

    assert state.bid_price_1 > state.ask_price_1
    assert not state.state_valid
    assert state.invalid_reason == REASON_CROSSED
    # the raw event is still recorded: invalid state never means dropped evidence
    assert normal_recorder.counters.written == normal_recorder.counters.received


def test_invalid_state_is_repaired_only_by_a_new_snapshot(normal_recorder, normal):
    messages = normal["messages"]
    normal_recorder.feed(messages[1])
    crossed = normal_recorder.feed(messages[normal["expected"]["crossed_message_index"]])[1]
    assert not crossed.state_valid

    _, later = normal_recorder.feed(messages[8])
    assert REASON_CROSSED in later.invalid_reasons, "an incremental update cannot repair"

    _, repaired = normal_recorder.feed(snapshot_a("1790200300"))
    assert repaired.state_valid
    assert repaired.invalid_reason is None


def test_non_positive_and_missing_size_are_flagged(normal_recorder, normal):
    normal_recorder.feed(normal["messages"][1])
    _, zero_bid = normal_recorder.feed(
        {"t": "tf", "e": "NSE", "tk": "9900001", "ft": "1790200080", "bp1": "0", "bq1": "10"}
    )
    assert not zero_bid.state_valid
    assert "non_positive_price" in zero_bid.invalid_reasons

    # a snapshot that carries prices without sizes: sizes stay absent, not zero
    _, no_size = normal_recorder.feed(
        {"t": "tk", "e": "NSE", "tk": "9900001", "ft": "1790200081",
         "bp1": "999.90", "sp1": "1000.10"}
    )
    assert not no_size.state_valid
    assert no_size.invalid_reasons.count(REASON_MISSING_SIZE) == 1
    assert no_size.bid_qty_1 is None and no_size.ask_qty_1 is None


def test_tick_size_rules_apply_to_index_tokens_too(normal_recorder, normal):
    normal_recorder.feed(normal["messages"][2])
    _, off_tick = normal_recorder.feed(
        {"t": "tf", "e": "NSE", "tk": "9900002", "ft": "1790200083", "lp": "5000.03"}
    )
    assert not off_tick.state_valid
    assert off_tick.invalid_reason == REASON_OUT_OF_TICK


def test_out_of_tick_price_is_flagged(normal_recorder, normal):
    normal_recorder.feed(normal["messages"][1])
    _, off_tick = normal_recorder.feed(
        {"t": "tf", "e": "NSE", "tk": "9900001", "ft": "1790200082", "lp": "1000.07"}
    )
    assert not off_tick.state_valid
    assert off_tick.invalid_reason == REASON_OUT_OF_TICK


def test_no_tick_size_means_unverifiable_not_invalid(tmp_path, normal):
    """Without a master row the tick rule cannot be checked, and an unverifiable
    rule must not manufacture an invalid quote."""
    writer = RawEventWriter(tmp_path / "raw", session_id="synthetic")
    recorder = Recorder(writer=writer, book=QuoteStateBook(), session_id="synthetic",
                        clock=FixedClock(), data_origin="synthetic")
    recorder.start()
    _, state = recorder.feed(normal["messages"][1])
    assert state.state_valid


# --- 7b: duplicates and timestamp regressions ---------------------------------
def test_duplicate_and_regressed_updates_are_flagged(normal_recorder, normal):
    messages = normal["messages"]
    normal_recorder.feed(messages[1])
    normal_recorder.feed(messages[8])  # feed time now at 1790200060
    regression = normal["expected"]["timestamp_regression_message_index"]
    _, regressed = normal_recorder.feed(messages[regression])
    assert not regressed.state_valid
    assert REASON_TIMESTAMP_REGRESSION in regressed.invalid_reasons

    normal_recorder.feed(snapshot_a("1790200400"))  # repair with a fresh snapshot
    duplicate = normal["expected"]["duplicate_message_index"]
    _, duplicated = normal_recorder.feed(messages[duplicate])
    assert not duplicated.state_valid
    assert REASON_DUPLICATE in duplicated.invalid_reasons


# --- 7c: staleness -------------------------------------------------------------
def test_stale_state_is_reported_not_accepted(normal_recorder, normal):
    normal_recorder.feed(normal["messages"][1])
    book = normal_recorder.book
    as_of = utc_now() + timedelta(minutes=5)

    stale = stale_states(book, as_of, max_age_ms=1_000)
    assert A in stale and stale[A] > 1_000

    state = book.states[A]
    assert state.quote_age_ms(as_of) == stale[A]
    assert state.state_valid, "age is a read-time property; the flagging is the caller's job"
    assert REASON_STALE not in state.invalid_reasons


# --- tf before any snapshot ----------------------------------------------------
def test_update_before_snapshot_is_flagged_not_interpreted(tmp_path):
    writer = RawEventWriter(tmp_path / "raw", session_id="synthetic")
    recorder = Recorder(writer=writer, book=QuoteStateBook(), session_id="synthetic",
                        clock=FixedClock(), data_origin="synthetic")
    recorder.start()
    _, state = recorder.feed(
        {"t": "tf", "e": "NSE", "tk": "9900001", "ft": "1790200000", "lp": "10"}
    )
    assert state is not None
    assert not state.state_valid
    assert state.invalid_reason == REASON_NO_SNAPSHOT
    assert any("tf before tk" in note for note in recorder.book.connection_anomalies)


# --- 6: reconnect ---------------------------------------------------------------
def test_reconnect_requires_new_snapshots(normal_recorder, normal, tmp_path):
    reconnect = load_fixture("session_reconnect")
    first = reconnect["phases"]["generation_1"]
    second = reconnect["phases"]["generation_2"]
    normal_recorder.register_subscribe_frame(first["subscribe_frame"])
    feed_all(normal_recorder, first["messages"])
    assert normal_recorder.book.states[A].state_valid

    normal_recorder.on_disconnect(first["disconnect_reason"])
    normal_recorder.start()
    assert normal_recorder.generation == 2

    # every subscription is restored: the gateway persists nothing
    resubscribe = normal_recorder.subscribe_all_known()
    assert resubscribe["k"] == "NSE|9900001#NSE|9900003"
    assert set(normal_recorder.book.subscriptions) == {A, ("NSE", "9900003")}

    # state is untrusted immediately after reconnect
    stale_generation = normal_recorder.book.states[A]
    assert not stale_generation.state_valid
    assert stale_generation.invalid_reason == REASON_RECONNECT
    assert normal_recorder.book.awaiting_snapshot() == [A, ("NSE", "9900003")]

    # an incremental update in the new generation does not restore trust
    _, early = normal_recorder.feed(second["messages"][0])
    assert not early.state_valid

    _, snapshot_a = normal_recorder.feed(second["messages"][1])
    _, snapshot_b = normal_recorder.feed(second["messages"][2])
    assert snapshot_a.state_valid and snapshot_b.state_valid
    assert normal_recorder.book.awaiting_snapshot() == []

    # fresh snapshots reinitialize the fields, not merge with pre-gap state
    assert snapshot_a.last_price == 102.0
    assert snapshot_a.open_price == 100.0
    assert snapshot_b.last_price == 205.0


def test_second_connect_ack_spelling_is_accepted(normal_recorder, normal):
    reconnect = load_fixture("session_reconnect")
    event, state = normal_recorder.feed(reconnect["phases"]["generation_2"]["connect_ack"])
    assert state is None, "a connect ack carries no instrument"
    assert event.ingestion_status == "non_quote_message"
    assert normal_recorder.counters.connect_acks["ck"] == 1

    event, _ = normal_recorder.feed(normal["connect_ack"])
    assert event.ingestion_status == "non_quote_message"
    assert normal_recorder.counters.connect_acks["ak"] == 1


def test_unknown_message_type_is_flagged_not_dropped(normal_recorder, normal):
    index = normal["expected"]["unknown_type_message_index"]
    event, state = normal_recorder.feed(normal["messages"][index])

    assert event.ingestion_status == "unknown_message_type"
    assert state is None
    assert normal_recorder.counters.unknown_types["zz"] == 1
    assert any("unknown message type" in note for note in normal_recorder.book.connection_anomalies)
    assert normal_recorder.counters.written == normal_recorder.counters.received


def test_feed_time_and_receive_time_stay_separate(normal_recorder, normal):
    """Plan 5.6.9: feed time is stored raw and parsed, receive time is separate."""
    event, state = normal_recorder.feed(normal["messages"][1])

    assert event.feed_timestamp_raw == "1790200000"
    assert event.feed_timestamp_utc is not None
    assert event.received_timestamp_utc is not None
    assert event.feed_timestamp_utc != event.received_timestamp_utc
    assert state.feed_timestamp_utc == event.feed_timestamp_utc
    assert state.received_timestamp_utc == event.received_timestamp_utc


def test_unparseable_feed_time_is_null_not_replaced_by_local_time(normal_recorder, normal):
    event, state = normal_recorder.feed(
        {"t": "tk", "e": "NSE", "tk": "9900001", "ft": "not-a-time", "lp": "10.00"}
    )
    assert event.feed_timestamp_raw == "not-a-time"
    assert event.feed_timestamp_utc is None
    assert state.feed_timestamp_utc is None
    assert state.received_timestamp_utc is not None


