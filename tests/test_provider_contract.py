from __future__ import annotations

import pytest

from jev_imrp.ingest.openalgo_client import OpenAlgoClient, OpenAlgoConfig
from jev_imrp.ingest.provider_contract import MarketDataProvider


def test_provider_contract_has_no_transaction_methods() -> None:
    assert not hasattr(MarketDataProvider, "place_order")
    assert not hasattr(MarketDataProvider, "cancel_order")


def test_openalgo_client_is_the_active_fyers_backed_provider() -> None:
    client = OpenAlgoClient(
        OpenAlgoConfig(
            host="http://openalgo",
            api_key="secret",
            ws_url="ws://openalgo",
            upstream_provider="fyers",
        ),
        transport=lambda path, payload: {"status": "success", "data": {}},
    )

    assert isinstance(client, MarketDataProvider)
    assert client.config.upstream_provider == "fyers"


def test_openalgo_provider_rejects_silent_provider_substitution() -> None:
    with pytest.raises(ValueError, match="pinned to upstream provider fyers"):
        OpenAlgoConfig(
            host="http://openalgo",
            api_key="secret",
            ws_url="ws://openalgo",
            upstream_provider="other",
        )
