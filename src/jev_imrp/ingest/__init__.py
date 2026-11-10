"""Raw feed capture: provider adapters, quote reconstruction and persistence.

Research-only. Nothing in this package places an order or opens a network
connection by itself: transports are injected by the caller.

The active provider boundary is direct FYERS v3. OpenAlgo stays diagnostic-only
and the archived Shoonya adapter is retained for historical replay provenance.
"""

from __future__ import annotations

from jev_imrp.ingest import legacy_shoonya
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
from jev_imrp.ingest.fyers_recorder import (
    FyersAuthError,
    FyersRecorder,
    FyersRecorderCounters,
    FyersRecorderError,
    FyersRecorderMetrics,
    L1Availability,
    SubscriptionEntry,
    SubscriptionPlan,
    build_auth_frame,
    build_subscribe_frame,
    build_subscription_plan,
    build_universe_subscription,
    hsm_token_from_access_token,
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


def __getattr__(name: str) -> object:
    """Fail-closed lazy loader for archived Shoonya exports.

    Accessing any archived Shoonya name raises :class:`LegacyShoonyaDisabled`
    unless ``JEV_ENABLE_LEGACY_SHOONYA=1`` is set.
    """
    return legacy_shoonya.legacy_export(name)


__all__ = [
    "DEFAULT_STANDARD_LIMITS",
    "RAW_EVENT_SCHEMA",
    "DataHealthReport",
    "DataHealthThresholds",
    "FyersAuthError",
    "FyersHistoryClient",
    "FyersHistoryError",
    "FyersInstrument",
    "FyersOAuthState",
    "FyersPacketDecoder",
    "FyersQuoteBook",
    "FyersQuoteState",
    "FyersRecorder",
    "FyersRecorderCounters",
    "FyersRecorderError",
    "FyersRecorderMetrics",
    "FyersSymbolError",
    "HistoricalDataError",
    "HistoryRequest",
    "L1Availability",
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
    "SubscriptionEntry",
    "SubscriptionPlan",
    "SymbolResolutionError",
    "app_id_hash",
    "authorization_url",
    "build_auth_frame",
    "build_fyers_mapping",
    "build_subscribe_frame",
    "build_subscription_plan",
    "build_universe_subscription",
    "chunk_history_window",
    "decode_frame",
    "drop_incomplete_candles",
    "empty_table",
    "evaluate_events",
    "fetch_eod_chart_data",
    "fetch_tp_series_data",
    "hsm_token_from_access_token",
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
