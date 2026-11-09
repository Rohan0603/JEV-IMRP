"""Provider-neutral, data-only ingestion contract.

The interface intentionally contains no account or transaction operations.
Implementations may use a gateway or a direct provider, but callers consume
only symbol, quote, and historical market-data responses.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class MarketDataProvider(Protocol):
    """Minimal read-only market-data surface used by JEV ingestion."""

    def symbol(self, *, symbol: str, exchange: str) -> Any:
        """Resolve one provider symbol without changing account state."""

    def quote(self, *, symbol: str, exchange: str) -> Mapping[str, object]:
        """Fetch one quote payload."""

    def depth(self, *, symbol: str, exchange: str) -> Mapping[str, object]:
        """Fetch provider-reported depth, preserving absent levels."""

    def history(
        self,
        *,
        symbol: str,
        exchange: str,
        interval: str,
        start_date: str,
        end_date: str,
    ) -> list[Mapping[str, object]]:
        """Fetch historical candles."""


__all__ = ["MarketDataProvider"]
