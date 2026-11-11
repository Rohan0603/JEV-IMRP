from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from jev_imrp.ingest.data_health import evaluate_events
from jev_imrp.ingest.fyers_socket import (
    FyersPacketError,
    FyersRecorder,
    FyersSocketSession,
    normalize_fyers_message,
    parse_fyers_message,
)


class FakeWriter:
    def __init__(self) -> None:
        self.events = []
        self.generation = 0
        self.provider = "unknown"
        self.provider_version = "not_declared"

    def write(self, event) -> None:
        self.events.append(event)


def test_fyers_symbol_packet_maps_l1_without_inventing_fields() -> None:
    message = {
        "symbol": "NSE:RELIANCE-EQ",
        "ltp": 1422.5,
        "last_traded_time": 1_767_348_000,
        "bid_price": 1422.4,
        "bid_size": 10,
        "ask_price": 1422.6,
        "ask_size": 12,
        "type": "sf",
    }

    normalized = normalize_fyers_message(parse_fyers_message(json.dumps(message)))

    assert normalized["t"] == "sf"
    assert normalized["e"] == "NSE"
    assert normalized["tk"] == "NSE:RELIANCE-EQ"
    assert normalized["bp1"] == 1422.4
    assert normalized["sp1"] == 1422.6
    assert normalized["bq1"] == 10
    assert normalized["sq1"] == 12
    assert normalized["ft"] == 1_767_348_000
    assert "bp2" not in normalized


def test_fyers_depth_packet_preserves_partial_levels() -> None:
    normalized = normalize_fyers_message(
        {
            "symbol": "NSE:RELIANCE-EQ",
            "bid_price1": 1422.4,
            "bid_size1": 10,
            "ask_price1": 1422.6,
            "ask_size1": 12,
            "bid_price2": 1422.3,
            "type": "dp",
        }
    )

    assert normalized["t"] == "dp"
    assert normalized["bp1"] == 1422.4
    assert normalized["bq1"] == 10
    assert normalized["sp1"] == 1422.6
    assert normalized["sq1"] == 12
    assert normalized["bp2"] == 1422.3
    assert "sp2" not in normalized
    assert "ft" not in normalized


def test_fyers_index_packet_is_accepted_without_touchline() -> None:
    normalized = normalize_fyers_message(
        {"symbol": "NSE:NIFTY50-INDEX", "ltp": 22_000.0, "type": "if"}
    )

    assert normalized["t"] == "if"
    assert normalized["e"] == "NSE"
    assert normalized["ti"] == "NSE:NIFTY50-INDEX"
    assert "bp1" not in normalized


def test_fyers_recorder_preserves_packet_provenance_and_rebuilds_state() -> None:
    writer = FakeWriter()
    now = datetime(2026, 1, 2, 10, 0, tzinfo=UTC)
    recorder = FyersRecorder(writer, clock=lambda: (now, 10))

    event, state = recorder.feed(
        {
            "type": "sf",
            "symbol": "NSE:RELIANCE-EQ",
            "ltp": 1422.5,
            "bid_price": 1422.4,
            "bid_size": 10,
            "ask_price": 1422.6,
            "ask_size": 12,
            "last_traded_time": 1_767_348_000,
        }
    )

    assert writer.provider == "fyers"
    assert writer.provider_version == "fyers-data-socket-v3"
    assert event.message_type == "tk"
    assert event.payload["_fyers_type"] == "sf"
    assert state is not None and state.last_price == 1422.5


def test_fyers_socket_session_reconciles_symbols_across_reconnect() -> None:
    session = FyersSocketSession(max_symbols=2, max_reconnects=3)

    assert session.subscribe(["NSE:RELIANCE-EQ", "NSE:TCS-EQ", "NSE:RELIANCE-EQ"]) == {
        "data_type": "SymbolUpdate",
        "symbols": ["NSE:RELIANCE-EQ", "NSE:TCS-EQ"],
    }
    session.disconnect()
    assert session.connect() == {
        "data_type": "SymbolUpdate",
        "symbols": ["NSE:RELIANCE-EQ", "NSE:TCS-EQ"],
    }
    assert session.generation == 1
    assert session.can_retry(3) is False
    assert session.retry_delay_seconds(1) == 0.5
    assert session.retry_delay_seconds(4) == 4.0


def test_fyers_socket_session_rejects_cap_overflow() -> None:
    session = FyersSocketSession(max_symbols=2)

    with pytest.raises(ValueError, match="2 symbols"):
        session.subscribe(["NSE:RELIANCE-EQ", "NSE:TCS-EQ", "NSE:INFY-EQ"])


def test_fyers_missing_source_time_and_touchline_fail_data_health_closed() -> None:
    writer = FakeWriter()
    recorder = FyersRecorder(
        writer,
        clock=lambda: (datetime(2026, 1, 2, 10, 0, tzinfo=UTC), 10),
    )
    recorder.feed({"type": "sf", "symbol": "NSE:RELIANCE-EQ", "ltp": 1422.5})

    report = evaluate_events(writer.events)

    assert report.ok is False
    assert "missing_source_timestamp" in report.blocked_reasons
    assert "missing_touchline" in report.blocked_reasons


@pytest.mark.parametrize("raw", ["not-json", "[]", '{"type":"unknown"}'])
def test_fyers_packet_parser_rejects_invalid_messages(raw: str) -> None:
    with pytest.raises(FyersPacketError):
        parse_fyers_message(raw)
