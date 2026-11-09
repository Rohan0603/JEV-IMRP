"""Raw feed capture: provider adapters, quote reconstruction and persistence.

Research-only. Nothing in this package places an order or opens a network
connection by itself: transports are injected by the caller.

The active provider boundary is direct FYERS v3. OpenAlgo stays diagnostic-only
and the archived Shoonya adapter is retained for historical replay provenance.
"""

from __future__ import annotations

from jev_imrp.ingest.data_health import (
    DataHealthReport,
    DataHealthThresholds,
    evaluate_events,
)
from jev_imrp.ingest.feed_state import QuoteState, QuoteStateBook
from jev_imrp.ingest.file_writer import RawEventWriter, read_manifest, verify_manifest
from jev_imrp.ingest.fyers_history import (
    FyersHistoryClient,
    FyersHistoryError,
    HistoryRequest,
    chunk_history_window,
    drop_incomplete_candles,
    normalize_candles,
)
from jev_imrp.ingest.fyers_oauth import (
    FyersOAuthState,
    OAuthStateError,
    app_id_hash,
    authorization_url,
    token_request,
)
from jev_imrp.ingest.fyers_socket import (
    FyersPacketDecoder,
    FyersQuoteBook,
    FyersQuoteState,
    decode_frame,
    measure_packet_rate,
)
from jev_imrp.ingest.fyers_symbol_master import (
    FyersInstrument,
    FyersSymbolError,
    build_fyers_mapping,
    to_fyers_symbol,
)
from jev_imrp.ingest.historical_data_client import (
    HistoricalDataError,
    fetch_eod_chart_data,
    fetch_tp_series_data,
)
from jev_imrp.ingest.openalgo_client import (
    OpenAlgoClient,
    OpenAlgoConfig,
    OpenAlgoProtocolError,
    parse_websocket_message,
)
from jev_imrp.ingest.openalgo_recorder import OpenAlgoRecorder, OpenAlgoRecorderCounters
from jev_imrp.ingest.provider_contract import MarketDataProvider
from jev_imrp.ingest.quota import (
    DEFAULT_STANDARD_LIMITS,
    QuotaExceeded,
    QuotaLimits,
    QuotaRemaining,
    QuotaTracker,
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
    "DEFAULT_STANDARD_LIMITS",
    "RAW_EVENT_SCHEMA",
    "DataHealthReport",
    "DataHealthThresholds",
    "FyersHistoryClient",
    "FyersHistoryError",
    "FyersInstrument",
    "FyersOAuthState",
    "FyersPacketDecoder",
    "FyersQuoteBook",
    "FyersQuoteState",
    "FyersSymbolError",
    "HistoricalDataError",
    "HistoryRequest",
    "MarketDataProvider",
    "OAuthStateError",
    "OpenAlgoClient",
    "OpenAlgoConfig",
    "OpenAlgoProtocolError",
    "OpenAlgoRecorder",
    "OpenAlgoRecorderCounters",
    "QuotaExceeded",
    "QuotaLimits",
    "QuotaRemaining",
    "QuotaTracker",
    "QuoteState",
    "QuoteStateBook",
    "RawEvent",
    "RawEventWriter",
    "Recorder",
    "ResolvedToken",
    "SymbolResolutionError",
    "app_id_hash",
    "authorization_url",
    "build_fyers_mapping",
    "chunk_history_window",
    "decode_frame",
    "drop_incomplete_candles",
    "empty_table",
    "evaluate_events",
    "fetch_eod_chart_data",
    "fetch_tp_series_data",
    "measure_packet_rate",
    "new_event_id",
    "normalize_candles",
    "parse_websocket_message",
    "read_manifest",
    "resolve_symbol",
    "resolve_symbols_batch",
    "resolve_via_master",
    "resolve_via_searchscrip",
    "to_fyers_symbol",
    "token_request",
    "verify_manifest",
]
