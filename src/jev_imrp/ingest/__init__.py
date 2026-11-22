"""Direct FYERS v3 data-only ingestion boundary.

Research-only. Nothing in this package places an order, reads credentials or
opens a network connection by itself: the transport is injected by the caller.
Direct FYERS is the only forward provider boundary. OpenAlgo modules remain
available only for immutable historical qualification and provenance.
"""

from __future__ import annotations

from jev_imrp.ingest.data_health import DataHealthReport, DataHealthThresholds, evaluate_events
from jev_imrp.ingest.feed_state import QuoteState, QuoteStateBook
from jev_imrp.ingest.file_writer import RawEventWriter, read_manifest, verify_manifest
from jev_imrp.ingest.fyers_auth_flow import interactive_reauthenticate
from jev_imrp.ingest.fyers_history import (
    FyersHistoryClient,
    FyersHistoryError,
    HistoryRequest,
    chunk_history_window,
    drop_incomplete_candles,
    normalize_candles,
)
from jev_imrp.ingest.fyers_market_data import FyersMarketDataBridge
from jev_imrp.ingest.fyers_oauth import (
    FyersAuthClient,
    FyersAuthError,
    FyersOAuthState,
    OAuthStateError,
    app_id_hash,
    authorization_url,
    httpx_auth_transport,
    parse_callback_query,
    replace_env_setting,
    token_request,
)
from jev_imrp.ingest.fyers_quotes import FyersQuoteClient, FyersQuoteError, httpx_quote_transport
from jev_imrp.ingest.fyers_socket import (
    FyersPacketError,
    FyersRecorder,
    FyersRecorderCounters,
    FyersSocketSession,
    normalize_fyers_message,
    parse_fyers_message,
)
from jev_imrp.ingest.fyers_symbol_master import (
    FyersInstrument,
    FyersSymbolError,
    build_fyers_mapping,
    to_fyers_symbol,
)
from jev_imrp.ingest.openalgo_client import (
    OpenAlgoClient,
    OpenAlgoConfig,
    OpenAlgoProtocolError,
    parse_websocket_message,
)
from jev_imrp.ingest.openalgo_market_data import OpenAlgoMarketDataBridge
from jev_imrp.ingest.openalgo_recorder import OpenAlgoRecorder, OpenAlgoRecorderCounters
from jev_imrp.ingest.provider_contract import MarketDataProvider
from jev_imrp.ingest.qualification import (
    QualificationReport,
    QualificationThresholds,
    evaluate_qualification,
    fill_eligible,
)
from jev_imrp.ingest.quota import (
    DEFAULT_STANDARD_LIMITS,
    QuotaExceeded,
    QuotaLimits,
    QuotaRemaining,
    QuotaTracker,
)
from jev_imrp.ingest.raw_event import RAW_EVENT_SCHEMA, RawEvent, empty_table, new_event_id

__all__ = [
    "DEFAULT_STANDARD_LIMITS",
    "RAW_EVENT_SCHEMA",
    "DataHealthReport",
    "DataHealthThresholds",
    "FyersAuthClient",
    "FyersAuthError",
    "FyersHistoryClient",
    "FyersHistoryError",
    "FyersInstrument",
    "FyersMarketDataBridge",
    "FyersOAuthState",
    "FyersPacketError",
    "FyersQuoteClient",
    "FyersQuoteError",
    "FyersRecorder",
    "FyersRecorderCounters",
    "FyersSocketSession",
    "FyersSymbolError",
    "HistoryRequest",
    "MarketDataProvider",
    "OAuthStateError",
    "OpenAlgoClient",
    "OpenAlgoConfig",
    "OpenAlgoMarketDataBridge",
    "OpenAlgoProtocolError",
    "OpenAlgoRecorder",
    "OpenAlgoRecorderCounters",
    "QualificationReport",
    "QualificationThresholds",
    "QuotaExceeded",
    "QuotaLimits",
    "QuotaRemaining",
    "QuotaTracker",
    "QuoteState",
    "QuoteStateBook",
    "RawEvent",
    "RawEventWriter",
    "app_id_hash",
    "authorization_url",
    "build_fyers_mapping",
    "chunk_history_window",
    "drop_incomplete_candles",
    "empty_table",
    "evaluate_events",
    "evaluate_qualification",
    "fill_eligible",
    "httpx_auth_transport",
    "httpx_quote_transport",
    "interactive_reauthenticate",
    "new_event_id",
    "normalize_candles",
    "normalize_fyers_message",
    "parse_callback_query",
    "parse_fyers_message",
    "parse_websocket_message",
    "read_manifest",
    "replace_env_setting",
    "to_fyers_symbol",
    "token_request",
    "verify_manifest",
]
