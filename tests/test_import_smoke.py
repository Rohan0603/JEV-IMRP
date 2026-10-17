"""Import smoke test with version/schema constants (mission phase 3 foundations)."""

from __future__ import annotations

import jev_imrp
from jev_imrp.common import config, errors, ledger, logging, timeutils, version


def test_public_exports_are_present_and_versioned() -> None:
    assert jev_imrp.__version__ == "0.1.0"
    for name in jev_imrp.__all__:
        assert hasattr(jev_imrp, name), name


def test_schema_constants_are_unique_and_prefixed() -> None:
    constants = {
        name: getattr(version, name)
        for name in dir(version)
        if name.endswith("VERSION") and not name.startswith("__")
    }
    assert {
        "RAW_EVENT_SCHEMA_VERSION",
        "QUOTE_STATE_SCHEMA_VERSION",
        "PARSER_VERSION",
        "FEATURE_CONFIG_VERSION",
        "LABEL_VERSION",
        "SIMULATION_VERSION",
        "COST_SCHEDULE_VERSION",
        "UNIVERSE_VERSION",
        "CONFIG_SCHEMA_VERSION",
        "EXPERIMENT_CONTRACT_VERSION",
    } <= constants.keys()
    assert len(set(constants.values())) == len(constants), constants


def test_foundation_modules_import() -> None:
    assert config.REPO_ROOT.name
    assert issubclass(errors.ConfigError, errors.JevError)
    assert ledger.REQUIRED_COLUMNS
    assert timeutils.IST is not None
    assert logging.SECRET_NAMES


def test_error_hierarchy_never_carries_values() -> None:
    error = errors.MissingSettingError("cost_schedule", ["stt.rate_pct"])
    assert "stt.rate_pct" in str(error)
    assert error.missing == ["stt.rate_pct"]
