from __future__ import annotations

import json
from pathlib import Path

from jev_imrp.ingest.fyers_socket import normalize_fyers_message, parse_fyers_message

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "fyers"


def test_sanitized_fyers_fixture_manifest_is_network_and_credential_free() -> None:
    manifest = json.loads((FIXTURE_ROOT / "manifest.json").read_text(encoding="utf-8"))

    assert manifest == {
        "fixture_schema": "jev_fyers_fixture_v1",
        "provider": "fyers",
        "source": "synthetic_sanitized_offline_fixture",
        "credentials": False,
        "network": False,
        "order_endpoints": False,
        "cases": manifest["cases"],
    }
    assert manifest["cases"]


def test_manifest_cases_parse_to_expected_canonical_message_types() -> None:
    manifest = json.loads((FIXTURE_ROOT / "manifest.json").read_text(encoding="utf-8"))

    for case in manifest["cases"]:
        message = parse_fyers_message(
            (FIXTURE_ROOT / case["file"]).read_text(encoding="utf-8")
        )
        normalized = normalize_fyers_message(message)
        assert message["type"] == case["type"]
        assert normalized["t"] == case["type"]
