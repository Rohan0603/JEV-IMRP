from __future__ import annotations

from pathlib import Path

AMENDMENT = (
    Path(__file__).parents[1] / "docs" / "PROVIDER_SWITCH_AMENDMENT_2026-11-07.md"
)


def test_provider_switch_amendment_is_explicitly_prospective_and_unapproved() -> None:
    text = AMENDMENT.read_text(encoding="utf-8")

    assert "DRAFT — NOT APPROVED" in text
    assert "Historical observations retain their original `provider=shoonya`" in text
    assert "no orders, no live routing, and no locked-holdout access" in text
    assert "live parity **NOT RUN**" in text
    assert "November 2026 file and 13 reconciliations **PENDING**" in text
    assert "Owner | **PENDING**" in text
    assert "Independent reviewer | **PENDING**" in text


def test_provider_switch_amendment_preserves_original_contract_references() -> None:
    text = AMENDMENT.read_text(encoding="utf-8")

    assert "../PREREGISTRATION.md" in text
    assert "experiments/trial_ledger.csv" in text
    assert "original" in text.lower()
    assert "not reclassified" in text
