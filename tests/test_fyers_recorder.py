"""Offline tests for the FYERS v3 data-socket recorder.

Every frame here is synthetic, built with :mod:`tests.fyers_packets`; no test
opens a socket, reads a credential, or places an order. The recorder is fed
pre-encoded bytes exactly as a transport would hand them over, so the decoder,
the L1 normalization and the replay path are all exercised end to end.
"""

from __future__ import annotations

import base64
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from fyers_packets import (
    DP_TOPIC,
    DP_TOPIC_ID,
    SF_TOPIC,
    SF_TOPIC_ID,
    SOURCE_EPOCH,
    SOURCE_TIME,
    auth_ok,
    control_frame,
    data_frame,
    dp_values,
    frame,
    full_block,
    only,
    sf_values,
    snapshot_block,
)
from jev_imrp.common.config import load_universe
from jev_imrp.ingest.feed_state import replay_raw_events
from jev_imrp.ingest.file_writer import (
    RawEventWriter,
    WriterSettings,
    iter_raw_events,
    read_manifest,
)
from jev_imrp.ingest.fyers_recorder import (
    DEFAULT_DATA_TYPES,
    PROVIDER,
    PROVIDER_VERSION,
    FyersAuthError,
    FyersRecorder,
    FyersRecorderError,
    build_auth_frame,
    build_subscribe_frame,
    build_subscription_plan,
    build_universe_subscription,
    hsm_token_from_access_token,
)
from jev_imrp.ingest.fyers_socket import RESP_AUTH, RESP_SUBSCRIBE, decode_frame
from jev_imrp.ingest.fyers_symbol_master import (
    FyersInstrument,
    FyersSymbolError,
    build_fyers_mapping,
)
from synthetic_feed import FixedClock

HASH = "a" * 64
SYMBOL = "RELIANCE"
TOKEN = "SYNTH2885"


# --- helpers --------------------------------------------------------------------
def make_instrument(
    *, symbol: str = SYMBOL, token: str | None = TOKEN
) -> FyersInstrument:
    return FyersInstrument(
        research_symbol=symbol,
        provider_symbol=f"NSE:{symbol}-EQ",
        series="EQ",
        status="ACTIVE",
        master_sha256=HASH,
        exchange_token=token,
    )


def make_plan(*, data_types: tuple[str, ...] = DEFAULT_DATA_TYPES):
    return build_subscription_plan(
        [make_instrument()],
        tick_sizes={SYMBOL: 0.05},
        data_types=data_types,
        master_sha256=HASH,
    )


def make_recorder(tmp_path: Path, plan=None, **kwargs) -> FyersRecorder:
    kwargs.setdefault("data_origin", "synthetic")
    writer = RawEventWriter(
        tmp_path / "raw",
        session_id="synthetic-session",
        settings=WriterSettings(max_rows_per_file=100),
    )
    recorder = FyersRecorder(
        writer=writer,
        plan=plan or make_plan(),
        session_id="synthetic-session",
        connection_id="conn-1",
        clock=FixedClock(),
        **kwargs,
    )
    recorder.start()
    return recorder


def sf_snapshot(**overrides: int) -> bytes:
    block = snapshot_block(
        SF_TOPIC_ID,
        SF_TOPIC,
        sf_values(**overrides),
        exchange_token=TOKEN,
        symbol=SYMBOL,
    )
    return data_frame(1, [block])


def dp_snapshot(**overrides: int) -> bytes:
    block = snapshot_block(
        DP_TOPIC_ID,
        DP_TOPIC,
        dp_values(**overrides),
        exchange_token=TOKEN,
        symbol=SYMBOL,
    )
    return data_frame(2, [block])


def incremental_ltp(value: int) -> bytes:
    block = full_block(SF_TOPIC_ID, only(ltp=value))
    return data_frame(2, [block])


def jwt(payload: dict) -> str:
    def encode(value: dict) -> str:
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{encode({'alg': 'HS256', 'typ': 'JWT'})}.{encode(payload)}.signature"


FUTURE_EXP = int(datetime(2030, 1, 1, tzinfo=UTC).timestamp())


# --- subscription plan ----------------------------------------------------------
def test_plan_derives_exchange_token_from_master_fytoken():
    rows = [
        {
            "symbol": "NSE:RELIANCE-EQ",
            "status": "ACTIVE",
            "series": "EQ",
            "fyToken": "10100000002885",
        }
    ]
    mapped = build_fyers_mapping([SYMBOL], rows, master_sha256=HASH)
    assert mapped[0].exchange_token == "2885"

    plan = build_subscription_plan(mapped, tick_sizes={SYMBOL: 0.05}, master_sha256=HASH)
    assert plan.topics == ("sf|nse_cm|2885",)
    assert plan.entries[0].exchange == "NSE"
    assert plan.entries[0].tick_size == 0.05
    assert plan.by_topic()["sf|nse_cm|2885"].research_symbol == SYMBOL
    assert plan.tick_sizes() == {("NSE", "2885"): 0.05}
    assert plan.tick_sizes_by_symbol() == {SYMBOL: 0.05}


def test_plan_fails_closed_without_a_token():
    with pytest.raises(FyersSymbolError, match="missing FYERS exchange token"):
        build_subscription_plan([make_instrument(token=None)], master_sha256=HASH)


def test_plan_rejects_unsupported_data_type():
    with pytest.raises(FyersRecorderError, match="unsupported FYERS data type"):
        build_subscription_plan(
            [make_instrument()], data_types=("sf", "quote"), master_sha256=HASH
        )


def test_plan_rejects_duplicate_topics():
    instrument = make_instrument()
    with pytest.raises(FyersSymbolError, match="duplicate FYERS topic"):
        build_subscription_plan([instrument, instrument], master_sha256=HASH)


def _universe_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index, item in enumerate(load_universe()["instruments"]):
        rows.append(
            {
                "symbol": f"NSE:{item['symbol']}-EQ",
                "status": "ACTIVE",
                "series": "EQ",
                "fyToken": f"1010000000{index:04d}",
            }
        )
    return rows


def test_universe_subscription_covers_all_twenty_frozen_symbols():
    plan = build_universe_subscription(_universe_rows(), master_sha256=HASH)
    assert len(plan.entries) == 20
    assert len(plan.topics) == 20
    assert all(topic.startswith("sf|nse_cm|") for topic in plan.topics)
    assert plan.tick_sizes_by_symbol()["TVSMOTOR"] == 0.1
    assert plan.tick_sizes_by_symbol()[SYMBOL] == 0.05


def test_universe_subscription_fails_closed_on_a_missing_symbol():
    with pytest.raises(FyersSymbolError, match="missing FYERS master symbol"):
        build_universe_subscription(_universe_rows()[:-1], master_sha256=HASH)


def test_universe_subscription_fails_closed_on_a_suspended_symbol():
    rows = _universe_rows()
    rows[0] = {**rows[0], "status": "SUSPENDED"}
    with pytest.raises(FyersSymbolError, match="suspended"):
        build_universe_subscription(rows, master_sha256=HASH)


# --- HSM control frames ---------------------------------------------------------
def test_auth_frame_round_trips_through_the_decoder():
    encoded = build_auth_frame("HSM-TOKEN", source="JEV-IMRP")
    parsed = decode_frame(encoded)
    assert parsed.response_type == RESP_AUTH
    assert parsed.response_name == "cn"
    assert parsed.declared_length == len(encoded) - 2
    assert [(f.field_id, f.value) for f in parsed.control_fields] == [
        (1, b"HSM-TOKEN"),
        (2, b"P"),
        (3, b"1"),
        (4, b"JEV-IMRP"),
    ]
    # documented length math: 16 + len(hsm) + len(source)
    assert len(encoded) - 2 == 16 + len("HSM-TOKEN") + len("JEV-IMRP")


def test_subscribe_frame_carries_count_and_topics():
    topics = ("sf|nse_cm|2885", "sf|nse_cm|3045")
    encoded = build_subscribe_frame(topics, channel=11)
    parsed = decode_frame(encoded)
    assert parsed.response_type == RESP_SUBSCRIBE
    assert parsed.response_name == "sub"
    assert parsed.declared_length == len(encoded) - 2
    assert parsed.control_fields[1].value == b"\x0b"

    raw = parsed.control_fields[0].value
    count = int.from_bytes(raw[:2], "big")
    assert count == len(topics)
    offset = 2
    decoded: list[str] = []
    for _ in range(count):
        length = raw[offset]
        decoded.append(raw[offset + 1 : offset + 1 + length].decode("ascii"))
        offset += 1 + length
    assert tuple(decoded) == topics


def test_subscribe_frame_rejects_empty_topics():
    with pytest.raises(FyersRecorderError):
        build_subscribe_frame(())


# --- access token handling ------------------------------------------------------
def test_hsm_token_is_extracted_without_exposing_the_access_token():
    token = jwt({"hsm_key": "hsm-123", "exp": FUTURE_EXP})
    assert hsm_token_from_access_token(token, now=SOURCE_TIME) == "hsm-123"


def test_expired_access_token_is_rejected():
    token = jwt({"hsm_key": "hsm-123", "exp": int(SOURCE_TIME.timestamp()) - 1})
    with pytest.raises(FyersAuthError, match="expired"):
        hsm_token_from_access_token(token, now=SOURCE_TIME)


@pytest.mark.parametrize(
    "token",
    ["", "not-a-jwt", "a.b", jwt({"exp": FUTURE_EXP}), jwt({"hsm_key": "k"})],
)
def test_malformed_or_incomplete_access_tokens_are_rejected(token: str):
    with pytest.raises(FyersAuthError):
        hsm_token_from_access_token(token, now=SOURCE_TIME)


def test_live_mode_requires_a_valid_access_token(tmp_path):
    with pytest.raises(FyersAuthError):
        make_recorder(tmp_path, data_origin="live")  # no token

    writer = RawEventWriter(tmp_path / "raw2", session_id="s")
    with pytest.raises(FyersAuthError, match="expired"):
        FyersRecorder(
            writer=writer,
            plan=make_plan(),
            data_origin="live",
            access_token=jwt({"hsm_key": "k", "exp": 1}),
        )


def test_auth_frame_method_uses_the_token_hsm_key(tmp_path):
    token = jwt({"hsm_key": "hsm-live", "exp": FUTURE_EXP})
    recorder = make_recorder(tmp_path, data_origin="live", access_token=token)
    parsed = decode_frame(recorder.auth_frame(token))
    assert parsed.control_fields[0].value == b"hsm-live"
    assert b"hsm-live" not in recorder.subscribe_frame()


# --- L1 normalization -----------------------------------------------------------
def test_sf_snapshot_normalizes_to_a_complete_l1_quote(tmp_path):
    recorder = make_recorder(tmp_path)
    messages = recorder.feed(sf_snapshot())
    assert len(messages) == 1
    event, state = messages[0].event, messages[0].state

    assert event.message_type == "tk"
    assert event.ingestion_status == "ok"
    assert event.exchange == "NSE"
    assert event.token == TOKEN
    assert event.symbol_as_received == SYMBOL
    assert event.feed_timestamp_raw == SOURCE_EPOCH
    assert event.feed_timestamp_utc == SOURCE_TIME

    payload = event.payload
    assert payload["lp"] == 2500.0
    assert payload["bp1"] == 2499.5
    assert payload["sp1"] == 2500.5
    assert payload["bq1"] == 10
    assert payload["sq1"] == 12
    assert payload["pp"] == 2475.0
    assert payload["o"] == 2480.0
    assert payload["h"] == 2550.0
    assert payload["l"] == 2450.0
    assert payload["ap"] == 2500.0
    assert payload["v"] == 123_456
    assert payload["oi"] == 0

    assert state is not None
    assert state.bid_price_1 == 2499.5
    assert state.ask_price_1 == 2500.5
    assert state.last_price == 2500.0
    assert state.state_valid
    assert recorder.counters.snapshots == 1


def test_depth_level_one_overrides_the_sf_touchline(tmp_path):
    recorder = make_recorder(tmp_path)
    recorder.feed(sf_snapshot())
    recorder.feed(dp_snapshot(bid_price1=249_900, ask_price1=250_100))

    state = recorder.book.states[("NSE", TOKEN)]
    assert state.bid_price_1 == 2499.0
    assert state.ask_price_1 == 2501.0
    assert state.last_price == 2500.0, "sf fields survive the depth merge"
    assert state.state_valid
    assert recorder.counters.snapshots == 2


def test_incremental_merge_emits_a_full_state_not_a_delta(tmp_path):
    recorder = make_recorder(tmp_path)
    recorder.feed(sf_snapshot())
    messages = recorder.feed(incremental_ltp(251_000))

    event, state = messages[0].event, messages[0].state
    assert event.message_type == "tk", "a merged state is recorded as a full snapshot"
    assert event.payload["lp"] == 2510.0
    assert event.payload["bp1"] == 2499.5, "untouched fields are carried forward"
    assert state is not None and state.last_price == 2510.0


def test_no_snapshot_before_first_frame_means_no_trusted_state(tmp_path):
    recorder = make_recorder(tmp_path)
    assert recorder.metrics().l1_availability == 0.0
    assert recorder.awaiting_snapshot() == [SF_TOPIC]


# --- reconnect ------------------------------------------------------------------
def test_reconnect_requires_a_fresh_snapshot_and_resets_the_decoder(tmp_path):
    recorder = make_recorder(tmp_path)
    recorder.feed(sf_snapshot())
    assert recorder.book.states[("NSE", TOKEN)].state_valid

    topics = recorder.reconnect("test drop")
    assert topics == recorder.plan.topics
    assert recorder.generation == 2
    assert recorder.book.states[("NSE", TOKEN)].invalid_reasons
    assert recorder.awaiting_snapshot() == [SF_TOPIC]

    # The topic-id registry is discarded, so a pre-snapshot incremental frame is
    # a parse error, never a correlation against a stale id.
    stale = recorder.feed(incremental_ltp(252_000))
    assert stale[0].event.message_type == "error"
    assert recorder.counters.parse_errors == 1
    assert not recorder.book.states[("NSE", TOKEN)].state_valid

    recorder.feed(sf_snapshot(exch_feed_time=SOURCE_EPOCH + 60))
    assert recorder.book.states[("NSE", TOKEN)].state_valid
    assert recorder.awaiting_snapshot() == []


# --- control frames and parse errors --------------------------------------------
def test_control_frames_are_recorded_without_echoing_values(tmp_path):
    recorder = make_recorder(tmp_path)
    secret = control_frame(RESP_AUTH, ((1, b"secret-hsm-token"),))
    (message,) = recorder.feed(secret)

    assert message.event.message_type == "cn"
    assert message.event.ingestion_status == "non_quote_message"
    assert message.state is None
    payload = message.event.payload
    assert payload["_fyers_field_ids"] == [1]
    assert payload["_fyers_field_lengths"] == [len(b"secret-hsm-token")]
    assert "secret-hsm-token" not in json.dumps(payload)


def test_acknowledgement_status_is_kept(tmp_path):
    recorder = make_recorder(tmp_path)
    (message,) = recorder.feed(auth_ok())
    assert message.event.payload["_fyers_status"] == "K"
    assert message.event.ingestion_status == "non_quote_message"


def test_unknown_response_type_is_flagged_not_dropped(tmp_path):
    recorder = make_recorder(tmp_path)
    (message,) = recorder.feed(frame(99, b"\x00"))
    assert message.event.message_type == "unknown:99"
    assert message.event.ingestion_status == "unknown_message_type"
    assert recorder.counters.unknown_types["unknown:99"] == 1


def test_unparseable_frame_is_recorded_as_parse_error(tmp_path):
    recorder = make_recorder(tmp_path)
    (message,) = recorder.feed(b"\x00\x05abc")
    assert message.event.message_type == "error"
    assert message.event.ingestion_status == "parse_error"
    assert message.event.payload["_parse_error"]
    assert recorder.counters.parse_errors == 1


# --- metrics --------------------------------------------------------------------
def test_packet_rate_and_l1_availability_are_reported(tmp_path):
    recorder = make_recorder(tmp_path)
    recorder.feed(sf_snapshot())
    recorder.feed(dp_snapshot())

    metrics = recorder.metrics()
    assert metrics.packet_rate.blocks == 2
    assert metrics.packet_rate.frames == 2
    assert metrics.packet_rate.per_topic_type["sf"] == 1
    assert metrics.packet_rate.per_topic_type["dp"] == 1
    assert metrics.l1_total == 1
    assert metrics.l1_complete == 1
    assert metrics.l1_availability == 1.0
    assert metrics.l1[0].source_time
    assert metrics.l1[0].state_valid


# --- replay parity and persisted provenance -------------------------------------
def test_recorded_events_replay_to_the_online_state(tmp_path):
    plan = make_plan()
    recorder = make_recorder(tmp_path, plan=plan)
    recorder.feed(sf_snapshot())
    recorder.feed(incremental_ltp(251_000))
    recorder.close()

    rows = list(iter_raw_events(tmp_path / "raw"))
    assert len(rows) == 2
    assert {row["message_type"] for row in rows} == {"tk"}
    _, replayed = replay_raw_events(rows, plan.tick_sizes())

    online = recorder.book.states[("NSE", TOKEN)]
    final = replayed[-1]
    assert final.to_row() == online.to_row()


def test_manifest_records_fyers_provenance(tmp_path):
    recorder = make_recorder(tmp_path)
    recorder.feed(sf_snapshot())
    recorder.close()

    entries = read_manifest(tmp_path / "raw")
    assert entries
    for entry in entries:
        assert entry.provider == PROVIDER
        assert entry.provider_version == PROVIDER_VERSION
        assert entry.master_hash == HASH
