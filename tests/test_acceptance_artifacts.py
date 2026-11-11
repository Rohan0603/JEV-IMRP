from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_acceptance_artifacts_keep_external_gates_open() -> None:
    power = (ROOT / "docs" / "POWER_AND_WINDOW_FEASIBILITY_2026-11-07.md").read_text(
        encoding="utf-8"
    )
    labels = (ROOT / "docs" / "LABEL_COST_ACCEPTANCE_2026-11-07.md").read_text(
        encoding="utf-8"
    )

    assert "NO STUDY RESULTS INSPECTED" in power
    assert "Power/MDE result" in power
    assert "UNRESOLVED SOURCE CONFLICT" in labels
    assert "Independent review" in labels
    assert "No bid/ask may be inferred" in labels
