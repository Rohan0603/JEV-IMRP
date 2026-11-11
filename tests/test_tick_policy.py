from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from jev_imrp.validation.tick_policy import (
    TickEvidence,
    TickPolicyError,
    nse_cash_tick_from_price,
    resolve_effective_tick,
)


@pytest.mark.parametrize(
    ("price", "expected"),
    [
        ("249.99", "0.01"),
        ("250", "0.05"),
        ("1000", "0.05"),
        ("1000.01", "0.10"),
        ("5000", "0.10"),
        ("5000.01", "0.50"),
        ("10000", "0.50"),
        ("10000.01", "1.00"),
        ("20000", "1.00"),
        ("20000.01", "5.00"),
    ],
)
def test_nse_price_band_boundaries(price: str, expected: str) -> None:
    assert nse_cash_tick_from_price(Decimal(price)) == Decimal(expected)


def test_effective_tick_resolution_is_dated_and_fail_closed() -> None:
    records = [
        TickEvidence(
            symbol="RELIANCE",
            series="EQ",
            effective_from=date(2026, 1, 1),
            effective_to=date(2026, 2, 1),
            tick_size=Decimal("0.05"),
            source="NSE security file",
            source_sha256="a" * 64,
        ),
        TickEvidence(
            symbol="RELIANCE",
            series="EQ",
            effective_from=date(2026, 2, 1),
            effective_to=None,
            tick_size=Decimal("0.01"),
            source="NSE security file",
            source_sha256="b" * 64,
        ),
    ]

    assert resolve_effective_tick(records, "RELIANCE", date(2026, 2, 1)) == Decimal("0.01")
    with pytest.raises(TickPolicyError, match="no effective tick evidence"):
        resolve_effective_tick(records, "TCS", date(2026, 2, 1))


def test_invalid_or_ambiguous_tick_evidence_fails_closed() -> None:
    evidence = TickEvidence(
        symbol="RELIANCE",
        series="EQ",
        effective_from=date(2026, 1, 1),
        effective_to=None,
        tick_size=Decimal("0.01"),
        source="NSE security file",
        source_sha256="bad",
    )

    with pytest.raises(TickPolicyError, match="source hash"):
        resolve_effective_tick([evidence], "RELIANCE", date(2026, 2, 1))
