from __future__ import annotations

from collections.abc import Mapping

from jev_imrp.ingest.openalgo_client import OpenAlgoClient, OpenAlgoConfig
from jev_imrp.ingest.provider_contract import MarketDataProvider


def test_openalgo_client_implements_the_data_only_provider_contract() -> None:
    client = OpenAlgoClient(
        OpenAlgoConfig(host="http://openalgo", api_key="secret", ws_url="ws://openalgo"),
        transport=lambda path, payload: {"status": "success", "data": {}},
    )

    assert isinstance(client, MarketDataProvider)
    assert isinstance(client.quote(symbol="RELIANCE", exchange="NSE"), Mapping)
    assert isinstance(client.depth(symbol="RELIANCE", exchange="NSE"), Mapping)


def test_provider_contract_has_no_transaction_methods() -> None:
    assert not hasattr(MarketDataProvider, "place_order")
    assert not hasattr(MarketDataProvider, "cancel_order")
