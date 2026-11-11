from __future__ import annotations

from datetime import UTC, datetime

from jev_imrp.ingest.openalgo_recorder import OpenAlgoRecorder


class FakeWriter:
    provider = "unknown"
    provider_version = "not_declared"
    generation = 0

    def __init__(self) -> None:
        self.events = []

    def write(self, event) -> None:
        self.events.append(event)


def test_recorder_preserves_fyers_upstream_lineage() -> None:
    writer = FakeWriter()
    recorder = OpenAlgoRecorder(
        writer,
        clock=lambda: (datetime(2026, 1, 2, 10, 0, tzinfo=UTC), 1),
    )
    event, state = recorder.feed(
        {
            "type": "quote",
            "exchange": "NSE",
            "symbol": "RELIANCE",
            "ltp": 1422.5,
            "bid": 1422.4,
            "ask": 1422.6,
        }
    )

    assert writer.provider == "openalgo:fyers"
    assert writer.provider_version == "openalgo-data-api-v1"
    assert event.payload["_openalgo_type"] == "quote"
    assert state is not None and state.last_price == 1422.5


def test_recorder_frames_never_include_order_actions() -> None:
    assert OpenAlgoRecorder.subscribe_frame("NSE", "RELIANCE", mode=3) == {
        "action": "subscribe",
        "mode": "Depth",
        "symbols": [{"exchange": "NSE", "symbol": "RELIANCE"}],
    }


def test_recorder_redacts_credentials_from_control_frames() -> None:
    writer = FakeWriter()
    recorder = OpenAlgoRecorder(writer)
    event, state = recorder.feed(
        {
            "type": "auth",
            "status": "success",
            "api_key": "secret-key",
            "nested": {"access_token": "access-secret"},
        }
    )

    assert state is None
    assert event.payload["_openalgo_payload"] == {
        "type": "auth",
        "status": "success",
        "api_key": "***REDACTED***",
        "nested": {"access_token": "***REDACTED***"},
    }


def test_recorder_can_batch_quote_and_depth_subscriptions() -> None:
    assert OpenAlgoRecorder.subscribe_batch_frame("NSE", ["RELIANCE", "TCS"], mode=3) == {
        "action": "subscribe",
        "mode": "Depth",
        "symbols": [
            {"exchange": "NSE", "symbol": "RELIANCE"},
            {"exchange": "NSE", "symbol": "TCS"},
        ],
    }


def test_recorder_normalizes_current_market_data_wrapper_and_depth() -> None:
    writer = FakeWriter()
    recorder = OpenAlgoRecorder(writer)
    event, state = recorder.feed(
        {
            "type": "market_data",
            "broker": "fyers",
            "exchange": "NSE",
            "symbol": "RELIANCE",
            "data": {
                "exchange": "NSE",
                "symbol": "RELIANCE",
                "timestamp": 1794386592453,
                "ltp": 1337.4,
                "depth": {
                    "buy": [{"price": 1336.9, "quantity": 17}],
                    "sell": [{"price": 1337.3, "quantity": 835}],
                },
            },
        }
    )

    assert event.payload["bp1"] == 1336.9
    assert event.payload["sp1"] == 1337.3
    assert event.feed_timestamp_utc is not None
    assert state is not None and state.last_price == 1337.4


def test_recorder_normalizes_openalgo_107_fyers_touchline_names() -> None:
    writer = FakeWriter()
    recorder = OpenAlgoRecorder(writer)
    event, state = recorder.feed(
        {
            "type": "market_data",
            "broker": "fyers",
            "mode": 3,
            "exchange": "NSE",
            "symbol": "RELIANCE",
            "data": {
                "data_type": "Depth",
                "exchange": "NSE",
                "symbol": "RELIANCE",
                "timestamp": 1794392041190,
                "bid_price": 1336.9,
                "ask_price": 1337.3,
                "bid_size": 17,
                "ask_size": 835,
                "depth": {"buy": [], "sell": []},
            },
        }
    )

    assert event.payload["bp1"] == 1336.9
    assert event.payload["sp1"] == 1337.3
    assert event.payload["bq1"] == 17
    assert event.payload["sq1"] == 835
    assert state is not None and state.ask_price_1 == 1337.3
