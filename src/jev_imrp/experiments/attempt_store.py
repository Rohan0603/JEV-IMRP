"""Durable attempt store (plan 9.4).

Every JEV attempt -- success, timeout, HTTP error, malformed response -- is
appended here and stays visible. Silent dropping of failed calls is the exact
failure mode the plan calls out, so this store is append-only and never updates
a row: a retry appends a new attempt with its own id.

Idempotency: an ``attempt_id`` is written at most once. Because attempt ids are
derived from ``(experiment_id, candidate_id, attempt_number)``, a rerun of the
same candidate rewrites the same ids and is a no-op on an already-populated
store, which is what makes a rerun idempotent instead of duplicating outcomes.

Format is JSON Lines: one self-describing record per line, cheap to append,
greppable, and it never needs a migration to add a field.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from jev_imrp.common.logging import get_logger
from jev_imrp.models.jev_client import Attempt

_LOG = get_logger(__name__)

ATTEMPT_STORE_SCHEMA_VERSION = "jev_attempts_v1"


class AttemptStoreCorruptionError(ValueError):
    """The append-only store contains a malformed record; fail closed."""


class AttemptStore:
    """Append-only JSONL store of :class:`Attempt` records."""

    schema_version = ATTEMPT_STORE_SCHEMA_VERSION

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock_path = self.path.with_name(f".{self.path.name}.lock")
        self._seen: set[str] = set()
        self._records: dict[str, str] = {}
        with self._exclusive_lock():
            self._reload_locked()

    def append(self, attempt: Attempt) -> bool:
        """Append one attempt. Returns False when that attempt id already exists."""
        record = {"schema_version": self.schema_version, **attempt.to_record()}
        encoded = canonical_line(record)
        with self._exclusive_lock():
            self._reload_locked()
            if attempt.attempt_id in self._seen:
                if self._records[attempt.attempt_id] != encoded:
                    raise AttemptStoreCorruptionError(
                        f"conflicting attempt record for id {attempt.attempt_id!r}"
                    )
                return False
            with self.path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            self._seen.add(attempt.attempt_id)
            self._records[attempt.attempt_id] = encoded
            return True

    def attempts(self) -> list[Attempt]:
        """Every stored attempt, in write order, rebuilt as :class:`Attempt`."""
        with self._exclusive_lock():
            return self._read_attempts_locked()

    def for_candidate(self, candidate_id: str) -> list[Attempt]:
        return [a for a in self.attempts() if a.candidate_id == candidate_id]

    def __len__(self) -> int:
        return len(self._seen)

    def _reload_locked(self) -> None:
        self._seen.clear()
        self._records.clear()
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    attempt_id = record["attempt_id"]
                except (json.JSONDecodeError, KeyError, TypeError) as exc:
                    raise AttemptStoreCorruptionError(
                        f"attempt store is corrupt at line {line_number}; recover or replace it"
                    ) from exc
                self._seen.add(attempt_id)
                self._records[attempt_id] = canonical_line(record)

    def _read_attempts_locked(self) -> list[Attempt]:
        if not self.path.exists():
            return []
        out: list[Attempt] = []
        with self.path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    record.pop("schema_version", None)
                    out.append(Attempt.from_record(record))
                except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                    raise AttemptStoreCorruptionError(
                        f"attempt store is corrupt at line {line_number}; recover or replace it"
                    ) from exc
        return out

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        self._lock_path.touch(exist_ok=True)
        with self._lock_path.open("a+b") as lock_handle:
            lock_handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lock_handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)  # type: ignore[attr-defined]
            try:
                yield
            finally:
                if os.name == "nt":
                    msvcrt.locking(lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]


def canonical_line(record: dict[str, Any]) -> str:
    """One stable JSON line: sorted keys, so the file diffs cleanly."""
    return json.dumps(record, sort_keys=True, separators=(",", ":"), default=str) + "\n"
