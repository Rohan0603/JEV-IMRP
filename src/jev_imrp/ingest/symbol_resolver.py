"""Symbol-to-token resolution for the Shoonya feed (plan 5.3, CLAUDE.md).

Research-only. This module never opens a socket itself; it wraps REST calls
behind an interface that the feed client and tests can both exercise.

Key constraints from CLAUDE.md:

* ``searchscrip`` is fuzzy: it matches partial trading symbols, so
  ``"SBIN"`` returns ``SBINMID150-EQ`` before ``SBIN-EQ``. We require an
  exact ``SYMBOL-EQ`` match and reject anything else.
* Symbol masters are downloadable per exchange at
  ``https://api.shoonya.com/<EX>_symbols.txt.zip`` and bypass login entirely.
  They are the preferred resolution path because they are deterministic and
  rate-limit-free.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from typing import Sequence

logger = logging.getLogger(__name__)

#: REST base for authenticated endpoints (CLAUDE.md: live API uses NorenWClientAPI/).
REST_BASE = "https://api.shoonya.com/NorenWClientAPI/"

#: Symbol masters bypass login entirely (CLAUDE.md).
SYMBOL_MASTER_BASE = "https://api.shoonya.com/"


@dataclass(frozen=True, slots=True)
class ResolvedToken:
    """A successfully resolved instrument token."""

    exchange: str
    token: str
    symbol: str
    instrument_name: str
    expiry: str | None = None
    strike: float | None = None
    option_type: str | None = None


class SymbolResolutionError(Exception):
    """Raised when a symbol cannot be resolved to a token."""


def _get_credentials() -> tuple[str, str, str]:
    """Retrieve API credentials from environment variables.

    **Why env vars only (CLAUDE.md):** credentials are never read from config,
    code, or logs. The JEV-IMRP policy is strict: anything in a versioned file
    is treated as a secret leak.
    """
    api_key = os.getenv("SHOONYA_API_KEY")
    api_secret = os.getenv("SHOONYA_API_SECRET")
    access_token = os.getenv("SHOONYA_ACCESS_TOKEN")
    if not all([api_key, api_secret, access_token]):
        missing = [
            name for name, val in (
                ("SHOONYA_API_KEY", api_key),
                ("SHOONYA_API_SECRET", api_secret),
                ("SHOONYA_ACCESS_TOKEN", access_token),
            ) if not val
        ]
        raise SymbolResolutionError(f"missing credentials: {missing}")
    return api_key, api_secret, access_token


def _build_checksum(client_id: str, client_secret: str, auth_code: str | None = None) -> str:
    """Build the SHA256 checksum: SHA256(client_id + secret + code)."""
    import hashlib

    base = client_id + client_secret
    if auth_code:
        base += auth_code
    return hashlib.sha256(base.encode("utf-8")).hexdigest()


def _form_request(jdata: dict[str, str], jkey: str, *, endpoint: str) -> urllib.Request:
    """Build a form-urlencoded POST request with jData/jKey."""
    body = urllib.parse.urlencode({"jData": json.dumps(jdata), "jKey": jkey})
    req = urllib.request.Request(
        f"{REST_BASE}{endpoint}",
        data=body.encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    return req


def _parse_response(raw: bytes) -> dict[str, object]:
    """Parse a Shoonya REST response, checking ``stat``."""
    decoded = json.loads(raw.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise SymbolResolutionError(f"unexpected response shape: {type(decoded).__name__}")
    stat = decoded.get("stat")
    if stat != "Ok":
        emsg = decoded.get("emsg", "unknown error")
        raise SymbolResolutionError(f"API returned stat={stat}: {emsg}")
    return decoded


def resolve_via_searchscrip(
    *,
    user_id: str,
    exchange: str,
    symbol: str,
    retryable_codes: frozenset[str] | None = None,
    max_retries: int = 3,
) -> ResolvedToken:
    """Resolve a symbol to a token using the SearchScrip REST endpoint.

    **CLAUDE.md constraint:** ``searchscrip`` is fuzzy — it returns partial
    matches. We require an exact ``SYMBOL-EQ`` match and reject anything else.

    Args:
        user_id: Shoonya user ID.
        exchange: Exchange code (e.g. ``"NSE"``).
        symbol: Trading symbol (e.g. ``"SBIN-EQ"``).
        retryable_codes: Set of stat strings that warrant a retry.
        max_retries: Maximum retry attempts for retryable errors.

    Returns:
        A :class:`ResolvedToken` with the exchange, token, and symbol.

    Raises:
        SymbolResolutionError: If the symbol cannot be resolved or if fuzzy
            results are returned without an exact match.
    """
    if retryable_codes is None:
        retryable_codes = frozenset({"Rate_Limited", "Server Timeout", "Session Expired"})

    _, _, access_token = _get_credentials()

    payload = {"uid": user_id, "exch": exchange, "sym": symbol}

    for attempt in range(max_retries):
        try:
            req = _form_request(payload, access_token, endpoint="SearchScrip")
            with urllib.request.urlopen(req, timeout=30) as response:
                raw_body = response.read()

            # Parse and check for retryable body stat errors (HTTP 200 with stat=Not_Ok)
            decoded = json.loads(raw_body.decode("utf-8"))
            if isinstance(decoded, dict) and decoded.get("stat") != "Ok":
                emsg = decoded.get("emsg", "")
                if any(code.lower() in emsg.lower() for code in retryable_codes) and attempt < max_retries - 1:
                    logger.warning("SearchScrip rate limited or timeout on attempt %d: %s", attempt + 1, emsg)
                    continue

            data = _parse_response(raw_body)
            break
        except urllib.error.HTTPError as exc:
            if _is_retryable_http(exc, retryable_codes) and attempt < max_retries - 1:
                logger.warning("SearchScrip HTTP error on attempt %d: %s", attempt + 1, exc)
                continue
            raise SymbolResolutionError(f"SearchScrip HTTP error: {exc}") from exc
        except urllib.request.URLError as exc:
            if attempt < max_retries - 1:
                logger.warning("SearchScrip URL error on attempt %d: %s", attempt + 1, exc)
                continue
            raise SymbolResolutionError(f"SearchScrip URL error: {exc}") from exc
        except SymbolResolutionError as exc:
            if any(code.lower() in str(exc).lower() for code in retryable_codes) and attempt < max_retries - 1:
                logger.warning("SearchScrip resolution error on attempt %d: %s", attempt + 1, exc)
                continue
            raise
    else:
        raise SymbolResolutionError(f"SearchScrip failed after {max_retries} attempts")

    # The response contains a list of scrips, potentially multiple matches.
    scrips = data.get("scrips", [])
    if not isinstance(scrips, list):
        raise SymbolResolutionError(f"unexpected scrips format: {type(scrips).__name__}")

    # Exact match on SYMBOL-EQ form; reject fuzzy results.
    exact_matches = [
        s for s in scrips
        if isinstance(s, dict) and s.get("tsym") == symbol
    ]

    if not exact_matches:
        # Log what was returned so operators can diagnose fuzzy-match issues.
        returned = [s.get("tsym", "?") for s in scrips if isinstance(s, dict)][:5]
        raise SymbolResolutionError(
            f"searchscrip returned fuzzy matches {returned} — no exact '{symbol}' match"
        )

    match = exact_matches[0]
    return ResolvedToken(
        exchange=exchange,
        token=str(match.get("token", "")),
        symbol=symbol,
        instrument_name=str(match.get("name", "")),
        expiry=match.get("expiry"),
        strike=match.get("strike"),
        option_type=match.get("optt"),
    )


def _is_retryable_http(exc: urllib.error.HTTPError, retryable_codes: frozenset[str]) -> bool:
    """Check if an HTTPError represents a retryable condition."""
    if exc.code in (429, 503, 504):
        return True
    # Some Shoonya errors come back as HTTP 200 with stat=Not_Ok.
    try:
        body = json.loads(exc.read().decode("utf-8"))
        stat = body.get("stat", "")
        emsg = body.get("emsg", "").lower()
        if stat == "Not_Ok":
            # Check for retryable codes in the error message
            if any(code.lower() in emsg for code in retryable_codes):
                return True
            # Also check for specific Shoonya error messages
            if "rate_limited" in emsg or "server timeout" in emsg or "session expired" in emsg:
                return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------------------
# Symbol master (preferred: no auth, no rate limit, deterministic)
# ---------------------------------------------------------------------------

def fetch_symbol_master_url(exchange: str) -> str:
    """Return the URL for an exchange's symbol master ZIP.

    **CLAUDE.md:** Symbol masters are downloadable per exchange at
    ``https://api.shoonya.com/<EX>_symbols.txt.zip`` and bypass login entirely.
    """
    return f"{SYMBOL_MASTER_BASE}{exchange}_symbols.txt.zip"


def resolve_via_master(
    exchange: str, symbol: str, *, master_cache: dict[str, dict[str, str]] | None = None
) -> ResolvedToken:
    """Resolve a symbol using the downloaded symbol master (preferred path).

    The symbol master is a ZIP containing a ``.txt`` file with comma-delimited
    columns. Column ``TradingSymbol`` holds the trading symbol (e.g.
    ``SBIN-EQ``) and column ``Token`` holds the numeric instrument token.

    **CLAUDE.md advantage:** no authentication, no rate limit, deterministic.
    This is the preferred resolution path for the research platform.
    """
    import csv
    import io
    import zipfile

    if master_cache is not None and (exchange, symbol) in _master_resolved_cache:
        token = _master_resolved_cache[(exchange, symbol)]
        return ResolvedToken(exchange=exchange, token=token, symbol=symbol, instrument_name=symbol)

    # Check if the exchange master is cached
    if master_cache is not None and exchange in master_cache:
        exchange_map = master_cache[exchange]
        token = exchange_map.get(symbol)
        if token is None:
            raise SymbolResolutionError(
                f"symbol '{symbol}' not found in {exchange} symbol master"
            )
        if master_cache is not None:
            _master_resolved_cache[(exchange, symbol)] = token
        return ResolvedToken(exchange=exchange, token=token, symbol=symbol, instrument_name=symbol)

    # Download and parse the master (research-only; real implementation would
    # cache the parsed CSV per exchange).
    url = fetch_symbol_master_url(exchange)
    try:
        with urllib.request.urlopen(url, timeout=60) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        raise SymbolResolutionError(f"failed to download symbol master for {exchange}: {exc}") from exc
    except urllib.request.URLError as exc:
        raise SymbolResolutionError(f"network error downloading symbol master for {exchange}: {exc}") from exc

    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            txt_name = [n for n in zf.namelist() if n.endswith(".txt")][0]
            with zf.open(txt_name) as f:
                content = f.read().decode("utf-8")
    except Exception as exc:
        raise SymbolResolutionError(f"failed to parse symbol master ZIP for {exchange}: {exc}") from exc

    # Parse the CSV. The live master is comma-delimited with columns
    # Exchange,Token,LotSize,Symbol,TradingSymbol,Instrument,TickSize,...
    # The trading symbol (e.g. SBIN-EQ) lives in TradingSymbol.
    reader = csv.DictReader(io.StringIO(content), delimiter=",")
    exchange_map: dict[str, str] = {}
    for row in reader:
        sym = (
            row.get("TradingSymbol")
            or row.get("tsym")
            or row.get("symbol")
            or row.get("SYMBOL")
        )
        tok = row.get("Token") or row.get("token") or row.get("TOKEN")
        if sym and tok:
            exchange_map[sym.strip()] = str(tok).strip()

    token = exchange_map.get(symbol)
    if token is None:
        raise SymbolResolutionError(f"symbol '{symbol}' not found in {exchange} symbol master")

    if master_cache is not None:
        master_cache[exchange] = exchange_map
        _master_resolved_cache[(exchange, symbol)] = token

    return ResolvedToken(exchange=exchange, token=token, symbol=symbol, instrument_name=symbol)


#: Cache for resolved tokens from symbol masters.
_master_resolved_cache: dict[tuple[str, str], str] = {}


def resolve_symbol(
    exchange: str,
    symbol: str,
    *,
    use_master: bool = True,
    user_id: str | None = None,
    master_cache: dict[str, dict[str, str]] | None = None,
) -> ResolvedToken:
    """Resolve a symbol to a token, preferring the symbol master.

    **CLAUDE.md priority:** Symbol masters bypass login entirely and are
    deterministic. They are tried first when ``use_master`` is True. If the
    symbol is not found in the master (or the master is unavailable), fall
    back to the authenticated ``SearchScrip`` REST endpoint — which requires
    exact ``SYMBOL-EQ`` matching because it is fuzzy.
    """
    if use_master:
        try:
            return resolve_via_master(exchange, symbol, master_cache=master_cache)
        except SymbolResolutionError as exc:
            logger.info("symbol master resolution failed for %s|%s: %s — falling back to SearchScrip",
                        exchange, symbol, exc)

    if user_id is None:
        raise SymbolResolutionError(
            "user_id required for SearchScrip fallback (set SHOONYA_USER_ID or pass explicitly)"
        )
    return resolve_via_searchscrip(user_id=user_id, exchange=exchange, symbol=symbol)


def resolve_symbols_batch(
    instruments: Sequence[tuple[str, str]],
    *,
    use_master: bool = True,
    user_id: str | None = None,
) -> list[ResolvedToken]:
    """Resolve a batch of (exchange, symbol) pairs to tokens.

    Returns a list of :class:`ResolvedToken` instances. Failures are not
    silently dropped: each failure raises so the caller can decide whether
    to skip or abort.
    """
    results: list[ResolvedToken] = []
    for exchange, symbol in instruments:
        token = resolve_symbol(exchange, symbol, use_master=use_master, user_id=user_id)
        results.append(token)
    return results


__all__ = [
    "REST_BASE",
    "SYMBOL_MASTER_BASE",
    "ResolvedToken",
    "SymbolResolutionError",
    "resolve_symbol",
    "resolve_via_master",
    "resolve_via_searchscrip",
    "resolve_symbols_batch",
]
