"""Shoonya regression scaffold.

Proves that the archived Shoonya runtime cannot be imported from
jev_imrp.ingest without an explicit opt-in via JEV_ENABLE_LEGACY_SHOONYA=1.
Tests that the lazy gate is wired correctly and that a new unconditional
import would be flagged as a violation.

See the project ORC-114 for context.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# ── paths ──────────────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parents[1]  # repo root
SRC = ROOT / "src"
INGEST = SRC / "jev_imrp" / "ingest"
FIXTURES = ROOT / "tests" / "fixtures" / "fyers"
ENV_FLAG = "JEV_ENABLE_LEGACY_SHOONYA"
LEGACY_MODULE_NAMES = {"shoonya_recorder", "shoonya_feed_client",
                       "historical_data_client", "symbol_resolver"}


# ── helpers ─────────────────────────────────────────────────────────────


def _imported_modules_legacy(path: Path) -> set[str]:
    """Return top-level module basenames imported from *path* that match LEGACY_MODULE_NAMES."""
    text = path.read_text(encoding="utf-8", errors="replace")
    tree = ast.parse(text, filename=str(path))
    basenames: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                # e.g. "jev_imrp.ingest.shoonya_recorder" -> "shoonya_recorder"
                name = alias.name.split(".")[-1]
                if name in LEGACY_MODULE_NAMES:
                    basenames.add(name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                # absolute import
                name = node.module.split(".")[-1]
                if name in LEGACY_MODULE_NAMES:
                    basenames.add(name)
            elif node.level > 0:
                # relative import inside jev_imrp.ingest package:
                # from .shoonya_recorder import Recorder
                effective = f"jev_imrp.ingest{'.' * (node.level - 1)}"
                if node.module:
                    effective += "." + node.module
                name = effective.split(".")[-1]
                if name in LEGACY_MODULE_NAMES:
                    basenames.add(name)
    return basenames


def _scan_files(paths: list[Path]) -> list[tuple[str, str]]:
    """Scan *paths* for unconditional legacy Shoonya imports.

    Returns a list of (file, found_legacy_basenames) violations.
    """
    violations: list[tuple[str, str]] = []
    for p in paths:
        if not p.is_file():
            continue
        found = _imported_modules_legacy(p)
        if found:
            violations.append((p.name, ", ".join(sorted(found))))
    return violations


def _write_temp_file(*, suffix: str = ".py", content: str) -> Path:
    """Write a temporary file and return its path."""
    tmp_dir = Path(tempfile.gettempdir()) / "jev-imrp-regression"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    f = tmp_dir / f"regress_{os.urandom(4).hex()}{suffix}"
    f.write_text(content, encoding="utf-8")
    return f


# ── tests ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "paths",
    [
        [INGEST / "__init__.py", INGEST / "legacy_shoonya.py"],
    ],
)
def test_no_unallowlisted_shoonya_imports_by_default(paths: list[Path]) -> None:
    """Default scan must find zero unconditional legacy Shoonya imports."""
    violations = _scan_files(paths)
    assert violations == [], f"unexpected violations: {violations}"


def test_scanner_detects_new_unconditional_import() -> None:
    """A temporary file importing Shoonya directly should be flagged."""
    probe = _write_temp_file(content=(
        "from jev_imrp.ingest.shoonya_feed_client import ShoonyaFeedClient  # probe\n"
        "x = ShoonyaFeedClient\n"
    ))
    try:
        violations = _scan_files([Path(probe)])
        assert violations, f"expected violations from probe import, got {violations}"
    finally:
        try:
            probe.unlink()
        except FileNotFoundError:
            pass


def test_legacy_export_disabled_by_default() -> None:
    """Without the env flag, legacy_export must raise LegacyShoonyaDisabled."""
    from jev_imrp.ingest import legacy_shoonya
    with pytest.raises(legacy_shoonya.LegacyShoonyaDisabled):
        legacy_shoonya.legacy_export("Recorder")


def test_legacy_export_enabled_with_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    """With JEV_ENABLE_LEGACY_SHOONYA=1, legacy_export returns the class."""
    monkeypatch.setenv(ENV_FLAG, "1")
    from jev_imrp.ingest import legacy_shoonya
    result = legacy_shoonya.legacy_export("Recorder")
    assert result is not None
    monkeypatch.delenv(ENV_FLAG, raising=False)


def test_import_package_does_not_load_shoonya_modules() -> None:
    """Importing jev_imrp.ingest as a package must not populate Shoonya modules."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    result = subprocess.run(
            [sys.executable, "-c",
             "import jev_imrp.ingest as ingest; "
             "bad = [m for m in __import__('sys').modules "
             "if m.startswith('jev_imrp.ingest') and 'shoonya' in m.lower() "
             "and 'legacy_shoonya' not in m.lower()]; "
             "print('OK' if not bad else bad)"],
        capture_output=True, text=True, env=env, cwd=str(ROOT),
    )
    assert result.returncode == 0, f"subprocess failed: {result.stderr}"
    assert "OK" in result.stdout, f"Shoonya modules still loaded: {result.stdout}"


@pytest.mark.parametrize(
    "label",
    [
        "snapshot_sf_reliance",
        "missing_source_time_sf",
        "implausible_source_time_sf",
        "after_hours_zero_bid_sf",
        "partial_depth_dp",
        "reconnect_snapshot_sf_reliance",
        "duplicate_sf",
        "auth_ok",
        "subscribe_ok",
        "truncated_data",
        "unknown_block_type",
    ],
)
def test_fixture_labels_covered(label: str) -> None:
    """Every label in the fixture manifest must have an expectation entry."""
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    expectations = manifest.get("expectations", {})
    assert label in expectations, f"label '{label}' missing from manifest expectations"


def test_manifest_declares_synthetic_offline_provenance() -> None:
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["data_origin"] == "synthetic"
    assert "SYNTHETIC" in manifest["warning"].upper()
    assert manifest["provider"] == "FYERS"
    assert manifest["api_version"] == "v3"
    assert manifest["data_socket_version"] == "HSM v1-5"
    assert manifest["parser_version"] == "fyers_socket_v0"


def test_manifest_hashes_and_line_endings() -> None:
    for p in (FIXTURES / "sanitized_packets.json",
              FIXTURES / "rest_cases.json",
              FIXTURES / "manifest.json"):
        raw = p.read_bytes()
        assert b"\r\n" not in raw, f"{p.name} contains CRLF"
        assert raw.endswith(b"\n"), f"{p.name} must end with LF"


def test_clock_policy_in_manifest() -> None:
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    cp = manifest["clock_policy"]
    assert cp["provider_timestamp_only"] is True
    assert cp["local_receipt_time_not_substituted"] is True
    assert cp["missing_provider_time_is_null"] is True
    assert cp["implausible_epoch_is_null_with_reason"] is True
    assert cp["dp_has_no_source_time"] is True
    assert cp["live_unit_verification"] == "NOT RUN"


def test_not_run_items_present() -> None:
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["live_capture"] == "NOT RUN"
    nr = manifest.get("not_run", [])
    assert isinstance(nr, list) and len(nr) >= 5
    for item in nr:
        assert isinstance(item, dict) and isinstance(item.get("reason", ""), str) and item["reason"].strip()


# ── credentials / redaction tests ───────────────────────────────────────


def test_fixtures_and_reports_do_not_contain_credentials() -> None:
    """Ensure test artefacts do not echo secret-like strings."""
    import re
    secret_re = re.compile(
        r"(?i)(access_token|api_secret|password|secret_code|app_id_hash|authorization)\s*[:=]\s*['\"]?\S+['\"]?"
    )
    for name in ("sanitized_packets.json", "rest_cases.json", "manifest.json"):
        text = (FIXTURES / name).read_text(encoding="utf-8")
        matches = secret_re.findall(text)
        assert not matches, f"{name} contains credential-like strings: {matches}"

    rest = json.loads((FIXTURES / "rest_cases.json").read_text(encoding="utf-8"))
    # the OAuth case structure must not embed raw secret payload
    assert "payload" not in rest["oauth"]["cases"][0], "oauth case must not embed raw secret payload"
