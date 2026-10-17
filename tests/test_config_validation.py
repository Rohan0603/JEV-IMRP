"""Config validation: missing settings fail clearly; secrets never leak."""

from __future__ import annotations

import logging

import pytest

from jev_imrp.common.config import (
    is_unresolved,
    load_cost_schedule,
    load_execution_policy,
    load_feature_config,
    load_required_config,
    load_universe,
    load_yaml,
    require_settings,
)
from jev_imrp.common.errors import ConfigError, MissingSettingError
from jev_imrp.common.logging import RedactingFilter, get_logger, redact


def test_shipped_configs_load() -> None:
    for loaded in (load_universe(), load_cost_schedule(), load_execution_policy(),
                   load_feature_config()):
        assert isinstance(loaded, dict) and loaded


def test_missing_required_setting_names_the_key(tmp_path) -> None:
    path = tmp_path / "partial.yaml"
    path.write_text("a: 1\n", encoding="utf-8")

    with pytest.raises(MissingSettingError) as excinfo:
        load_required_config(path, ["a", "b.c"], config_name="partial")

    message = str(excinfo.value)
    assert "b.c" in message
    assert "partial" in message


def test_null_setting_counts_as_missing() -> None:
    with pytest.raises(MissingSettingError):
        require_settings({"a": None, "b": 1}, ["a", "b"], "cfg")


def test_secret_value_is_never_echoed_in_errors(tmp_path) -> None:
    secret = "hunter2-not-a-real-secret"
    path = tmp_path / "with_secret.yaml"
    path.write_text(f"api_key: {secret}\n", encoding="utf-8")

    with pytest.raises(MissingSettingError) as excinfo:
        load_required_config(path, ["api_key", "broker"], config_name="with_secret")

    assert secret not in str(excinfo.value)


def test_secret_value_is_never_echoed_in_logs(caplog) -> None:
    secret = "another-not-a-real-secret"
    logger = get_logger("jev.test.redaction")
    with caplog.at_level(logging.INFO, logger="jev.test.redaction"):
        logger.info("connect ttoken=%s", secret)
        logger.info("payload %s", {"api_key": secret, "symbol": "RELIANCE"})

    assert secret not in caplog.text
    assert "RELIANCE" in caplog.text  # redaction must not swallow normal fields


def test_redact_handles_common_shapes() -> None:
    for raw in ("ttoken=abc123", "password: p@ss", "Authorization: Bearer xyz.abc",
                "access_token=zzz"):
        assert "REDACTED" in redact(raw), raw


def test_redacting_filter_passes_records() -> None:
    record = logging.LogRecord("n", logging.INFO, "p", 1, "ttoken=abc", None, None)
    assert RedactingFilter().filter(record) is True
    assert "abc" not in record.getMessage()


def test_invalid_yaml_is_a_config_error(tmp_path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("a: [1, 2\n", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_yaml(path)


def test_missing_file_is_a_config_error(tmp_path) -> None:
    with pytest.raises(ConfigError):
        load_yaml(tmp_path / "absent.yaml")


def test_is_unresolved_flags_tbd_and_unverified() -> None:
    assert is_unresolved(None)
    assert is_unresolved("TBD - must be fixed before data inspection")
    assert is_unresolved({"rate_pct": None})
    assert is_unresolved(["UNVERIFIED (plan section 8.4; external verification in flight)"])
    assert not is_unresolved(0.05)
    assert not is_unresolved({"rate_pct": 0.05})


def test_shipped_cost_rates_are_marked_unverified() -> None:
    schedule = load_cost_schedule()
    assert "UNVERIFIED" in schedule["unverified_notice"]
    components = schedule["versions"][0]["components"]
    for name in ("brokerage", "stt", "exchange_transaction_fee_nse", "sebi_turnover_fee",
                 "stamp_duty", "gst", "ipft_and_other_levies"):
        assert "verification" in components[name], name
        assert "UNVERIFIED" in components[name]["verification"], name


def test_instrument_tokens_are_not_invented() -> None:
    instruments = load_universe()["instruments"]
    assert len(instruments) == 20
    assert len({i["symbol"] for i in instruments}) == 20
    assert all(i["instrument_token"] is None for i in instruments)
