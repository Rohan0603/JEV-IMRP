"""Sprint 1 raw feed capture: recorder, quote-state reconstruction, persistence.

Research-only. Nothing in this package places an order, reads credentials or
opens a network connection by itself: the transport is injected by the caller
(see `shoonya_recorder.Recorder.feed`).
"""

from __future__ import annotations

from jev_imrp.ingest.feed_state import QuoteState, QuoteStateBook
from jev_imrp.ingest.file_writer import RawEventWriter, read_manifest, verify_manifest
from jev_imrp.ingest.historical_data_client import (
    HistoricalDataError,
    fetch_eod_chart_data,
    fetch_tp_series_data,
)
from jev_imrp.ingest.raw_event import (
    RAW_EVENT_SCHEMA,
    RawEvent,
    empty_table,
    new_event_id,
)
from jev_imrp.ingest.shoonya_recorder import Recorder
from jev_imrp.ingest.symbol_resolver import (
    ResolvedToken,
    SymbolResolutionError,
    resolve_symbol,
    resolve_symbols_batch,
    resolve_via_master,
    resolve_via_searchscrip,
)

__all__ = [
    "RAW_EVENT_SCHEMA",
    "HistoricalDataError",
    "QuoteState",
    "QuoteStateBook",
    "RawEvent",
    "RawEventWriter",
    "Recorder",
    "ResolvedToken",
    "SymbolResolutionError",
    "empty_table",
    "fetch_eod_chart_data",
    "fetch_tp_series_data",
    "new_event_id",
    "read_manifest",
    "resolve_symbol",
    "resolve_symbols_batch",
    "resolve_via_master",
    "resolve_via_searchscrip",
    "verify_manifest",
]
