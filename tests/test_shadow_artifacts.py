from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_shadow_artifacts_are_drafts_and_fail_closed() -> None:
    history = (ROOT / "docs" / "HISTORICAL_DATA_QUALIFICATION_2026-11-07.md").read_text(
        encoding="utf-8"
    )
    protocol = (ROOT / "docs" / "SHADOW_STUDY_PROTOCOL_2026-11-07.md").read_text(
        encoding="utf-8"
    )
    runbook = (ROOT / "docs" / "SHADOW_OPERATIONS_RUNBOOK_2026-11-07.md").read_text(
        encoding="utf-8"
    )

    assert "NOT RUN" in history
    assert "cannot establish L1" in history
    assert "NOT APPROVED; NOT ACTIVE" in protocol
    assert "NOT ACTIVE" in runbook
    assert "no live collection" in protocol
