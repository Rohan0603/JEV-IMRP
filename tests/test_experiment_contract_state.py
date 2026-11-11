from __future__ import annotations

from pathlib import Path

import yaml


def test_experiment_config_cannot_look_approved_before_amendment_signoff() -> None:
    config = yaml.safe_load(
        (Path(__file__).parents[1] / "config" / "experiment.yaml").read_text(
            encoding="utf-8"
        )
    )

    assert config["mode"] == "research_only"
    assert config["contract_state"] == "draft_unapproved"
    assert config["amendment_record"] == (
        "docs/PREREGISTRATION_RECONCILIATION_2026-11-07.md"
    )
