from __future__ import annotations

from datetime import UTC, datetime

from jev_imrp.ingest.fyers_market_data import FyersMarketDataBridge


def test_direct_fyers_bridge_publishes_only_validated_quote_states() -> None:
    bridge = FyersMarketDataBridge(
        writer=_Writer(),
        clock=lambda: (datetime(2026, 1, 2, 10, 0, tzinfo=UTC), 10),
    )

    event, state = bridge.feed(
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

    assert event.message_type == "tk"
    assert state is not None and state.state_valid
    assert bridge.quote_rows == 1
    record = bridge.compute("NSE:RELIANCE-EQ", datetime(2026, 1, 2, 10, 0, tzinfo=UTC))
    assert record.features["spread_bps"].valid


class _Writer:
    def __init__(self) -> None:
        self.events = []
        self.provider = "unknown"
        self.provider_version = "not_declared"
        self.generation = 0

    def write(self, event: object) -> None:
        self.events.append(event)
