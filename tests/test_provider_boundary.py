from __future__ import annotations

import ast
from pathlib import Path

ACTIVE_INGEST = Path(__file__).parents[1] / "src" / "jev_imrp" / "ingest"
ACTIVE_SOURCE = Path(__file__).parents[1] / "src"


def test_active_ingest_has_no_retired_broker_import_edges() -> None:
    violations: list[str] = []
    for path in ACTIVE_INGEST.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imported = ""
            if isinstance(node, ast.Import):
                imported = ",".join(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported = node.module or ""
            if any(term in imported.lower() for term in ("legacy_broker", "noren", "tpseries")):
                violations.append(f"{path.name}:{imported}")

    assert violations == []


def test_active_source_has_no_retired_broker_import_edges() -> None:
    violations: list[str] = []
    for path in ACTIVE_SOURCE.rglob("*.py"):
        if "legacy_broker" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            imported = ""
            if isinstance(node, ast.Import):
                imported = ",".join(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported = node.module or ""
            if any(term in imported.lower() for term in ("legacy_broker", "noren", "tpseries")):
                violations.append(f"{path.relative_to(ACTIVE_SOURCE)}:{imported}")

    assert violations == []


def test_active_writer_partition_is_not_provider_specific() -> None:
    from jev_imrp.ingest.file_writer import FEED_TYPE

    assert FEED_TYPE == "market_data"
