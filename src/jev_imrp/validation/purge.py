"""Label-interval purge and time-based embargo (plan 10.1).

The purge rule implemented here, fixed before any tuning:

    A training row is purged when its LABEL INTERVAL
    ``[label_interval_start, label_interval_end]`` overlaps or REACHES INTO the
    evaluation interval, i.e. when ``label_interval_end >= boundary_start``.

Why "reaches into" counts: such a row's outcome is only fully known at or after
``label_available_at == label_interval_end``, which lies at or after the first
evaluation session's open. Keeping it would hand the fit a return that was
realised inside the evaluation window. The horizon sets the width of the risk
zone, not a number of rows: purge is by TIME INTERVAL, never by a fixed count of
rows, so the purged volume follows the data rather than a tuned constant.

Rows that merely TOUCH the boundary (``label_interval_end == boundary_start``)
are purged by default (``touch_is_purge=True``). Setting ``touch_is_purge=False``
keeps strictly-overlapping purging and records the touching rows in
:attr:`PurgeResult.boundary_touching`, so the choice is explicit and auditable
either way. Rows with no label interval (``label_interval_end is None``, i.e.
unlabelled) carry no realised outcome and are therefore never purged for
overlap; their exclusion is an explicit dataset decision recorded in the split
manifest, not a silent drop.

Embargo: measured in real time and real sessions, never in rows. Either the
whole sessions immediately preceding the boundary are dropped
(``embargo_sessions``) or rows whose decision time falls inside a wall-clock
window before the boundary are dropped (``embargo_minutes``). Justification: with
a 30-minute primary horizon, intraday outcomes of adjacent sessions share the
overnight-gap and opening-auction dependence structure, so a one-session
embargo absorbs it while a row-count purge could not be justified at all. Both
knobs are configuration, never constants.

Grouped rows (same symbol and decision timestamp, i.e. simultaneous candidates
for one symbol) stay together: a violation found on any member removes the whole
group, and the recorded reason is the strongest reason among its members.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any

from jev_imrp.common.errors import JevError
from jev_imrp.common.timeutils import ensure_aware, ist_session_date, session_bounds_utc

DEFAULT_TIMESTAMP_KEY = "decision_timestamp_utc"
DEFAULT_SYMBOL_KEY = "symbol"
DEFAULT_LABEL_START_KEY = "label_interval_start"
DEFAULT_LABEL_END_KEY = "label_interval_end"

#: Predeclared purge rule version, recorded on every result.
PURGE_RULE_VERSION = "purge_v1"


class PurgeError(JevError):
    """Malformed input for purge or embargo."""


class PurgeReason(StrEnum):
    """Why a row left the training partition."""

    LABEL_INTERVAL_OVERLAPS_BOUNDARY = "label_interval_overlaps_boundary"
    LABEL_INTERVAL_REACHES_BOUNDARY = "label_interval_reaches_boundary"
    EMBARGO = "embargo"
    MISSING_LABEL_INTERVAL = "missing_label_interval"

    @property
    def severity(self) -> int:
        """Higher wins when one group carries several reasons."""
        order = {
            PurgeReason.MISSING_LABEL_INTERVAL: 0,
            PurgeReason.EMBARGO: 1,
            PurgeReason.LABEL_INTERVAL_REACHES_BOUNDARY: 2,
            PurgeReason.LABEL_INTERVAL_OVERLAPS_BOUNDARY: 3,
        }
        return order[self]


@dataclass(frozen=True, slots=True)
class PurgeConfig:
    """Purge and embargo settings; defaults encode the plan's primary horizon."""

    horizon_minutes: int = 30
    embargo_sessions: int = 0
    embargo_minutes: int = 0
    touch_is_purge: bool = True
    rule_version: str = PURGE_RULE_VERSION

    def validate(self) -> None:
        if self.horizon_minutes <= 0:
            raise PurgeError("horizon_minutes must be positive")
        if self.embargo_sessions < 0 or self.embargo_minutes < 0:
            raise PurgeError("embargo must not be negative")

    def describe(self) -> str:
        return (
            f"{self.rule_version}: purge when label_interval_end >= boundary_start "
            f"(touch_is_purge={self.touch_is_purge}); horizon={self.horizon_minutes}m; "
            f"embargo_sessions={self.embargo_sessions}; embargo_minutes={self.embargo_minutes}"
        )


@dataclass(frozen=True, slots=True)
class PurgedRow:
    """One removed row: its interval, the reason, and the group it belonged to."""

    symbol: str
    decision_timestamp_utc: datetime
    session: date
    label_interval_start: datetime | None
    label_interval_end: datetime | None
    reason: PurgeReason
    group_key: tuple[str, datetime]

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "decision_timestamp_utc": self.decision_timestamp_utc.isoformat(),
            "session": self.session.isoformat(),
            "label_interval_start": (
                self.label_interval_start.isoformat() if self.label_interval_start else None
            ),
            "label_interval_end": (
                self.label_interval_end.isoformat() if self.label_interval_end else None
            ),
            "reason": str(self.reason),
            "group_symbol": self.group_key[0],
            "group_timestamp_utc": self.group_key[1].isoformat(),
        }


@dataclass(frozen=True, slots=True)
class PurgeResult:
    """What survived, what did not, and why."""

    kept: tuple[Mapping[str, Any], ...]
    purged: tuple[PurgedRow, ...]
    boundary_touching: tuple[Mapping[str, Any], ...] = ()
    by_reason: Mapping[str, int] = field(default_factory=dict)
    rule: str = PURGE_RULE_VERSION

    @property
    def n_kept(self) -> int:
        return len(self.kept)

    @property
    def n_purged(self) -> int:
        return len(self.purged)

    def summary(self) -> dict[str, Any]:
        return {
            "rule": self.rule,
            "n_input": self.n_kept + self.n_purged,
            "n_kept": self.n_kept,
            "n_purged": self.n_purged,
            "by_reason": dict(sorted(self.by_reason.items())),
            "n_boundary_touching_kept": len(self.boundary_touching),
        }


def embargo_window_start(
    boundary_start: datetime,
    *,
    embargo_sessions: int = 0,
    embargo_minutes: int = 0,
    sessions: Sequence[date] = (),
) -> datetime | None:
    """Earliest decision time still inside the embargo window, or ``None``.

    ``embargo_sessions`` counts real trading sessions, so it needs the ordered
    session list; ``embargo_minutes`` is a wall-clock window. Both are real
    time, never a row count.
    """
    boundary_start = ensure_aware(boundary_start)
    earliest: datetime | None = None
    if embargo_minutes > 0:
        earliest = boundary_start - timedelta(minutes=embargo_minutes)
    if embargo_sessions > 0:
        ordered = sorted(set(sessions))
        boundary_session = ist_session_date(boundary_start)
        try:
            position = ordered.index(boundary_session)
        except ValueError as exc:
            raise PurgeError("boundary session is not in the ordered session list") from exc
        target = position - embargo_sessions
        if target < 0:
            raise PurgeError("embargo_sessions is larger than the available history")
        session_open, _ = session_bounds_utc(ordered[target])
        if earliest is None or session_open > earliest:
            earliest = session_open
    return earliest


def _interval(
    row: Mapping[str, Any], *, start_key: str, end_key: str
) -> tuple[datetime | None, datetime | None]:
    start = row.get(start_key)
    end = row.get(end_key)
    return (
        ensure_aware(start) if isinstance(start, datetime) else None,
        ensure_aware(end) if isinstance(end, datetime) else None,
    )


def purge_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    boundary_start: datetime,
    boundary_end: datetime | None = None,
    config: PurgeConfig | None = None,
    sessions: Sequence[date] = (),
    timestamp_key: str = DEFAULT_TIMESTAMP_KEY,
    symbol_key: str = DEFAULT_SYMBOL_KEY,
    label_start_key: str = DEFAULT_LABEL_START_KEY,
    label_end_key: str = DEFAULT_LABEL_END_KEY,
) -> PurgeResult:
    """Purge training rows whose label interval crosses into an evaluation period.

    ``boundary_start`` is the first instant of the evaluation period (validation
    or locked test). ``boundary_end`` is validated and reported but never used
    to purge: a training row reaching past the evaluation period is exactly the
    overlap this rule removes, and the evaluation period is chronologically
    after the training period by construction.
    """
    cfg = config or PurgeConfig()
    cfg.validate()
    boundary_start = ensure_aware(boundary_start)
    if boundary_end is not None and ensure_aware(boundary_end) < boundary_start:
        raise PurgeError("boundary_end precedes boundary_start")

    window_start = embargo_window_start(
        boundary_start,
        embargo_sessions=cfg.embargo_sessions,
        embargo_minutes=cfg.embargo_minutes,
        sessions=sessions,
    )

    materialised = list(rows)
    parsed: list[tuple[Mapping[str, Any], datetime, tuple[str, datetime], PurgeReason | None]] = []
    group_reasons: dict[tuple[str, datetime], PurgeReason] = {}

    for row in materialised:
        decision = row.get(timestamp_key)
        if not isinstance(decision, datetime):
            raise PurgeError(f"row has no usable {timestamp_key}")
        decision = ensure_aware(decision)
        group = (str(row.get(symbol_key, "")), decision)
        _, end = _interval(row, start_key=label_start_key, end_key=label_end_key)

        reason: PurgeReason | None = None
        if end is not None:
            if end > boundary_start:
                reason = PurgeReason.LABEL_INTERVAL_OVERLAPS_BOUNDARY
            elif end == boundary_start and cfg.touch_is_purge:
                reason = PurgeReason.LABEL_INTERVAL_REACHES_BOUNDARY
        if (
            reason is None
            and window_start is not None
            and window_start <= decision < boundary_start
        ):
            reason = PurgeReason.EMBARGO

        parsed.append((row, decision, group, reason))
        if reason is not None:
            current = group_reasons.get(group)
            if current is None or reason.severity > current.severity:
                group_reasons[group] = reason

    kept: list[Mapping[str, Any]] = []
    touching: list[Mapping[str, Any]] = []
    purged: list[PurgedRow] = []
    by_reason: dict[str, int] = {}

    for row, decision, group, own_reason in parsed:
        group_reason = group_reasons.get(group)
        if group_reason is None:
            kept.append(row)
            if own_reason is None:
                _, end = _interval(row, start_key=label_start_key, end_key=label_end_key)
                if end is not None and end == boundary_start:
                    touching.append(row)
            continue
        start, end = _interval(row, start_key=label_start_key, end_key=label_end_key)
        by_reason[str(group_reason)] = by_reason.get(str(group_reason), 0) + 1
        purged.append(
            PurgedRow(
                symbol=group[0],
                decision_timestamp_utc=decision,
                session=ist_session_date(decision),
                label_interval_start=start,
                label_interval_end=end,
                reason=group_reason,
                group_key=group,
            )
        )

    return PurgeResult(
        kept=tuple(kept),
        purged=tuple(purged),
        boundary_touching=tuple(touching),
        by_reason=by_reason,
        rule=cfg.describe(),
    )


def purge_training_rows(
    rows: Iterable[Mapping[str, Any]],
    *,
    boundary_start: datetime,
    boundary_end: datetime | None = None,
    config: PurgeConfig | None = None,
    sessions: Sequence[date] = (),
    timestamp_key: str = DEFAULT_TIMESTAMP_KEY,
) -> PurgeResult:
    """Thin wrapper for the single-boundary training case (see :func:`purge_rows`)."""
    return purge_rows(
        rows,
        boundary_start=boundary_start,
        boundary_end=boundary_end,
        config=config,
        sessions=sessions,
        timestamp_key=timestamp_key,
    )
