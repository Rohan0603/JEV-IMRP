"""Central error types.

Errors carry the setting/field *name* only. No error in this package ever puts a
configuration value into a message, because config values may contain
credentials (plan 14.3).
"""

from __future__ import annotations


class JevError(Exception):
    """Base class for every error raised by this package."""


class ConfigError(JevError):
    """Configuration file is missing, unreadable, or structurally wrong."""


class MissingSettingError(ConfigError):
    """A required configuration setting was absent."""

    def __init__(self, config_name: str, missing: list[str]) -> None:
        self.config_name = config_name
        self.missing = list(missing)
        super().__init__(
            f"{config_name}: missing required setting(s): {', '.join(self.missing)}. "
            "Set them in the config file; do not supply them ad hoc in code."
        )


class UnverifiedValueError(ConfigError):
    """A config value is still marked UNVERIFIED/TBD and the caller demanded verified data."""


class LedgerError(JevError):
    """Trial ledger row/schema problem."""
