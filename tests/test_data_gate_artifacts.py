from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_quota_and_data_health_artifacts_are_offline_only() -> None:
    quota = (ROOT / "docs" / "QUOTA_HEALTH_POLICY_2026-11-07.md").read_text(
        encoding="utf-8"
    )
    health = (ROOT / "docs" / "DATA_HEALTH_GATE_REPORT_2026-11-07.md").read_text(
        encoding="utf-8"
    )

    assert "ACCOUNT TIER NOT VERIFIED" in quota
    assert "Credentialed run NOT RUN" in quota
    assert "LIVE GATE NOT PASSED" in health
    assert "receipt time must not substitute" in health
