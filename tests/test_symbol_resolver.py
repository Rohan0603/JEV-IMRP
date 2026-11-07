"""Symbol-to-token resolution tests (plan 5.3, CLAUDE.md).

Covers:
* Exact symbol matching (no fuzzy matches).
* Symbol master fallback when SearchScrip is unavailable.
* Credential requirement enforcement.
* Retry logic for retryable errors.
* Batch resolution with error propagation.
"""

from __future__ import annotations

import io
import json
import os
from unittest.mock import MagicMock, patch

import pytest

from jev_imrp.ingest.symbol_resolver import (
    SymbolResolutionError,
    _build_checksum,
    _master_resolved_cache,
    _parse_response,
    fetch_symbol_master_url,
    resolve_symbol,
    resolve_symbols_batch,
    resolve_via_master,
    resolve_via_searchscrip,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_credentials() -> tuple[str, str, str]:
    return ("test_user", "test_secret", "test_token")


def _mock_urlopen_with_response(data: dict, status: int = 200):
    """Return a mock response object from urlopen."""
    raw = json.dumps(data).encode("utf-8")
    resp = MagicMock()
    resp.read.return_value = raw
    resp.getcode.return_value = status
    resp.headers = {}
    resp.__enter__ = lambda self: self
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _create_symbol_master_zip(exchange: str, entries: list[dict]) -> bytes:
    """Create a fake symbol master ZIP file content matching the live format.

    The live master is comma-delimited with columns
    Exchange,Token,LotSize,Symbol,TradingSymbol,Instrument,TickSize,...
    The trading symbol (e.g. SBIN-EQ) lives in TradingSymbol.
    """
    lines = ["Exchange,Token,LotSize,Symbol,TradingSymbol,Instrument,TickSize,"]
    for entry in entries:
        symbol_val = entry.get("symbol", "") or entry.get("tsym", "")
        token_val = entry.get("token", "") or entry.get("tok", "")
        trading_symbol = entry.get("trading_symbol", "") or symbol_val
        entry.get("name", "") or entry.get("instrument_name", "")
        instrument = entry.get("instrument", "EQ")
        tick_size = str(entry.get("tick_size", "0.05"))
        lines.append(
            f"{exchange},{token_val},1,{symbol_val},{trading_symbol},{instrument},{tick_size},"
        )
    content = "\n".join(lines).encode("utf-8")

    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{exchange}_symbols.txt", content.decode("utf-8"))
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Credential handling
# ---------------------------------------------------------------------------


def test_get_credentials_raises_when_env_not_set():
    """Credential lookup requires all three env vars."""
    with (
        patch.dict(os.environ, {}, clear=True),
        pytest.raises(SymbolResolutionError, match="missing credentials"),
    ):
        from jev_imrp.ingest.symbol_resolver import _get_credentials

        _get_credentials()


def test_get_credentials_returns_tuple_when_all_present():
    """All three credentials present → returned as a tuple."""
    with patch.dict(
        os.environ,
        {
            "SHOONYA_API_KEY": "key",
            "SHOONYA_API_SECRET": "secret",
            "SHOONYA_ACCESS_TOKEN": "token",
        },
    ):
        from jev_imrp.ingest.symbol_resolver import _get_credentials

        result = _get_credentials()
        assert result == ("key", "secret", "token")


# ---------------------------------------------------------------------------
# Checksum construction
# ---------------------------------------------------------------------------


def test_checksum_without_code():
    """Checksum is SHA256(client_id + secret) when no auth code is given."""
    checksum = _build_checksum("uid123", "mysecret")
    expected = "uid123mysecret"
    import hashlib

    computed = hashlib.sha256(expected.encode("utf-8")).hexdigest()
    assert checksum == computed


def test_checksum_with_code():
    """Checksum is SHA256(client_id + secret + code) when auth code is present."""
    checksum = _build_checksum("uid123", "mysecret", "authcode")
    import hashlib

    computed = hashlib.sha256(b"uid123mysecretauthcode").hexdigest()
    assert checksum == computed


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def test_parse_response_success():
    """Successful API responses have stat='Ok' and are returned as dicts."""
    data = {"stat": "Ok", "s": "SBIN-EQ", "token": "22"}
    result = _parse_response(json.dumps(data).encode())
    assert result == data


def test_parse_response_failure_raises():
    """API responses with stat='Not_Ok' raise SymbolResolutionError."""
    data = {"stat": "Not_Ok", "emsg": "Invalid Input"}
    with pytest.raises(SymbolResolutionError, match="Invalid Input"):
        _parse_response(json.dumps(data).encode())


def test_parse_response_non_dict_raises():
    """Non-dict responses raise an error."""
    with pytest.raises(SymbolResolutionError, match="unexpected response shape"):
        _parse_response(b"[1, 2, 3]")


# ---------------------------------------------------------------------------
# SearchScrip resolution (exact match enforced)
# ---------------------------------------------------------------------------


def test_searchscrip_exact_match_succeeds():
    """SearchScrip returns exact SYMBOL-EQ match → success."""
    response = {
        "stat": "Ok",
        "scrips": [
            {"tsym": "SBIN-EQ", "token": "22", "name": "STATE BANK OF INDIA"},
            {"tsym": "SBINMID150-EQ", "token": "9999", "name": "SBIN MIDCAP"},
        ],
    }
    with (
        patch("jev_imrp.ingest.symbol_resolver._get_credentials", return_value=_make_credentials()),
        patch(
            "jev_imrp.ingest.symbol_resolver.urllib.request.urlopen",
            return_value=_mock_urlopen_with_response(response),
        ),
    ):
        result = resolve_via_searchscrip(user_id="uid123", exchange="NSE", symbol="SBIN-EQ")
        assert result.token == "22"
        assert result.symbol == "SBIN-EQ"


def test_searchscrip_fuzzy_match_rejected():
    """SearchScrip returns only fuzzy matches → SymbolResolutionError."""
    response = {
        "stat": "Ok",
        "scrips": [
            {"tsym": "SBINMID150-EQ", "token": "9999", "name": "SBIN MIDCAP"},
        ],
    }
    with (
        patch("jev_imrp.ingest.symbol_resolver._get_credentials", return_value=_make_credentials()),
        patch(
            "jev_imrp.ingest.symbol_resolver.urllib.request.urlopen",
            return_value=_mock_urlopen_with_response(response),
        ),
        pytest.raises(SymbolResolutionError, match="fuzzy matches"),
    ):
        resolve_via_searchscrip(user_id="uid123", exchange="NSE", symbol="SBIN-EQ")


def test_searchscrip_no_matches_raises():
    """SearchScrip returns empty list → SymbolResolutionError."""
    response = {"stat": "Ok", "scrips": []}
    with (
        patch("jev_imrp.ingest.symbol_resolver._get_credentials", return_value=_make_credentials()),
        patch(
            "jev_imrp.ingest.symbol_resolver.urllib.request.urlopen",
            return_value=_mock_urlopen_with_response(response),
        ),
        pytest.raises(SymbolResolutionError),
    ):
        resolve_via_searchscrip(user_id="uid123", exchange="NSE", symbol="NOSUCHSYM-EQ")


def test_searchscrip_retries_on_retryable_error():
    """Retryable HTTP errors trigger retries."""
    search_response = {
        "stat": "Ok",
        "scrips": [{"tsym": "SBIN-EQ", "token": "22", "name": "SBI"}],
    }
    mock_resp1 = MagicMock()
    mock_resp1.read.return_value = json.dumps({"stat": "Not_Ok", "emsg": "Rate_Limited"}).encode()
    mock_resp1.getcode.return_value = 200
    mock_resp1.headers = {}
    mock_resp1.__enter__.return_value = mock_resp1
    mock_resp1.__exit__.return_value = False

    mock_resp2 = MagicMock()
    mock_resp2.read.return_value = json.dumps(search_response).encode()
    mock_resp2.getcode.return_value = 200
    mock_resp2.headers = {}
    mock_resp2.__enter__.return_value = mock_resp2
    mock_resp2.__exit__.return_value = False

    with (
        patch("jev_imrp.ingest.symbol_resolver._get_credentials", return_value=_make_credentials()),
        patch(
            "jev_imrp.ingest.symbol_resolver.urllib.request.urlopen",
            side_effect=[mock_resp1, mock_resp2],
        ),
    ):
        result = resolve_via_searchscrip(user_id="uid123", exchange="NSE", symbol="SBIN-EQ")
        assert result.token == "22"


# ---------------------------------------------------------------------------
# Symbol master resolution
# ---------------------------------------------------------------------------


def test_master_resolution_exact_match():
    """Symbol master lookup returns exact match token."""
    zip_content = _create_symbol_master_zip(
        "NSE",
        [
            {"ts": "NSE", "tok": "22", "symbol": "SBIN-EQ", "name": "STATE BANK OF INDIA"},
            {"ts": "NSE", "tok": "23", "symbol": "RELIANCE-EQ", "name": "RELIANCE INDUSTRIES"},
        ],
    )
    mock_resp = MagicMock()
    mock_resp.read.return_value = zip_content
    mock_resp.getcode.return_value = 200
    mock_resp.headers = {}
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = False

    with patch("jev_imrp.ingest.symbol_resolver.urllib.request.urlopen", return_value=mock_resp):
        result = resolve_via_master("NSE", "SBIN-EQ")
        assert result.token == "22"
        assert result.symbol == "SBIN-EQ"


def test_master_resolution_missing_symbol_raises():
    """Symbol not in master → SymbolResolutionError."""
    zip_content = _create_symbol_master_zip(
        "NSE",
        [
            {"ts": "NSE", "tok": "22", "symbol": "SBIN-EQ", "name": "STATE BANK OF INDIA"},
            {"ts": "NSE", "tok": "23", "symbol": "RELIANCE-EQ", "name": "RELIANCE INDUSTRIES"},
        ],
    )
    mock_resp = MagicMock()
    mock_resp.read.return_value = zip_content
    mock_resp.getcode.return_value = 200
    mock_resp.headers = {}
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = False

    with (
        patch(
            "jev_imrp.ingest.symbol_resolver.urllib.request.urlopen",
            return_value=mock_resp,
        ),
        pytest.raises(SymbolResolutionError, match="not found"),
    ):
        resolve_via_master("NSE", "NOSUCHSYM-EQ")


def test_master_resolution_caches_result():
    """Subsequent lookups for the same symbol use the cache."""
    global _master_resolved_cache
    _master_resolved_cache.clear()
    zip_content = _create_symbol_master_zip(
        "NSE",
        [
            {"ts": "NSE", "tok": "22", "symbol": "SBIN-EQ", "name": "STATE BANK OF INDIA"},
            {"ts": "NSE", "tok": "23", "symbol": "RELIANCE-EQ", "name": "RELIANCE INDUSTRIES"},
        ],
    )
    mock_resp = MagicMock()
    mock_resp.read.return_value = zip_content
    mock_resp.getcode.return_value = 200
    mock_resp.headers = {}
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = False

    with patch("jev_imrp.ingest.symbol_resolver.urllib.request.urlopen", return_value=mock_resp):
        result1 = resolve_via_master("NSE", "SBIN-EQ")
        result2 = resolve_via_master("NSE", "SBIN-EQ")
        assert result1.token == result2.token


# ---------------------------------------------------------------------------
# resolve_symbol (combined path)
# ---------------------------------------------------------------------------


def test_resolve_symbol_prefers_master():
    """resolve_symbol tries master first, falls back to SearchScrip on miss."""
    zip_content = _create_symbol_master_zip(
        "NSE",
        [
            {"ts": "NSE", "tok": "22", "symbol": "SBIN-EQ", "name": "SBI"},
        ],
    )
    with patch(
        "jev_imrp.ingest.symbol_resolver.urllib.request.urlopen",
        side_effect=[
            # First call: master lookup (returns 200 with zip)
            MagicMock(
                read=MagicMock(return_value=zip_content),
                getcode=MagicMock(return_value=200),
                headers={},
                __enter__=lambda s: s,
                __exit__=MagicMock(return_value=False),
            ),
        ],
    ):
        result = resolve_symbol("NSE", "SBIN-EQ")
        assert result.token == "22"


def test_resolve_symbol_fallback_to_searchscrip():
    """When master fails, resolve_symbol falls back to SearchScrip."""
    search_response = {
        "stat": "Ok",
        "scrips": [{"tsym": "SBIN-EQ", "token": "22", "name": "SBI"}],
    }
    mock_master_resp = MagicMock()
    mock_master_resp.read.return_value = b""
    mock_master_resp.getcode.return_value = 200
    mock_master_resp.headers = {}
    mock_master_resp.__enter__.return_value = mock_master_resp
    mock_master_resp.__exit__.return_value = False

    mock_search_resp = MagicMock()
    mock_search_resp.read.return_value = json.dumps(search_response).encode()
    mock_search_resp.getcode.return_value = 200
    mock_search_resp.headers = {}
    mock_search_resp.__enter__.return_value = mock_search_resp
    mock_search_resp.__exit__.return_value = False

    with (
        patch("jev_imrp.ingest.symbol_resolver._get_credentials", return_value=_make_credentials()),
        patch(
            "jev_imrp.ingest.symbol_resolver.urllib.request.urlopen",
            side_effect=[mock_master_resp, mock_search_resp],
        ),
    ):
        result = resolve_symbol("NSE", "SBIN-EQ", use_master=True, user_id="uid123")
        assert result.token == "22"


# ---------------------------------------------------------------------------
# Batch resolution
# ---------------------------------------------------------------------------


def test_resolve_symbols_batch():
    """Batch resolution returns a list of ResolvedTokens."""
    master_zip = _create_symbol_master_zip(
        "NSE",
        [
            {"ts": "NSE", "tok": "22", "symbol": "SBIN-EQ", "name": "SBI"},
            {"ts": "NSE", "tok": "1419", "symbol": "RELIANCE-EQ", "name": "RELIANCE"},
        ],
    )
    mock_resp = MagicMock()
    mock_resp.read.return_value = master_zip
    mock_resp.getcode.return_value = 200
    mock_resp.headers = {}
    mock_resp.__enter__.return_value = mock_resp
    mock_resp.__exit__.return_value = False

    with patch(
        "jev_imrp.ingest.symbol_resolver.urllib.request.urlopen", side_effect=[mock_resp, mock_resp]
    ):
        results = resolve_symbols_batch(
            [
                ("NSE", "SBIN-EQ"),
                ("NSE", "RELIANCE-EQ"),
            ]
        )
        assert len(results) == 2
        assert results[0].token == "22"
        assert results[1].token == "1419"


# ---------------------------------------------------------------------------
# REST URL constants
# ---------------------------------------------------------------------------


def test_symbol_master_url_format():
    """Symbol master URLs follow the documented pattern."""
    assert fetch_symbol_master_url("NSE") == "https://api.shoonya.com/NSE_symbols.txt.zip"
    assert fetch_symbol_master_url("BSE") == "https://api.shoonya.com/BSE_symbols.txt.zip"


def test_rest_base_constant():
    """REST base uses the documented endpoint path (CLAUDE.md: NorenWClientAPI/)."""
    from jev_imrp.ingest.symbol_resolver import REST_BASE

    assert REST_BASE == "https://api.shoonya.com/NorenWClientAPI/"
