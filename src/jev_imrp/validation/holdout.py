"""Locked-holdout access protection (plan 10.2).

The holdout is readable through exactly one route: an explicit, LOGGED opening
for one frozen confirmatory run. Everything else raises:

* :meth:`HoldoutGuard.training_view` and :meth:`HoldoutGuard.tuning_view` refuse
  any row whose session is a holdout session, so a fitting, imputation, scaling,
  encoding, feature-selection, calibration, threshold or prompt-selection path
  that touches holdout data raises :class:`HoldoutAccessError` before it runs;
* :meth:`HoldoutGuard.holdout_view` requires an open :class:`HoldoutLease`, so
  an evaluation path cannot read holdout rows by accident either;
* a second opening of the same holdout is a protocol violation: it is written to
  the append-only log as a violation record AND raised, so it cannot be silent.

The opening log is JSONL (who / when / why / which sessions / which run). It is
append-only and lives outside the package; tests point it at a temporary file.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from jev_imrp.common.errors import JevError
from jev_imrp.common.timeutils import ensure_aware, ist_session_date, utc_now

DEFAULT_TIMESTAMP_KEY = "decision_timestamp_utc"
HOLDOUT_LOG_NAME = "holdout_access_log.jsonl"


class HoldoutError(JevError):
    """Locked-holdout protection failure."""


class HoldoutAccessError(HoldoutError):
    """A fit/tune path tried to read protected holdout observations."""


class HoldoutProtocolViolation(HoldoutError):
    """The holdout was opened more than once for the same frozen run."""


@dataclass(frozen=True, slots=True)
class HoldoutOpening:
    """One recorded opening (or refusal) of the holdout."""

    kind: str  # "open" or "violation"
    run_id: str
    who: str
    why: str
    opened_at_utc: str
    sessions: tuple[str, ...]
    n_sessions: int

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class HoldoutLease:
    """A single-open lease. Valid only inside its ``with`` block."""

    def __init__(self, guard: HoldoutGuard, opening: HoldoutOpening) -> None:
        self._guard = guard
        self.opening = opening

    def __enter__(self) -> HoldoutLease:
        self._guard._lease.set(self)
        return self

    def __exit__(self, *exc: object) -> None:
        self._guard._clear_lease()

    def read(self, rows: Iterable[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
        """Read holdout rows under the open lease."""
        return self._guard.holdout_view(rows)


class HoldoutGuard:
    """Guards a set of locked-holdout sessions.

    ``log_path`` receives one JSON object per line for every open attempt,
    including refused ones. Pass ``allow_reopen=True`` only for a genuinely new
    pre-registered confirmatory experiment on a NEW holdout, never to make a
    second look at the same data convenient.
    """

    def __init__(
        self,
        sessions: Iterable[date],
        *,
        log_path: str | Path,
        allow_reopen: bool = False,
        timestamp_key: str = DEFAULT_TIMESTAMP_KEY,
    ) -> None:
        self.sessions: frozenset[date] = frozenset(sessions)
        if not self.sessions:
            raise HoldoutError("a holdout must contain at least one complete session")
        self.log_path = Path(log_path)
        self.allow_reopen = allow_reopen
        self.timestamp_key = timestamp_key
        self._lease: ContextVar[HoldoutLease | None] = ContextVar(
            f"jev_holdout_lease_{id(self)}", default=None
        )

    # -- membership --------------------------------------------------------

    def is_holdout_session(self, session: date) -> bool:
        return session in self.sessions

    def is_holdout_row(self, row: Mapping[str, Any]) -> bool:
        stamp = row.get(self.timestamp_key)
        if not isinstance(stamp, datetime):
            raise HoldoutError(
                f"row is missing a usable {self.timestamp_key} timestamp; "
                "holdout membership cannot be determined without it"
            )
        try:
            aware_stamp = ensure_aware(stamp)
        except ValueError as exc:
            raise HoldoutError(f"row {self.timestamp_key} timestamp is invalid: {stamp!r}") from exc
        return self.is_holdout_session(ist_session_date(aware_stamp))

    def holdout_rows(self, rows: Iterable[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
        return tuple(row for row in rows if self.is_holdout_row(row))

    # -- fit-time guards ---------------------------------------------------

    def training_view(self, rows: Iterable[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
        """Rows a fit may see. Raises if any holdout row is present.

        This is the automated protection check: fitting, imputation, scaling,
        encoding, feature selection, calibration, threshold choice and prompt
        selection must all be handed this view, and any holdout row reaching
        those paths raises before any estimate is computed.
        """
        materialised = tuple(rows)
        offending = self.holdout_rows(materialised)
        if offending:
            raise HoldoutAccessError(
                f"refused: {len(offending)} locked-holdout row(s) reached a fit/tuning path "
                f"(first: {offending[0].get(self.timestamp_key)}). The holdout may only be "
                "read inside an explicitly opened, logged confirmatory run."
            )
        return materialised

    def tuning_view(self, rows: Iterable[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
        """Threshold, hyperparameter and prompt selection use the same guard."""
        return self.training_view(rows)

    # -- holdout access ----------------------------------------------------

    def open_holdout(self, *, run_id: str, who: str, why: str) -> HoldoutLease:
        """Open the holdout once, logging who/when/why before returning a lease.

        A second opening of the SAME holdout is recorded as a violation and
        raised: if the holdout influenced a change it is development data now and
        a new holdout is required (plan 10.2).
        """
        if not run_id or not who or not why:
            raise HoldoutError("run_id, who and why are required to open the holdout")
        already_open = any(entry.kind == "open" for entry in self.openings())
        violation = already_open and not self.allow_reopen
        opening = self._record(
            "violation" if violation else "open", run_id=run_id, who=who, why=why
        )
        if violation:
            raise HoldoutProtocolViolation(
                "the locked holdout was already opened for a frozen run; a second opening is a "
                "protocol violation and has been recorded. If the holdout influenced a change it "
                "is development data now and a new holdout is required (plan 10.2)."
            )
        lease = HoldoutLease(self, opening)
        self._lease.set(lease)
        return lease

    def holdout_view(self, rows: Iterable[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
        """Read holdout rows; requires an open lease.

        Every row must be a holdout row.  A mixed batch (development + holdout)
        raises so that the lease cannot be used as a leaky pass-through.
        """
        lease = self._lease.get()
        if lease is None or lease._guard is not self:
            raise HoldoutAccessError(
                "refused: the locked holdout is closed. Call open_holdout(run_id=..., who=..., "
                "why=...) for the single frozen confirmatory run first."
            )
        materialised = tuple(rows)
        if not all(self.is_holdout_row(row) for row in materialised):
            raise HoldoutAccessError(
                "refused: an open lease was used on rows that contain no holdout observation; "
                "the lease is scoped to the holdout and must not be used to read other data."
            )
        if not materialised:
            raise HoldoutAccessError(
                "refused: an open lease was used on an empty row set; "
                "the lease is scoped to the holdout."
            )
        return materialised

    def _clear_lease(self) -> None:
        self._lease.set(None)

    # -- audit log ---------------------------------------------------------

    def _record(self, kind: str, *, run_id: str, who: str, why: str) -> HoldoutOpening:
        """Append one log line for an open attempt (including a refused one)."""
        opening = HoldoutOpening(
            kind=kind,
            run_id=run_id,
            who=who,
            why=why,
            opened_at_utc=utc_now().isoformat(),
            sessions=tuple(sorted(session.isoformat() for session in self.sessions)),
            n_sessions=len(self.sessions),
        )
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(opening.as_dict(), sort_keys=True) + "\n")
        return opening

    def log_entries(self) -> list[dict[str, Any]]:
        if not self.log_path.exists():
            return []
        return [
            json.loads(line)
            for line in self.log_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def openings(self) -> tuple[HoldoutOpening, ...]:
        entries: list[HoldoutOpening] = []
        for entry in self.log_entries():
            record = dict(entry)
            record["sessions"] = tuple(record["sessions"])
            entries.append(HoldoutOpening(**record))
        return tuple(entries)

    def protocol_violations(self) -> tuple[HoldoutOpening, ...]:
        return tuple(entry for entry in self.openings() if entry.kind == "violation")

    def was_opened(self) -> bool:
        return any(entry.kind == "open" for entry in self.openings())

    def describe(self) -> dict[str, Any]:
        return {
            "n_holdout_sessions": len(self.sessions),
            "first_session": min(self.sessions).isoformat(),
            "last_session": max(self.sessions).isoformat(),
            "log_path": str(self.log_path),
            "n_opens": sum(1 for entry in self.openings() if entry.kind == "open"),
            "n_violations": len(self.protocol_violations()),
        }


def assert_holdout_not_fitted(
    guard: HoldoutGuard,
    rows: Iterable[Mapping[str, Any]],
    *,
    context: str = "fit",
) -> None:
    """Standalone check for callers that build their own fit path."""
    if guard.holdout_rows(rows):
        raise HoldoutAccessError(
            f"{context}: protected holdout observations cannot enter model fitting or tuning"
        )


__all__: tuple[str, ...] = (
    "DEFAULT_TIMESTAMP_KEY",
    "HOLDOUT_LOG_NAME",
    "HoldoutAccessError",
    "HoldoutError",
    "HoldoutGuard",
    "HoldoutLease",
    "HoldoutOpening",
    "HoldoutProtocolViolation",
    "assert_holdout_not_fitted",
)
