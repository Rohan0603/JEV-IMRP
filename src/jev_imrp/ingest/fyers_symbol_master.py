"""Provider-specific FYERS symbol mapping over the frozen research universe."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


class FyersSymbolError(ValueError):
    """A FYERS symbol-master mapping is missing or unsafe to use."""


@dataclass(frozen=True, slots=True)
class FyersInstrument:
    research_symbol: str
    provider_symbol: str
    series: str
    status: str
    master_sha256: str
    #: FYERS exchange token used in ``<type>|<segment>|<token>`` HSM topics.
    #: Optional here so older callers can still validate symbol mapping; the
    #: subscription planner fails closed when it is missing.
    exchange_token: str | None = None


_PLAIN_SYMBOL = re.compile(r"^[A-Z][A-Z0-9&.-]*$")
_FYERS_SYMBOL = re.compile(r"^NSE:[A-Z][A-Z0-9&.-]*-EQ$")


def to_fyers_symbol(symbol: str) -> str:
    """Convert one frozen cash-equity name to an exact FYERS ticker."""

    if not symbol:
        raise FyersSymbolError("symbol must not be empty")
    if ":" in symbol:
        if not _FYERS_SYMBOL.fullmatch(symbol):
            raise FyersSymbolError("FYERS symbol must be an exact NSE:<NAME>-EQ ticker")
        return symbol
    if not _PLAIN_SYMBOL.fullmatch(symbol):
        raise FyersSymbolError("research symbol must be an exact uppercase name")
    return f"NSE:{symbol}-EQ"


def build_fyers_mapping(
    research_symbols: Sequence[str],
    master_rows: Sequence[Mapping[str, Any]],
    *,
    master_sha256: str,
) -> list[FyersInstrument]:
    """Resolve frozen symbols against exact active EQ rows, preserving order."""

    _validate_hash(master_sha256)
    by_symbol: dict[str, Mapping[str, Any]] = {}
    for row in master_rows:
        provider_symbol = row.get("symbol")
        if not isinstance(provider_symbol, str):
            continue
        if provider_symbol in by_symbol:
            raise FyersSymbolError(f"duplicate FYERS master row for {provider_symbol}")
        by_symbol[provider_symbol] = row

    result: list[FyersInstrument] = []
    for research_symbol in research_symbols:
        provider_symbol = to_fyers_symbol(research_symbol)
        matched_row = by_symbol.get(provider_symbol)
        if matched_row is None:
            raise FyersSymbolError(f"missing FYERS master symbol {provider_symbol}")
        status = str(matched_row.get("status", "")).upper()
        if status != "ACTIVE":
            raise FyersSymbolError(f"FYERS symbol {provider_symbol} is suspended")
        series = str(matched_row.get("series", ""))
        if series != "EQ":
            raise FyersSymbolError(f"FYERS symbol {provider_symbol} is not cash EQ")
        result.append(
            FyersInstrument(
                research_symbol=research_symbol,
                provider_symbol=provider_symbol,
                series=series,
                status=status,
                master_sha256=master_sha256,
                exchange_token=_exchange_token(matched_row),
            )
        )
    return result


def _exchange_token(row: Mapping[str, Any]) -> str | None:
    """Read the FYERS exchange token from the master row, or return None.

    The official master exposes a 14-digit ``fyToken`` whose last digits are the
    exchange token (the reference SDK uses ``fyToken[10:]``). A row that already
    carries ``exchange_token``/``token`` is accepted as-is, including synthetic
    test tokens such as ``SYNTH2885``.
    """
    for key in ("exchange_token", "exch_token", "fyToken", "fy_token", "token"):
        value = row.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if not text:
            continue
        if text.isdigit() and len(text) > 10:
            text = text[10:]
        if text:
            return text
    return None


def _validate_hash(value: str) -> None:
    if len(value) != 64:
        raise FyersSymbolError("FYERS master source hash must be SHA-256")
    try:
        int(value, 16)
    except ValueError as exc:
        raise FyersSymbolError("FYERS master source hash must be SHA-256") from exc


__all__ = [
    "FyersInstrument",
    "FyersSymbolError",
    "build_fyers_mapping",
    "to_fyers_symbol",
]
