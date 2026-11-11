from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_licensing_policy_is_dated_and_fail_closed() -> None:
    text = (ROOT / "docs" / "LICENSING_RETENTION_POLICY_2026-11-07.md").read_text(
        encoding="utf-8"
    )

    assert "DRAFT — OWNER/LEGAL APPROVAL REQUIRED" in text
    assert "Unknown permissions fail closed" in text
    assert "Redistribution, publication, or sharing" in text
    assert "ORC-116" in text


def test_preregistration_reconciliation_preserves_signoff_gate() -> None:
    text = (ROOT / "docs" / "PREREGISTRATION_RECONCILIATION_2026-11-07.md").read_text(
        encoding="utf-8"
    )

    assert "NO STUDY DATA INSPECTED" in text
    assert "1 basis point" in text
    assert "Owner | PENDING" in text
    assert "Independent reviewer | PENDING" in text
    assert "locked-holdout access" in text
