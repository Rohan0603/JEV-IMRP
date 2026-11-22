"""Direct FYERS market-data bridge into the shared causal feature pipeline.

This is a data-only integration boundary. It records raw FYERS packets through
the canonical recorder, publishes only validated quote states to the shared
``FeatureEngine``, and exposes no trading or account operations.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

import pandas as pd  # type: ignore[import-untyped]

from jev_imrp.features.compute import FeatureEngine, FeatureRecord
from jev_imrp.features.definitions import QUOTE_COLUMNS
from jev_imrp.ingest.feed_state import QuoteState
from jev_imrp.ingest.fyers_socket import FyersRecorder
from jev_imrp.ingest.raw_event import RawEvent


class FyersMarketDataBridge:
    """Feed direct FYERS packets into JEV's validated quote/feature path."""

    def __init__(
        self,
        writer: Any,
        *,
        feature_engine: FeatureEngine | None = None,
        recorder: FyersRecorder | None = None,
        clock: Callable[[], tuple[datetime, int]] | None = None,
    ) -> None:
        self.feature_engine = feature_engine or FeatureEngine()
        self.recorder = recorder or FyersRecorder(writer, clock=clock)
        self.quote_rows = 0

    def feed(self, message: str | bytes | dict[str, Any]) -> tuple[RawEvent, QuoteState | None]:
        """Record one FYERS packet and publish its validated quote state."""

        event, state = self.recorder.feed(message)
        if state is not None and state.symbol:
            self.feature_engine.observe_quote(pd.DataFrame([self._quote_row(state)]))
            self.quote_rows += 1
        return event, state

    def compute(self, symbol: str, as_of: datetime) -> FeatureRecord:
        """Compute the same causal feature snapshot used by historical replay."""

        return self.feature_engine.compute(symbol, as_of)

    @staticmethod
    def _quote_row(state: QuoteState) -> dict[str, Any]:
        row = state.to_row()
        return {column: row.get(column) for column in QUOTE_COLUMNS} | {
            "last_traded_price": state.last_price,
            "last_traded_volume": state.last_traded_qty,
            "quote_age_ms": state.quote_age_ms(state.received_timestamp_utc),
            "state_valid": state.state_valid,
        }


__all__ = ["FyersMarketDataBridge"]
