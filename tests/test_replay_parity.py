"""Replay parity: recorded raw events reconstruct the online state exactly.

Plan 5.6.10. The property under test is that replay is *the same code path*
(:meth:`QuoteStateBook.apply`) reading back the persisted rows, so there is no
second parser that could disagree. The comparison covers every reconstructed
field of every state, not a summary.

Parity is only meaningful if the recorded payload and stamps are byte-identical
to what was received, so the round trip itself is asserted too.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from jev_imrp.common.version import PARSER_VERSION, RAW_EVENT_SCHEMA_VERSION
from jev_imrp.ingest.feed_state import QuoteStateBook, replay_raw_events
from jev_imrp.ingest.file_writer import (
    RawEventWriter,
    WriterSettings,
    iter_raw_events,
    read_raw_events,
)
from jev_imrp.ingest.shoonya_recorder import Recorder
from synthetic_feed import FixedClock, load_fixture, tick_sizes

#: Every field that must match between the online parser and the replay.
COMPARED_FIELDS = (
    "exchange",
    "token",
    "symbol",
    "subscription_generation",
    "message_type",
    "feed_timestamp_utc",
    "received_timestamp_utc",
    "last_valid_update_utc",
    "last_price",
    "last_traded_qty",
    "volume",
    "bid_price_1",
    "bid_qty_1",
    "ask_price_1",
    "ask_qty_1",
    "open_price",
    "high_price",
    "low_price",
    "close_price",
    "prev_close_price",
    "average_price",
    "open_interest",
    "fields_present",
    "invalid_reasons",
    "event_id",
    "parser_version",
)


def build_recorder(root: Path, session_id: str = "synthetic-session") -> Recorder:
    """A recorder over the normal fixture's universe, writing into ``root``."""
    fixture = load_fixture("session_normal")
    writer = RawEventWriter(
        root, session_id=session_id, settings=WriterSettings(max_rows_per_file=4)
    )
    recorder = Recorder(
        writer=writer,
        book=QuoteStateBook(tick_sizes=tick_sizes(fixture)),
        session_id=session_id,
        connection_id="conn-1",
        clock=FixedClock(),
        data_origin="synthetic",
    )
    recorder.start()
    return recorder


def assert_same_state(online, replayed) -> None:
    for name in COMPARED_FIELDS:
        assert getattr(replayed, name) == getattr(online, name), f"replay differs on {name}"


def test_replay_reconstructs_identical_final_state(tmp_path):
    fixture = load_fixture("session_normal")
    ticks = tick_sizes(fixture)
    root = tmp_path / "raw"
    recorder = build_recorder(root)
    recorder.run(fixture["messages"])
    recorder.close()

    rows = list(iter_raw_events(root))
    assert len(rows) == len(fixture["messages"])
    _, replayed = replay_raw_events(rows, ticks)

    assert replayed, "the fixture must produce quote states"
    assert set(state.key for state in replayed) == set(recorder.book.states)
    for key, online_state in recorder.book.states.items():
        assert_same_state(online_state, {s.key: s for s in replayed}[key])


def test_replay_matches_online_parser_step_by_step(tmp_path):
    """After every message, the online state and the replayed state agree."""
    fixture = load_fixture("session_normal")
    ticks = tick_sizes(fixture)
    root = tmp_path / "raw"
    online = build_recorder(root)

    online_states = []
    for message in fixture["messages"]:
        _, state = online.feed(message)
        if state is not None:
            online_states.append(state)
    online.close()

    rows = list(iter_raw_events(root))
    quote_rows = [row for row in rows if row["message_type"] in ("tk", "tf")]
    for index in range(1, len(rows) + 1):
        _, replayed = replay_raw_events(rows[:index], ticks)
        # exactly one state per quote message seen so far, no more and no fewer
        assert len(replayed) == sum(
            1 for row in rows[:index] if row["message_type"] in ("tk", "tf")
        )
        for online_state, replayed_state in zip(
            online_states[: len(replayed)], replayed, strict=True
        ):
            assert_same_state(online_state, replayed_state)
    assert len(quote_rows) == len(online_states) == len(replayed)
    for online_state, replayed_state in zip(online_states, replayed, strict=True):
        assert_same_state(online_state, replayed_state)


def test_event_ids_and_timestamps_are_reproducible(tmp_path):
    fixture = load_fixture("session_normal")
    root = tmp_path / "raw"
    recorder = build_recorder(root)
    recorder.run(fixture["messages"])
    recorder.close()

    rows = list(iter_raw_events(root))
    assert len({row["event_id"] for row in rows}) == len(rows)
    for row in rows:
        assert row["session_id"] == "synthetic-session"
        assert row["subscription_generation"] == 1
        assert row["schema_version"] == RAW_EVENT_SCHEMA_VERSION
        assert row["parser_version"] == PARSER_VERSION
        assert row["data_origin"] == "synthetic"


def test_raw_event_round_trip_is_lossless(tmp_path):
    fixture = load_fixture("session_normal")
    root = tmp_path / "raw"
    recorder = build_recorder(root)
    recorder.run(fixture["messages"])
    recorder.close()

    rows = read_raw_events(root).to_pylist()
    for sent, row in zip(fixture["messages"], rows, strict=True):
        if sent.get("t") in ("tk", "tf"):
            assert row["exchange"] == sent["e"]
            assert row["token"] == sent["tk"]
            assert row["symbol_as_received"] == sent.get("ti")
            assert row["feed_timestamp_raw"] == sent["ft"]
            assert row["message_type"] == sent["t"]
            # exact payload, not a normalized subset
            assert json.loads(row["raw_payload_json"]) == sent


def test_replay_preserves_reconnect_generations(tmp_path):
    reconnect = load_fixture("session_reconnect")
    ticks = tick_sizes(load_fixture("session_normal"))
    root = tmp_path / "raw"
    recorder = build_recorder(root)
    first = reconnect["phases"]["generation_1"]
    second = reconnect["phases"]["generation_2"]
    recorder.register_subscribe_frame(first["subscribe_frame"])
    recorder.run(first["messages"])
    recorder.on_disconnect(first["disconnect_reason"])
    recorder.start()
    recorder.register_subscribe_frame(second["subscribe_frame"])
    recorder.run(second["messages"])
    recorder.close()

    online_final = dict(recorder.book.states)
    _, replayed = replay_raw_events(list(iter_raw_events(root)), ticks)
    replayed_final = {state.key: state for state in replayed}

    assert set(online_final) == set(replayed_final)
    for key, online_state in online_final.items():
        assert_same_state(online_state, replayed_final[key])
    assert replayed_final[("NSE", "9900001")].subscription_generation == 2
    assert any(state.subscription_generation == 1 for state in replayed)


def test_replay_of_a_partial_capture_matches_online_state(tmp_path):
    """Only finalized batches are replayable, and replaying them is exact."""
    fixture = load_fixture("session_normal")
    ticks = tick_sizes(fixture)
    root = tmp_path / "raw"
    recorder = build_recorder(root)
    recorder.run(fixture["messages"][:7])
    recorder.close()

    _, replayed = replay_raw_events(list(iter_raw_events(root)), ticks)
    assert_same_state(replayed[-1], recorder.book.states[("NSE", "9900001")])


def test_replay_is_repeatable(tmp_path):
    fixture = load_fixture("session_normal")
    ticks = tick_sizes(fixture)
    root = tmp_path / "raw"
    recorder = build_recorder(root)
    recorder.run(fixture["messages"])
    recorder.close()
    rows = list(iter_raw_events(root))

    first = replay_raw_events(rows, ticks)[1]
    second = replay_raw_events(rows, ticks)[1]
    for a, b in zip(first, second, strict=True):
        assert_same_state(a, b)


def test_replay_without_master_tick_sizes_is_still_deterministic(tmp_path):
    """Tick checks need the instrument master; replay stays deterministic without it."""
    fixture = load_fixture("session_normal")
    root = tmp_path / "raw"
    recorder = build_recorder(root)
    recorder.run(fixture["messages"])
    recorder.close()
    rows = list(iter_raw_events(root))

    with_master = replay_raw_events(rows, tick_sizes(fixture))[1]
    without_master = replay_raw_events(rows, {})[1]
    assert len(with_master) == len(without_master)
    for a, b in zip(with_master, without_master, strict=True):
        assert a.last_price == b.last_price
        assert a.bid_price_1 == b.bid_price_1
        assert a.ask_qty_1 == b.ask_qty_1


