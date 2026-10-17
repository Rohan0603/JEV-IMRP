"""Structured logging with credential redaction (plan 14.3).

Rule: secrets are read from the environment and must never reach a log line.
The filter below is a backstop for the case where a caller logs a dict or an
exception string that happens to contain one.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

#: Environment/settings names whose values are secrets.
SECRET_NAMES: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "secret",
        "api_key",
        "apikey",
        "client_secret",
        "access_token",
        "refresh_token",
        "ttoken",
        "susertoken",
        "private_key",
        "session_token",
    }
)

_REDACTED = "***REDACTED***"

# value patterns, applied to any log text
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"(?i)\b(pass(?:word|wd)?|secret|api[_-]?key|access[_-]?token|refresh[_-]?token"
            r"|su[_-]?user[_-]?token|t[_-]?token|session[_-]?token|token|authorization)\b"
            r"(\s*[:=]\s*|\s+)(\S+)"
        ),
        r"\1\2" + _REDACTED,
    ),
    (
        re.compile(r"(?i)\bbearer\s+(\S+)"),
        "bearer " + _REDACTED,
    ),
)


def redact(text: str) -> str:
    """Remove anything that looks like a credential from free text."""
    out = text
    for pattern, replacement in _PATTERNS:
        out = pattern.sub(replacement, out)
    return out


class RedactingFilter(logging.Filter):
    """Redacts secrets from every record's message and arguments."""

    def filter(self, record: logging.LogRecord) -> bool:
        # Scrub secret-keyed dict arguments before interpolation, then redact the
        # fully formatted message. Rewriting only the message would break %-format.
        if isinstance(record.args, dict):
            record.args = {
                k: (_REDACTED if self._is_secret_key(k) and v is not None else v)
                for k, v in record.args.items()
            }
        record.msg = redact(record.getMessage())
        record.args = ()
        if record.exc_text:
            record.exc_text = redact(record.exc_text)
        return True

    @staticmethod
    def _is_secret_key(key: Any) -> bool:
        return isinstance(key, str) and key.strip().lower() in SECRET_NAMES


def configure_logging(level: int | str | None = None) -> None:
    """Install a single stderr handler with redaction. Idempotent."""
    root = logging.getLogger()
    if not any(getattr(h, "_jev", False) for h in root.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        handler.addFilter(RedactingFilter())
        handler._jev = True  # type: ignore[attr-defined]
        root.addHandler(handler)
    root.setLevel(level if level is not None else os.environ.get("JEV_LOG_LEVEL", "INFO"))


def get_logger(name: str) -> logging.Logger:
    """Return a module logger with redaction already installed.

    The filter sits on the logger as well as the handler so that any handler
    added later (a test capture, a file handler) still sees redacted text.
    """
    configure_logging()
    logger = logging.getLogger(name)
    if not any(isinstance(f, RedactingFilter) for f in logger.filters):
        logger.addFilter(RedactingFilter())
    return logger
