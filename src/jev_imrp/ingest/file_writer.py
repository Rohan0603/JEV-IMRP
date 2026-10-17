"""Bounded-batch Parquet persistence, rotation and a durable manifest.

Plan section 5.5 rules this module implements:

* raw messages are appended to Parquet in bounded batches, never one file per
  message;
* rotation by row count, byte size and wall-clock age;
* a file is complete only after write + close succeeded: the batch is written to
  a temporary name, closed, fsynced and then atomically renamed, and only then
  is an entry appended to the manifest;
* the manifest is an append-only JSONL log, fsynced per entry, carrying counts,
  time ranges, checksums and parser/schema versions;
* restart detects unfinalized temporary files and quarantines them explicitly
  rather than reading or deleting them;
* no existing data is ever overwritten or deleted.

Partitioning is ``trading_date=<IST date>`` of the receive timestamp, plus feed
type. Partition by symbol only if measurement later shows it helps.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from jev_imrp.common.timeutils import ist_session_date
from jev_imrp.common.version import PARSER_VERSION, RAW_EVENT_SCHEMA_VERSION
from jev_imrp.ingest.raw_event import RawEvent, empty_table, table_from_events

FEED_TYPE = "shoonya_market_feed"
#: Suffix of an in-progress file. Anything ending in it is unfinalized.
TEMP_SUFFIX = ".parquet.tmp"
MANIFEST_NAME = "manifest.jsonl"
QUARANTINE_DIR = "_quarantine"


@dataclass(frozen=True, slots=True)
class WriterSettings:
    """Rotation thresholds. Bounded batches by construction."""

    max_rows_per_file: int = 50_000
    max_bytes_per_file: int = 64 * 1024 * 1024
    max_seconds_per_file: float = 300.0


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    """One completed file. Appended only after the file is durable."""

    path: str
    session_id: str
    subscription_generation: int
    row_count: int
    first_received_utc: str
    last_received_utc: str
    trading_date: str
    feed_type: str
    bytes: int
    sha256: str
    parser_version: str
    schema_version: str
    finalized_at_utc: str
    first_sequence: int
    last_sequence: int

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)


@dataclass
class _OpenBatch:
    events: list[RawEvent] = field(default_factory=list)
    started_monotonic: float = 0.0
    path: Path | None = None

    def approximate_bytes(self) -> int:
        return sum(len(e.raw_payload_json) for e in self.events)


def _fsync_path(path: Path) -> None:
    """Flush file contents to stable storage.

    Opened read-write because Windows refuses ``fsync`` on a read-only handle.
    Files here are always ours to rewrite only while unfinalized.
    """
    fd = os.open(path, os.O_RDWR)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class RawEventWriter:
    """Append-only, bounded-batch Parquet writer with a durable manifest."""

    def __init__(self, root: str | os.PathLike[str], session_id: str,
                 settings: WriterSettings | None = None,
                 generation: int = 1) -> None:
        self.root = Path(root)
        self.session_id = session_id
        self.settings = settings or WriterSettings()
        self.generation = generation
        self.root.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.root / MANIFEST_NAME
        self._batch = _OpenBatch(started_monotonic=time.monotonic())
        self._file_seq = self._initial_file_seq()
        self.closed = False

    def _initial_file_seq(self) -> int:
        """Continue numbering after restart so file names never collide."""
        highest = 0
        for path in self.root.rglob("*.parquet*"):
            stem = path.name.split("-")[-1].removesuffix(".parquet").removesuffix(".tmp")
            if stem.isdigit():
                highest = max(highest, int(stem))
        return highest + 1

    # -- partition path -----------------------------------------------------
    def partition_dir(self, when: datetime) -> Path:
        day = ist_session_date(when).isoformat()
        return self.root / f"feed_type={FEED_TYPE}" / f"trading_date={day}"

    # -- writing ------------------------------------------------------------
    def write(self, event: RawEvent) -> Path | None:
        """Buffer one event, rotating when any bound is exceeded.

        Returns the finalized path when this write triggered a rotation.
        """
        if self.closed:
            raise RuntimeError("writer is closed; create a new writer after finalize()")
        self._batch.events.append(event)
        if self._should_rotate():
            return self._rotate()
        return None

    def _should_rotate(self) -> bool:
        batch = self._batch
        if len(batch.events) >= self.settings.max_rows_per_file:
            return True
        if batch.approximate_bytes() >= self.settings.max_bytes_per_file:
            return True
        # A single event never forms a batch on its own, however old it is.
        return len(batch.events) > 1 and (
            time.monotonic() - batch.started_monotonic >= self.settings.max_seconds_per_file
        )

    def _rotate(self) -> Path:
        events = self._batch.events
        if not events:
            raise RuntimeError("rotate with an empty batch")
        partition = self.partition_dir(events[-1].received_timestamp_utc)
        partition.mkdir(parents=True, exist_ok=True)
        name = f"part-{self.generation:03d}-{self._file_seq:06d}.parquet"
        final = partition / name
        temp = final.with_suffix(".parquet.tmp")
        if final.exists() or temp.exists():
            raise FileExistsError(f"refusing to overwrite existing raw file: {final}")
        self._write_durably(events, temp, final)
        self._file_seq += 1
        self._batch = _OpenBatch(started_monotonic=time.monotonic())
        return final

    def _write_durably(self, events: list[RawEvent], temp: Path, final: Path) -> ManifestEntry:
        table = table_from_events(events)
        with pq.ParquetWriter(temp, table.schema, compression="zstd") as sink:
            sink.write_table(table)
        _fsync_path(temp)
        size = temp.stat().st_size
        checksum = _sha256(temp)
        os.replace(temp, final)
        _fsync_dir(final.parent)
        entry = ManifestEntry(
            path=final.relative_to(self.root).as_posix(),
            session_id=self.session_id,
            subscription_generation=self.generation,
            row_count=len(events),
            first_received_utc=events[0].received_timestamp_utc.isoformat(),
            last_received_utc=events[-1].received_timestamp_utc.isoformat(),
            trading_date=ist_session_date(events[0].received_timestamp_utc).isoformat(),
            feed_type=FEED_TYPE,
            bytes=size,
            sha256=checksum,
            parser_version=PARSER_VERSION,
            schema_version=RAW_EVENT_SCHEMA_VERSION,
            finalized_at_utc=datetime.now(UTC).isoformat(),
            first_sequence=events[0].sequence,
            last_sequence=events[-1].sequence,
        )
        self._append_manifest(entry)
        return entry

    def _append_manifest(self, entry: ManifestEntry) -> None:
        with self.manifest_path.open("a", encoding="utf-8") as handle:
            handle.write(entry.to_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def flush_batch(self) -> Path | None:
        """Write out the open batch without closing the writer.

        Used on SIGINT/SIGTERM so a completed batch is durable immediately; a
        file is still only manifest-listed after write + close succeed.
        """
        if self.closed or not self._batch.events:
            return None
        return self._rotate()

    def finalize(self) -> Path | None:
        """Flush the open batch and close. Safe to call twice."""
        if self.closed:
            return None
        self.closed = True
        if not self._batch.events:
            return None
        return self._rotate()

    @property
    def pending_rows(self) -> int:
        return len(self._batch.events)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fsync_dir(path: Path) -> None:
    """Flush a directory entry where the platform supports it.

    POSIX needs this so the rename is durable; Windows has no directory fsync, so
    the rename is flushed with the parent directory handle best-effort. Data
    durability does not depend on it: the file itself is already fsynced before
    the rename.
    """
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


# --- manifest reading, restart detection and recovery -----------------------
def read_manifest(root: str | os.PathLike[str]) -> list[ManifestEntry]:
    """All durable manifest entries, in write order. Missing manifest -> empty."""
    path = Path(root) / MANIFEST_NAME
    if not path.exists():
        return []
    entries: list[ManifestEntry] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                entries.append(ManifestEntry(**json.loads(line)))
    return entries


def verify_manifest(root: str | os.PathLike[str]) -> list[str]:
    """Human-readable problems: missing files, size or checksum mismatch."""
    base = Path(root)
    problems: list[str] = []
    for entry in read_manifest(base):
        path = base / entry.path
        if not path.exists():
            problems.append(f"missing file: {entry.path}")
            continue
        if path.stat().st_size != entry.bytes:
            problems.append(f"size mismatch: {entry.path}")
        elif _sha256(path) != entry.sha256:
            problems.append(f"checksum mismatch: {entry.path}")
    return problems


def find_unfinalized(root: str | os.PathLike[str]) -> list[Path]:
    """Temporary files present after a stop: written but never finalized.

    Anything already in the quarantine directory is excluded; those files have
    been dealt with explicitly and must not be reported again.
    """
    return sorted(
        path
        for path in Path(root).rglob(f"*{TEMP_SUFFIX}")
        if QUARANTINE_DIR not in path.parts
    )


def quarantine_unfinalized(root: str | os.PathLike[str]) -> list[Path]:
    """Move unfinalized temp files into ``_quarantine/``; never delete them.

    Called on startup so a hard-killed run cannot be mistaken for a complete
    capture, and so its contents stay inspectable and recoverable by hand.
    """
    moved: list[Path] = []
    base = Path(root)
    for temp in find_unfinalized(base):
        destination = base / QUARANTINE_DIR / temp.parent.name / temp.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temp, destination)
        moved.append(destination)
    return moved


def read_raw_events(
    root: str | os.PathLike[str], manifest: list[ManifestEntry] | None = None
) -> pa.Table:
    """Read finalized raw events back, ordered by (generation, sequence).

    Only manifest-listed files are read, so quarantined or half-written data can
    never enter a replay.
    """
    base = Path(root)
    entries = manifest if manifest is not None else read_manifest(base)
    if not entries:
        return empty_table()
    table = pa.concat_tables([pq.read_table(base / e.path) for e in entries])
    return table.sort_by([("subscription_generation", "ascending"), ("sequence", "ascending")])


def iter_raw_events(
    root: str | os.PathLike[str], manifest: list[ManifestEntry] | None = None
) -> Iterator[dict[str, Any]]:
    """Row dicts of finalized raw events in replay order."""
    table = read_raw_events(root, manifest)
    yield from table.to_pylist()
