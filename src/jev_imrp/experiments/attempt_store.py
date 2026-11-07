"""Durable attempt store (plan 9.4).

Every JEV attempt -- success, timeout, HTTP error, malformed response -- is
appended here and stays visible. Silent dropping of failed calls is the exact
failure mode the plan calls out, so this store is append-only and never updates a
row: a retry appends a new attempt with its own id.

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
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from jev_imrp.common.logging import get_logger
from jev_imrp.models.jev_client import Attempt

_LOG = get_logger(__name__)

ATTEMPT_STORE_SCHEMA_VERSION = "jev_attempts_v1"
_LOCKS: dict[Path, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


class AttemptStoreCorruptionError(RuntimeError):
    """The append-only store contains a malformed record; fail closed."""


class AttemptStore:
    """Append-only JSONL store of :class:`Attempt` records."""

    schema_version = ATTEMPT_STORE_SCHEMA_VERSION

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seen: set[str] = set(self._read_records())

    @property
    def _lock_path(self) -> Path:
        return self.path.with_name(f".{self.path.name}.lock")

    def _thread_lock(self) -> threading.RLock:
        key = self.path.resolve()
        with _LOCKS_GUARD:
            return _LOCKS.setdefault(key, threading.RLock())

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        with self._thread_lock(), self._lock_path.open("a+b") as lock_handle:
            if lock_handle.seek(0, os.SEEK_END) == 0:
                lock_handle.write(b"\0")
                lock_handle.flush()
            if os.name == "nt":
                import msvcrt

                lock_handle.seek(0)
                msvcrt.locking(lock_handle.fileno(), msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    import msvcrt

                    lock_handle.seek(0)
                    msvcrt.locking(lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)

    def _read_records(self) -> list[str]:
        ids: list[str] = []
        if not self.path.exists():
            return ids
        with self.path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    attempt_id = record["attempt_id"]
                except (ValueError, TypeError, KeyError) as exc:
                    raise AttemptStoreCorruptionError(
                        f"malformed attempt record at {self.path}:{line_number}"
                    ) from exc
                if not isinstance(attempt_id, str):
                    raise AttemptStoreCorruptionError(
                        f"invalid attempt_id at {self.path}:{line_number}"
                    )
                ids.append(attempt_id)
        return ids

    def append(self, attempt: Attempt) -> bool:
        """Append one attempt. Returns False when that attempt id already exists."""
        with self._exclusive_lock():
            self._seen = set(self._read_records())
            if attempt.attempt_id in self._seen:
                return False
            record = {"schema_version": self.schema_version, **attempt.to_record()}
            encoded = canonical_line(record).encode("utf-8")
            with self.path.open("ab") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            self._seen.add(attempt.attempt_id)
            return True

    def attempts(self) -> list[Attempt]:
        """Every stored attempt, in write order, rebuilt as :class:`Attempt`."""
        if not self.path.exists():
            return []
        out: list[Attempt] = []
        with self._exclusive_lock(), self.path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except (ValueError, TypeError) as exc:
                    raise AttemptStoreCorruptionError(
                        f"malformed attempt record in {self.path}"
                    ) from exc
                record.pop("schema_version", None)
                out.append(Attempt.from_record(record))
        return out

    def for_candidate(self, candidate_id: str) -> list[Attempt]:
        return [a for a in self.attempts() if a.candidate_id == candidate_id]

    def __len__(self) -> int:
        return len(self._seen)


def canonical_line(record: dict[str, Any]) -> str:
    """One stable JSON line: sorted keys, so the file diffs cleanly."""
    return json.dumps(record, sort_keys=True, separators=(",", ":"), default=str) + "\n"
