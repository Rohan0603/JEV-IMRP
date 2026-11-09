"""Offline OpenAlgo-to-replay recorder tests."""

from __future__ import annotations

from datetime import UTC, datetime

from jev_imrp.ingest.file_writer import RawEventWriter, read_manifest
from jev_imrp.ingest.openalgo_recorder import OpenAlgoRecorder


class FakeWriter:
    def __init__(self) -> None:
        self.events = []
        self.generation = 0

    def write(self, event) -> None:
        self.events.append(event)


def test_openalgo_quotes_use_the_existing_append_only_state_path() -> None:
    writer = FakeWriter()
    now = datetime(2026, 1, 2, 10, 0, tzinfo=UTC)
    recorder = OpenAlgoRecorder(writer, clock=lambda: (now, 10))

    first_event, first_state = recorder.feed(
        {
            "type": "quote",
            "exchange": "NSE",
            "symbol": "RELIANCE",
            "ltp": 1422.5,
            "bid": 1422.4,
            "ask": 1422.6,
            "timestamp": 1767348000,
        }
    )
    second_event, second_state = recorder.feed(
        {
            "type": "quote",
            "exchange": "NSE",
            "symbol": "RELIANCE",
            "ltp": 1423.0,
            "timestamp": 1767348060,
        }
    )

    assert first_event.message_type == "tk"
    assert second_event.message_type == "tf"
    assert first_event.payload["_openalgo_payload"]["ltp"] == 1422.5
    assert first_state is not None and first_state.last_price == 1422.5
    assert second_state is not None and second_state.last_price == 1423.0
    assert len(writer.events) == 2


def test_openalgo_control_messages_are_recorded_without_creating_quote_state() -> None:
    writer = FakeWriter()
    recorder = OpenAlgoRecorder(writer)

    event, state = recorder.feed({"type": "auth", "status": "success"})

    assert event.message_type == "auth"
    assert event.ingestion_status == "non_quote_message"
    assert state is None
    assert recorder.counters.received == 1


def test_openalgo_frames_are_data_only_and_deterministic() -> None:
    assert OpenAlgoRecorder.authenticate_frame("secret") == {
        "action": "authenticate",
        "api_key": "secret",
    }
    assert OpenAlgoRecorder.subscribe_frame("NSE", "RELIANCE", mode=2) == {
        "action": "subscribe",
        "symbols": [{"symbol": "RELIANCE", "exchange": "NSE"}],
        "mode": "Quote",
    }


def test_openalgo_market_data_frame_unwraps_nested_depth() -> None:
    """Live gateway frames (verified 2026-11-09) nest quote data and depth."""
    writer = FakeWriter()
    now = datetime(2026, 11, 9, 15, 15, tzinfo=UTC)
    recorder = OpenAlgoRecorder(writer, clock=lambda: (now, 10))

    event, state = recorder.feed(
        {
            "type": "market_data",
            "mode": 3,
            "exchange": "NSE",
            "symbol": "RELIANCE",
            "data": {
                "symbol": "RELIANCE",
                "exchange": "NSE",
                "ltp": 1324.4,
                "ltt": 1794237354448,
                "volume": 19734153,
                "depth": {
                    "buy": [{"price": 1324.3, "quantity": 100}],
                    "sell": [{"price": 1324.4, "quantity": 60779}],
                },
            },
        }
    )

    assert event.message_type == "tk"
    assert event.payload["lp"] == 1324.4
    assert event.payload["ft"] == 1794237354448
    assert event.payload["bp1"] == 1324.3
    assert event.payload["sp1"] == 1324.4
    assert event.payload["sq1"] == 60779
    assert state is not None and state.last_price == 1324.4


def test_openalgo_recorder_stamps_provider_lineage(tmp_path) -> None:
    writer = RawEventWriter(tmp_path / "raw", session_id="openalgo-session")
    recorder = OpenAlgoRecorder(writer, clock=lambda: (datetime.now(UTC), 10))

    recorder.feed(
        {
            "type": "quote",
            "exchange": "NSE",
            "symbol": "RELIANCE",
            "ltp": 1422.5,
        }
    )
    writer.finalize()

    assert read_manifest(tmp_path / "raw")[0].provider == "openalgo"
