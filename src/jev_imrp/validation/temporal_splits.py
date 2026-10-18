"""Session-based chronological splits and walk-forward folds (plan 10.1).

The split unit is a COMPLETE trading session (an IST calendar date that appears
in the data). Because the partition is a pure function of the session date, all
symbols and all timestamps of a session - including every row produced by one
market event - land on the same side of a boundary. Grouping is therefore a
property of the data structure, not a convention a caller must remember.

Purge/embargo of the rows near a boundary lives in :mod:`jev_imrp.validation.purge`
and is applied by :meth:`Fold.training_rows`.

Walk-forward folds are EXPANDING windows: fold *k* trains on all sessions up to
the validation window and validates on the next window. Structural enforcement
of "fit on this fold's training portion only" comes from
:meth:`Fold.fit_view` / :func:`assert_fit_partition`, which raise
:class:`SplitLeakError` when a row outside the fold's training sessions reaches a
fit-time call, rather than trusting the caller to pre-filter.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from jev_imrp.common.errors import JevError
from jev_imrp.common.timeutils import (
    ensure_aware,
    ist_session_date,
    session_bounds_utc,
)
from jev_imrp.validation.purge import (
    DEFAULT_SYMBOL_KEY,
    DEFAULT_TIMESTAMP_KEY,
    PurgeConfig,
    PurgeResult,
    purge_rows,
)

SPLIT_MANIFEST_VERSION = "split_v1"


class SplitError(JevError):
    """Malformed or contradictory split request."""


class SplitLeakError(SplitError):
    """A row that may not be fitted or tuned reached a fit-time path."""


class Partition(StrEnum):
    """The three chronological partitions of the experiment."""

    TRAIN = "train"
    VALIDATION = "validation"
    TEST = "test"


#: Fixed evaluation order: every training session precedes every validation
#: session, which in turn precedes every locked-test session.
PARTITION_ORDER: tuple[Partition, ...] = (Partition.TRAIN, Partition.VALIDATION, Partition.TEST)


@dataclass(frozen=True, slots=True)
class DatasetBoundaries:
    """Exact dataset boundary of one partition."""

    part: Partition
    sessions: tuple[date, ...]
    start_utc: datetime
    end_utc: datetime

    @property
    def n_sessions(self) -> int:
        return len(self.sessions)

    def as_dict(self) -> dict[str, Any]:
        return {
            "part": str(self.part),
            "n_sessions": self.n_sessions,
            "first_session": self.sessions[0].isoformat(),
            "last_session": self.sessions[-1].isoformat(),
            "start_utc": self.start_utc.isoformat(),
            "end_utc": self.end_utc.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class ExclusionRow:
    """Observations that no partition claims, with the reason."""

    session: date | None
    reason: str
    n_rows: int
    symbols: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "session": self.session.isoformat() if self.session else None,
            "reason": self.reason,
            "n_rows": self.n_rows,
            "symbols": list(self.symbols),
        }


@dataclass(frozen=True, slots=True)
class SplitManifest:
    """Exact dataset boundaries, counts and exclusions of one split."""

    boundaries: Mapping[Partition, DatasetBoundaries]
    observation_counts: Mapping[Partition, int]
    per_symbol_counts: Mapping[Partition, Mapping[str, int]]
    excluded: tuple[ExclusionRow, ...] = ()
    purge: PurgeConfig | None = None
    embargo_sessions: int = 0
    embargo_minutes: int = 0
    version: str = SPLIT_MANIFEST_VERSION
    notes: tuple[str, ...] = ()

    def sessions(self, part: Partition) -> tuple[date, ...]:
        return self.boundaries[part].sessions

    def interval(self, part: Partition) -> tuple[datetime, datetime]:
        """(first instant, last instant) of a partition - the purge boundary."""
        bounds = self.boundaries[part]
        return bounds.start_utc, bounds.end_utc

    def as_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "boundaries": {str(part): bounds.as_dict() for part, bounds in self.boundaries.items()},
            "observation_counts": {str(k): v for k, v in self.observation_counts.items()},
            "per_symbol_counts": {
                str(part): dict(sorted(counts.items()))
                for part, counts in self.per_symbol_counts.items()
            },
            "excluded": [row.as_dict() for row in self.excluded],
            "purge_rule": self.purge.describe() if self.purge else None,
            "embargo_sessions": self.embargo_sessions,
            "embargo_minutes": self.embargo_minutes,
            "notes": list(self.notes),
        }


def make_session_partitions(
    sessions: Iterable[date],
    *,
    train_fraction: float,
    validation_fraction: float,
    test_fraction: float,
) -> dict[Partition, tuple[date, ...]]:
    """Chronological session partition by fraction of trading sessions.

    Fractions are cut from the earliest session forward; the remainder goes to
    the locked test period, so no session is silently dropped.
    """
    ordered = sorted(set(sessions))
    for name, fraction in (
        ("train_fraction", train_fraction),
        ("validation_fraction", validation_fraction),
        ("test_fraction", test_fraction),
    ):
        if not 0.0 < fraction <= 1.0:
            raise SplitError(f"{name} must be in (0, 1]")
    if train_fraction + validation_fraction + test_fraction > 1.0 + 1e-9:
        raise SplitError("fractions must not sum above 1.0")

    total = len(ordered)
    if total < 3:
        raise SplitError("at least three complete sessions are required")
    n_train = int(total * train_fraction)
    n_validation = int(total * validation_fraction)
    n_train = max(1, min(n_train, total - 2))
    n_validation = max(1, min(n_validation, total - n_train - 1))
    return {
        Partition.TRAIN: tuple(ordered[:n_train]),
        Partition.VALIDATION: tuple(ordered[n_train : n_train + n_validation]),
        Partition.TEST: tuple(ordered[n_train + n_validation :]),
    }


def _boundaries(part: Partition, sessions: Sequence[date]) -> DatasetBoundaries:
    if not sessions:
        raise SplitError(f"{part} partition has no sessions")
    ordered = sorted(set(sessions))
    start, _ = session_bounds_utc(ordered[0])
    _, end = session_bounds_utc(ordered[-1])
    return DatasetBoundaries(part=part, sessions=tuple(ordered), start_utc=start, end_utc=end)


def build_split_manifest(
    partitions: Mapping[Partition, Sequence[date]],
    rows: Iterable[Mapping[str, Any]] = (),
    *,
    purge: PurgeConfig | None = None,
    timestamp_key: str = DEFAULT_TIMESTAMP_KEY,
    symbol_key: str = DEFAULT_SYMBOL_KEY,
    notes: Sequence[str] = (),
) -> SplitManifest:
    """Exact boundaries plus per-partition observation counts and exclusions.

    A row whose session belongs to no partition is recorded as an exclusion with
    its reason, never dropped silently.
    """
    boundaries = {part: _boundaries(part, sessions) for part, sessions in partitions.items()}
    audit_split(boundaries)
    assignment = {
        session: part for part, bounds in boundaries.items() for session in bounds.sessions
    }

    counts: dict[Partition, int] = {part: 0 for part in boundaries}
    per_symbol: dict[Partition, dict[str, int]] = {part: {} for part in boundaries}
    excluded: dict[tuple[date | None, str], ExclusionRow] = {}

    for row in rows:
        stamp = row.get(timestamp_key)
        if not isinstance(stamp, datetime):
            raise SplitError(f"row has no usable {timestamp_key}")
        session = ist_session_date(ensure_aware(stamp))
        part = assignment.get(session)
        if part is None:
            key = (session, "session_not_assigned_to_any_partition")
            existing = excluded.get(key)
            symbol = str(row.get(symbol_key, ""))
            if existing is None:
                excluded[key] = ExclusionRow(
                    session=session, reason=key[1], n_rows=1, symbols=(symbol,)
                )
            else:
                symbols = (
                    existing.symbols if symbol in existing.symbols else (*existing.symbols, symbol)
                )
                excluded[key] = ExclusionRow(
                    session=session, reason=key[1], n_rows=existing.n_rows + 1, symbols=symbols
                )
            continue
        counts[part] += 1
        symbol = str(row.get(symbol_key, ""))
        per_symbol[part][symbol] = per_symbol[part].get(symbol, 0) + 1

    return SplitManifest(
        boundaries=boundaries,
        observation_counts=counts,
        per_symbol_counts=per_symbol,
        excluded=tuple(
            excluded[key] for key in sorted(excluded, key=lambda k: (k[0] or date.min, k[1]))
        ),
        purge=purge,
        embargo_sessions=purge.embargo_sessions if purge else 0,
        embargo_minutes=purge.embargo_minutes if purge else 0,
        notes=tuple(notes),
    )


def audit_split(boundaries: Mapping[Partition, DatasetBoundaries]) -> None:
    """Raise unless the split is chronological, disjoint and fully assigned."""
    present = [part for part in PARTITION_ORDER if part in boundaries]
    if present != sorted(present, key=PARTITION_ORDER.index):
        raise SplitLeakError("partitions are not in chronological order")
    seen: dict[date, Partition] = {}
    for index, part in enumerate(present):
        bounds = boundaries[part]
        if not bounds.sessions:
            raise SplitError(f"{part} partition has no sessions")
        if bounds.start_utc > bounds.end_utc:
            raise SplitError(f"{part} partition has inverted boundaries")
        for session in bounds.sessions:
            if session in seen:
                raise SplitLeakError(
                    f"session {session.isoformat()} assigned to both "
                    f"{seen[session]} and {part}: a session must be assigned exactly once"
                )
            seen[session] = part
        if index + 1 < len(present):
            nxt = boundaries[present[index + 1]]
            if bounds.sessions[-1] >= nxt.sessions[0]:
                raise SplitLeakError(
                    f"{part} session {bounds.sessions[-1]} is not before "
                    f"{present[index + 1]} session {nxt.sessions[0]}"
                )
            if bounds.end_utc > nxt.start_utc:
                raise SplitLeakError(f"{part} interval reaches into {present[index + 1]}")


@dataclass(frozen=True, slots=True)
class SessionSplitter:
    """Assigns whole sessions to partitions and exposes the boundaries."""

    manifest: SplitManifest

    def partition_for(self, session: date) -> Partition | None:
        for part, bounds in self.manifest.boundaries.items():
            if session in bounds.sessions:
                return part
        return None

    def partition_for_timestamp(self, stamp: datetime) -> Partition | None:
        return self.partition_for(ist_session_date(ensure_aware(stamp)))

    def sessions_for(self, part: Partition) -> tuple[date, ...]:
        return self.manifest.sessions(part)

    def boundaries(self, part: Partition) -> DatasetBoundaries:
        return self.manifest.boundaries[part]

    def interval(self, part: Partition) -> tuple[datetime, datetime]:
        return self.manifest.interval(part)

    def rows_for(
        self,
        rows: Iterable[Mapping[str, Any]],
        part: Partition,
        *,
        timestamp_key: str = DEFAULT_TIMESTAMP_KEY,
    ) -> tuple[Mapping[str, Any], ...]:
        """Rows of one partition, by session only."""
        sessions = set(self.sessions_for(part))
        return tuple(
            row
            for row in rows
            if isinstance(row.get(timestamp_key), datetime)
            and ist_session_date(ensure_aware(row[timestamp_key])) in sessions
        )


def assert_fit_partition(
    rows: Iterable[Mapping[str, Any]],
    allowed_sessions: Iterable[date],
    *,
    context: str = "fit",
    timestamp_key: str = DEFAULT_TIMESTAMP_KEY,
    symbol_key: str = DEFAULT_SYMBOL_KEY,
) -> None:
    """Raise unless every row belongs to ``allowed_sessions``.

    This is the structural guard for plan 10.1: imputation, scaling, encoding,
    feature selection, calibration and threshold choice must all run through it
    (or through :meth:`Fold.fit_view`, which calls it), so a validation or
    locked-holdout row cannot enter a fit-time path even by accident.
    """
    allowed = set(allowed_sessions)
    for row in rows:
        stamp = row.get(timestamp_key)
        if not isinstance(stamp, datetime):
            raise SplitLeakError(f"{context}: row has no usable {timestamp_key}")
        session = ist_session_date(ensure_aware(stamp))
        if session not in allowed:
            raise SplitLeakError(
                f"{context}: row for symbol {row.get(symbol_key, '')!r} on session "
                f"{session.isoformat()} is outside the permitted training sessions. "
                "Fit-time operations may only see the fold's training portion."
            )


@dataclass(frozen=True, slots=True)
class Fold:
    """One expanding-window walk-forward fold.

    ``train_sessions`` always starts at the first available session and grows
    with each fold; ``validation_sessions`` is the immediately following window.
    """

    index: int
    train_sessions: tuple[date, ...]
    validation_sessions: tuple[date, ...]
    embargo_sessions: int = 0
    embargo_minutes: int = 0
    horizon_minutes: int = 30
    purge_config: PurgeConfig | None = None

    @property
    def all_sessions(self) -> tuple[date, ...]:
        return self.train_sessions + self.validation_sessions

    def partition_of(self, session: date) -> Partition:
        """Which side of this fold a session belongs to."""
        if session in self.train_sessions:
            return Partition.TRAIN
        if session in self.validation_sessions:
            return Partition.VALIDATION
        raise SplitError(f"session {session.isoformat()} belongs to no fold window")

    @property
    def validation_start_utc(self) -> datetime:
        start, _ = session_bounds_utc(self.validation_sessions[0])
        return start

    @property
    def validation_end_utc(self) -> datetime:
        _, end = session_bounds_utc(self.validation_sessions[-1])
        return end

    def _config(self) -> PurgeConfig:
        if self.purge_config is not None:
            return self.purge_config
        return PurgeConfig(
            horizon_minutes=self.horizon_minutes,
            embargo_sessions=self.embargo_sessions,
            embargo_minutes=self.embargo_minutes,
        )

    def validation_rows(
        self,
        rows: Iterable[Mapping[str, Any]],
        *,
        timestamp_key: str = DEFAULT_TIMESTAMP_KEY,
    ) -> tuple[Mapping[str, Any], ...]:
        """Validation rows, untouched: nothing is purged from the held-out side."""
        sessions = set(self.validation_sessions)
        return tuple(
            row
            for row in rows
            if isinstance(row.get(timestamp_key), datetime)
            and ist_session_date(ensure_aware(row[timestamp_key])) in sessions
        )

    def training_rows(
        self,
        rows: Iterable[Mapping[str, Any]],
        *,
        timestamp_key: str = DEFAULT_TIMESTAMP_KEY,
    ) -> PurgeResult:
        """Training rows of this fold, purged and embargoed against validation."""
        sessions = set(self.train_sessions)
        train_rows = tuple(
            row
            for row in rows
            if isinstance(row.get(timestamp_key), datetime)
            and ist_session_date(ensure_aware(row[timestamp_key])) in sessions
        )
        return purge_rows(
            train_rows,
            boundary_start=self.validation_start_utc,
            boundary_end=self.validation_end_utc,
            config=self._config(),
            sessions=self.all_sessions,
            timestamp_key=timestamp_key,
        )

    def fit_view(
        self,
        rows: Iterable[Mapping[str, Any]],
        *,
        context: str | None = None,
        timestamp_key: str = DEFAULT_TIMESTAMP_KEY,
    ) -> tuple[Mapping[str, Any], ...]:
        """The only rows a fit-time operation may see for this fold.

        Raises :class:`SplitLeakError` when the caller hands over a row that is
        not in this fold's training sessions, so the fold boundary cannot be
        bypassed by forgetting to pre-filter.
        """
        materialised = tuple(rows)
        assert_fit_partition(
            materialised,
            self.train_sessions,
            context=context or f"fold {self.index} fit",
            timestamp_key=timestamp_key,
        )
        return self.training_rows(materialised, timestamp_key=timestamp_key).kept

    def as_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "n_train_sessions": len(self.train_sessions),
            "n_validation_sessions": len(self.validation_sessions),
            "first_train_session": self.train_sessions[0].isoformat(),
            "last_train_session": self.train_sessions[-1].isoformat(),
            "first_validation_session": self.validation_sessions[0].isoformat(),
            "last_validation_session": self.validation_sessions[-1].isoformat(),
            "purge_rule": self._config().describe(),
        }


def walk_forward_folds(
    sessions: Iterable[date],
    *,
    n_folds: int = 3,
    validation_sessions: int = 1,
    min_train_sessions: int = 2,
    embargo_sessions: int = 0,
    embargo_minutes: int = 0,
    horizon_minutes: int = 30,
) -> tuple[Fold, ...]:
    """Expanding-window folds over the ordered session list.

    Fold *k* trains on ``sessions[:cut + k * validation_sessions]`` and validates
    on the next ``validation_sessions`` sessions, so the training window only ever
    grows and never sees its own validation window.
    """
    ordered = sorted(set(sessions))
    if n_folds < 1 or validation_sessions < 1 or min_train_sessions < 1:
        raise SplitError("n_folds, validation_sessions and min_train_sessions must be positive")
    needed = min_train_sessions + n_folds * validation_sessions
    if len(ordered) < needed:
        raise SplitError(
            f"{len(ordered)} sessions are too few for {n_folds} folds of "
            f"{validation_sessions} session(s) with {min_train_sessions} minimum training sessions"
        )
    folds: list[Fold] = []
    for index in range(n_folds):
        cut = min_train_sessions + index * validation_sessions
        stop = cut + validation_sessions
        folds.append(
            Fold(
                index=index,
                train_sessions=tuple(ordered[:cut]),
                validation_sessions=tuple(ordered[cut:stop]),
                embargo_sessions=embargo_sessions,
                embargo_minutes=embargo_minutes,
                horizon_minutes=horizon_minutes,
            )
        )
    return tuple(folds)


__all__: list[str] = [
    "DEFAULT_SYMBOL_KEY",
    "DEFAULT_TIMESTAMP_KEY",
    "PARTITION_ORDER",
    "SPLIT_MANIFEST_VERSION",
    "DatasetBoundaries",
    "ExclusionRow",
    "Fold",
    "Partition",
    "PurgeConfig",
    "PurgeResult",
    "SessionSplitter",
    "SplitError",
    "SplitLeakError",
    "SplitManifest",
    "assert_fit_partition",
    "audit_split",
    "build_split_manifest",
    "make_session_partitions",
    "walk_forward_folds",
]
