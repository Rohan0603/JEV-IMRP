from __future__ import annotations

from datetime import UTC, datetime

from jev_imrp.features.compute import FeatureEngine
from jev_imrp.ingest.openalgo_market_data import OpenAlgoMarketDataBridge


class FakeWriter:
    provider = "unknown"
    provider_version = "not_declared"
    generation = 0

    def write(self, event) -> None:
        pass


def test_bridge_feeds_openalgo_touchline_into_jev_feature_engine() -> None:
    received = datetime(2026, 1, 2, 10, 0, tzinfo=UTC)
    engine = FeatureEngine()
    bridge = OpenAlgoMarketDataBridge(
        FakeWriter(),
        feature_engine=engine,
        clock=lambda: (received, 123),
    )

    event, state = bridge.feed(
        {
            "type": "market_data",
            "broker": "fyers",
            "exchange": "NSE",
            "symbol": "RELIANCE",
            "data": {
                "exchange": "NSE",
                "symbol": "RELIANCE",
                "timestamp": 1767348000000,
                "ltp": 1422.5,
                "bid_price": 1422.4,
                "ask_price": 1422.6,
                "bid_size": 10,
                "ask_size": 20,
            },
        }
    )

    assert state is not None
    assert event.event_id
    assert bridge.quote_rows == 1
    record = bridge.compute("RELIANCE", received)
    assert record.features["spread_bps"].valid
    assert record.features["touch_size_imbalance"].value == -1 / 3
    assert "event_id=" + event.event_id in record.lineage["quote"]


def test_bridge_keeps_invalid_openalgo_quotes_in_jev_for_fail_closed_features() -> None:
    received = datetime(2026, 1, 2, 10, 0, tzinfo=UTC)
    bridge = OpenAlgoMarketDataBridge(
        FakeWriter(),
        feature_engine=FeatureEngine(),
        clock=lambda: (received, 123),
    )
    bridge.feed(
        {
            "type": "quote",
            "exchange": "NSE",
            "symbol": "RELIANCE",
            "bid": 1422.6,
            "ask": 1422.4,
        }
    )

    record = bridge.compute("RELIANCE", received)
    assert record.features["spread_bps"].valid is False
    assert record.features["spread_bps"].quality == "invalid_quote_state"
