"""Validated configuration loading.

Contract:
  * a missing required setting fails immediately at startup, by name, with no
    value echoed;
  * secrets are never read from these files (they come from the environment) and
    never appear in an error message;
  * a value still marked ``TBD`` or ``UNVERIFIED`` can be detected, and callers
    that need trustworthy numbers are expected to refuse it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Final

import yaml

from jev_imrp.common.errors import ConfigError, MissingSettingError

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
CONFIG_DIR: Final = REPO_ROOT / "config"

#: Substrings marking a value as not yet trustworthy.
UNRESOLVED_MARKERS: Final = ("TBD", "UNVERIFIED")


def config_path(relative: str | Path) -> Path:
    """Resolve a path relative to the repository root."""
    path = Path(relative)
    return path if path.is_absolute() else REPO_ROOT / path


def load_yaml(path: str | Path) -> dict[str, Any]:
    """Load one YAML file, returning a mapping. Raises ConfigError otherwise."""
    resolved = config_path(path)
    try:
        raw = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read config file: {resolved}") from exc
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ConfigError(f"invalid YAML in {resolved}: {exc.__class__.__name__}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{resolved}: top level must be a mapping")
    return data


def _dig(data: dict[str, Any], dotted: str) -> Any:
    node: Any = data
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


def require_settings(data: dict[str, Any], required: list[str], config_name: str) -> dict[str, Any]:
    """Return the requested settings, raising if any is absent or null.

    Only key names appear in the message; values are never echoed.
    """
    resolved = {key: _dig(data, key) for key in required}
    missing = sorted(key for key, value in resolved.items() if value is None)
    if missing:
        raise MissingSettingError(config_name, missing)
    return resolved


def is_unresolved(data: Any) -> bool:
    """True when a value is null or carries a TBD/UNVERIFIED marker."""
    if data is None:
        return True
    if isinstance(data, str):
        return any(marker in data for marker in UNRESOLVED_MARKERS)
    if isinstance(data, dict):
        return any(is_unresolved(v) for v in data.values())
    if isinstance(data, list):
        return any(is_unresolved(v) for v in data)
    return False


def load_required_config(
    relative: str | Path,
    required: list[str],
    *,
    config_name: str | None = None,
) -> dict[str, Any]:
    """Load a YAML config, require the given dotted settings, return the whole file."""
    resolved = config_path(relative)
    name = config_name or resolved.name
    data = load_yaml(resolved)
    require_settings(data, required, name)
    return data


def load_universe() -> dict[str, Any]:
    """Validated universe config (plan section 2.3)."""
    return load_required_config(
        CONFIG_DIR / "universe.yaml",
        ["universe_version", "exchange", "eligibility_rule", "instruments"],
    )


def load_cost_schedule() -> dict[str, Any]:
    """Cost schedule config (plan section 8.4)."""
    return load_required_config(
        CONFIG_DIR / "cost_schedule.yaml",
        ["cost_schedule_version", "versions"],
    )


def load_execution_policy() -> dict[str, Any]:
    """Trading policy config (plan sections 8.2 / 8.3)."""
    return load_required_config(
        CONFIG_DIR / "execution_policy.yaml",
        ["policy_version", "sizing", "entry", "exit", "session"],
    )


def load_feature_config() -> dict[str, Any]:
    """Feature registry config (plan section 7.2)."""
    return load_required_config(
        CONFIG_DIR / "feature_config.yaml",
        ["feature_config_version", "features"],
    )
