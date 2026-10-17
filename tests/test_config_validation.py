"""Config validation: missing settings fail clearly; secrets never leak."""

from __future__ import annotations

import logging

import pytest

from jev_imrp.common.config import (
    CONFIG_DIR,
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


def test_every_cost_rate_carries_source_and_verification() -> None:
    schedule = load_cost_schedule()
    assert "verified_at" in schedule
    current = schedule["versions"][0]["components"]
    for name, component in current.items():
        if name == "note" or not isinstance(component, dict):
            continue
        assert component.get("source"), name
        assert component.get("verification"), name


def test_known_verified_rates() -> None:
    components = load_cost_schedule()["versions"][0]["components"]
    assert components["brokerage"]["cap_per_order_inr"] == 5.00
    assert components["stt"]["rate_pct"] == 0.025 and components["stt"]["sides"] == ["sell"]
    assert components["exchange_transaction_fee_nse"]["rate_pct"] == 0.00307
    assert components["exchange_transaction_fee_bse"]["applies_to_this_research"] is False
    assert components["sebi_turnover_fee"]["rate_per_crore_inr"] == 10
    # stamp duty must keep the dual lower-of form, not a bare percentage
    assert components["stamp_duty"]["percent"] == 0.003
    assert components["stamp_duty"]["absolute_per_crore_inr"] == 300
    assert components["stamp_duty"]["sides"] == ["buy"]
    assert components["gst"]["rate_pct"] == 18
    assert components["ipft_and_other_levies"]["gst_treatment"] == (
        "UNVERIFIED_CONTRACT_NOTE_CHECK"
    )
    assert components["ipft_and_other_levies"]["included_by_default"] is False


def test_derivatives_stt_change_is_recorded_out_of_scope() -> None:
    notes = load_cost_schedule()["out_of_scope_notes"]
    assert "2026-04-01" in notes["derivatives_stt_change_2026-04-01"]


def test_jev_model_is_pinned_and_contract_recorded() -> None:
    data = load_yaml(CONFIG_DIR / "experiments" / "jev_v1.yaml")
    assert data["models"]["confirmatory_model_id"] == "jev-1.13.0"
    assert data["models"]["moving_alias_allowed_in_confirmatory_replay"] is False
    assert data["api"]["contract_status"] == "VERIFIED"
    assert data["api"]["request_fields"] == ["state", "model", "questions"]
    assert data["api"]["response_fields"] == ["model", "answers", "usage"]
    assert data["api"]["usage_fields"] == ["input_tokens", "output_tokens"]
    assert data["batching"]["array_state_is_a_batch_of_cases"] is False
    assert data["reliability"]["retry_status_codes"] == [429, 529]
    assert data["reliability"]["retry_other_failures"] is False
    assert data["scoring"]["confidence_is_not_a_probability"] is True


def test_instrument_tokens_are_not_invented() -> None:
    instruments = load_universe()["instruments"]
    assert len(instruments) == 20
    assert len({i["symbol"] for i in instruments}) == 20
    assert all(i["instrument_token"] is None for i in instruments)
