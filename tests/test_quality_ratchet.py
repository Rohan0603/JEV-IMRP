from __future__ import annotations

import importlib.util
from pathlib import Path
from unittest.mock import patch


MODULE_PATH = Path(__file__).parents[1] / "scripts" / "quality_ratchet.py"
SPEC = importlib.util.spec_from_file_location("quality_ratchet", MODULE_PATH)
assert SPEC and SPEC.loader
quality_ratchet = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(quality_ratchet)


def test_missing_quality_tool_fails_closed() -> None:
    with patch.object(quality_ratchet, "run", return_value=(127, "FileNotFoundError: ruff")):
        assert quality_ratchet.main() == 1


def test_known_baseline_failure_is_allowed() -> None:
    outputs = iter(
        [
            (1, "Found 127 errors."),
            (1, "39 files would be reformatted"),
            (1, "Found 24 errors in 5 files"),
        ]
    )
    with patch.object(quality_ratchet, "run", side_effect=lambda *_: next(outputs)):
        assert quality_ratchet.main() == 0
