from __future__ import annotations

import pytest

from jev_imrp.ingest.fyers_symbol_master import (
    FyersInstrument,
    FyersSymbolError,
    build_fyers_mapping,
    to_fyers_symbol,
)


def test_symbol_formatter_is_exact_and_does_not_reuse_legacy_tokens() -> None:
    assert to_fyers_symbol("RELIANCE") == "NSE:RELIANCE-EQ"
    assert to_fyers_symbol("NSE:RELIANCE-EQ") == "NSE:RELIANCE-EQ"

    with pytest.raises(FyersSymbolError, match="exact"):
        to_fyers_symbol("NSE:SBIN")


def test_mapping_requires_unique_active_exact_master_rows() -> None:
    rows = [
        {"symbol": "NSE:RELIANCE-EQ", "status": "ACTIVE", "series": "EQ"},
        {"symbol": "NSE:TCS-EQ", "status": "SUSPENDED", "series": "EQ"},
    ]

    with pytest.raises(FyersSymbolError, match="suspended"):
        build_fyers_mapping(["RELIANCE", "TCS"], rows, master_sha256="a" * 64)


def test_mapping_preserves_frozen_membership_and_master_hash() -> None:
    rows = [{"symbol": "NSE:RELIANCE-EQ", "status": "ACTIVE", "series": "EQ"}]

    mapping = build_fyers_mapping(["RELIANCE"], rows, master_sha256="a" * 64)

    assert mapping == [
        FyersInstrument(
            research_symbol="RELIANCE",
            provider_symbol="NSE:RELIANCE-EQ",
            series="EQ",
            status="ACTIVE",
            master_sha256="a" * 64,
        )
    ]


def test_mapping_resolves_all_twenty_frozen_symbols_in_order() -> None:
    frozen = [
        "RELIANCE",
        "TCS",
        "HDFCBANK",
        "ICICIBANK",
        "INFY",
        "SBIN",
        "BHARTIARTL",
        "ITC",
        "LT",
        "AXISBANK",
        "KOTAKBANK",
        "HINDUNILVR",
        "MARUTI",
        "SUNPHARMA",
        "TVSMOTOR",
        "BAJFINANCE",
        "ADANIENT",
        "ASIANPAINT",
        "ONGC",
        "NTPC",
    ]
    rows = [
        {"symbol": to_fyers_symbol(symbol), "status": "ACTIVE", "series": "EQ"}
        for symbol in reversed(frozen)
    ]

    mapping = build_fyers_mapping(frozen, rows, master_sha256="b" * 64)

    assert len(mapping) == 20
    assert [item.research_symbol for item in mapping] == frozen
    assert [item.provider_symbol for item in mapping] == [
        f"NSE:{symbol}-EQ" for symbol in frozen
    ]
    assert {item.master_sha256 for item in mapping} == {"b" * 64}
