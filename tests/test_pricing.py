from decimal import Decimal

import pytest

from jev_imrp.models.pricing import (
    CostStatus,
    PricingSnapshot,
    aggregate_costs,
    estimate_cost,
)

SNAPSHOT = PricingSnapshot(
    provider="typesafe",
    route="https://api.typesafe.ai/v1/systemone",
    model_id="jev-1.13.0",
    input_usd_per_million=Decimal("0.042"),
    output_usd_per_million=Decimal("0"),
    source_url="https://help.typesafe.ai/articles/5187202169-how-much-does-jev-cost",
    source_updated="2026-11-03",
    checked_at="2026-11-05",
)


@pytest.mark.parametrize(
    ("tokens", "expected"),
    [(1_000_000, Decimal("0.042")), (1_000, Decimal("0.000042"))],
)
def test_estimate_uses_provider_input_rate_and_zero_output_rate(
    tokens: int, expected: Decimal
) -> None:
    result = estimate_cost(
        input_tokens=tokens,
        output_tokens=999,
        model_id="jev-1.13.0",
        mode="live",
        snapshot=SNAPSHOT,
    )
    assert result.status is CostStatus.KNOWN
    assert result.estimated_usd == expected


def test_missing_output_with_valid_input_is_priced() -> None:
    result = estimate_cost(
        input_tokens=1_000,
        output_tokens=None,
        model_id="jev-1.13.0",
        mode="live",
        snapshot=SNAPSHOT,
    )
    assert result.status is CostStatus.KNOWN
    assert result.estimated_usd == Decimal("0.000042")


def test_unknown_route_is_not_priced() -> None:
    result = estimate_cost(
        1_000,
        1,
        "jev-1.13.0",
        "live",
        SNAPSHOT,
        route="https://example.invalid/other",
    )
    assert result.status is CostStatus.UNKNOWN
    assert result.reason == "unknown_route"


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"input_tokens": None, "output_tokens": 1}, "missing_input_usage"),
        ({"input_tokens": 1, "output_tokens": 1, "model_id": "jev-latest"}, "unknown_model"),
    ],
)
def test_unpriced_live_usage_is_unknown(kwargs: dict[str, object], reason: str) -> None:
    values: dict[str, object] = {
        "input_tokens": 1,
        "output_tokens": 1,
        "model_id": "jev-1.13.0",
        "mode": "live",
        "snapshot": SNAPSHOT,
    }
    values.update(kwargs)
    result = estimate_cost(**values)  # type: ignore[arg-type]
    assert result.status is CostStatus.UNKNOWN
    assert result.reason == reason
    assert result.estimated_usd is None


def test_mocked_run_is_zero_and_distinct_from_provider_estimate() -> None:
    result = estimate_cost(
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        model_id="jev-1.13.0",
        mode="mocked",
        snapshot=SNAPSHOT,
    )
    assert result.status is CostStatus.MOCKED_ZERO
    assert result.estimated_usd == Decimal("0")


def test_aggregate_sums_before_display_rounding_and_deduplicates_attempts() -> None:
    first = estimate_cost(1, 0, "jev-1.13.0", "live", SNAPSHOT, attempt_id="a1")
    second = estimate_cost(1, 0, "jev-1.13.0", "live", SNAPSHOT, attempt_id="a2")
    report = aggregate_costs([first, second, first])
    assert report.status is CostStatus.KNOWN
    assert report.estimated_usd == Decimal("0.000000084")
    assert report.display_usd == "0.00000008"
    assert report.attempt_count == 2
    assert report.source_url == SNAPSHOT.source_url


def test_negative_or_boolean_usage_is_rejected() -> None:
    with pytest.raises(ValueError):
        estimate_cost(-1, 0, "jev-1.13.0", "live", SNAPSHOT)
    with pytest.raises(ValueError):
        estimate_cost(True, 0, "jev-1.13.0", "live", SNAPSHOT)
