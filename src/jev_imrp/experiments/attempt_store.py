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
from pathlib import Path
from typing import Any

from jev_imrp.common.logging import get_logger
from jev_imrp.models.jev_client import Attempt

_LOG = get_logger(__name__)

ATTEMPT_STORE_SCHEMA_VERSION = "jev_attempts_v1"


class AttemptStore:
    """Append-only JSONL store of :class:`Attempt` records."""

    schema_version = ATTEMPT_STORE_SCHEMA_VERSION

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seen: set[str] = set()
        if self.path.exists():
            with self.path.open(encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        self._seen.add(json.loads(line)["attempt_id"])
                    except (ValueError, KeyError):
                        _LOG.warning("attempt store: skipping unreadable line in %s", self.path)

    def append(self, attempt: Attempt) -> bool:
        """Append one attempt. Returns False when that attempt id already exists."""
        if attempt.attempt_id in self._seen:
            return False
        record = {"schema_version": self.schema_version, **attempt.to_record()}
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical_line(record))
            handle.flush()
            os.fsync(handle.fileno())
        self._seen.add(attempt.attempt_id)
        return True

    def attempts(self) -> list[Attempt]:
        """Every stored attempt, in write order, rebuilt as :class:`Attempt`."""
        if not self.path.exists():
            return []
        out: list[Attempt] = []
        with self.path.open(encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                record = json.loads(line)
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
