"""Online recorder health checks (plan 5.4 tracking list, 14.1, 14.2).

Cheap, synchronous checks that run during capture and decide two things:

* what to *record* (nothing is dropped for being unhealthy), and
* whether the caller should stop emitting decisions while raw capture continues.

The stop conditions are exactly plan 14.2's list. They are reported as data, with
the observed value and the limit side by side, so an operator can see why.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from jev_imrp.ingest.feed_state import QuoteStateBook, stale_states
from jev_imrp.ingest.shoonya_recorder import Recorder

#: Wall-clock sanity band. A feed that appears to be from the future or from
#: before the run started indicates a broken clock, not a broken market.
MAX_WALL_CLOCK_SKEW_SECONDS = 5.0


@dataclass(frozen=True, slots=True)
class HealthCheck:
    """One named check with its observation and whether it passed."""

    name: str
    passed: bool
    observed: Any
    limit: Any
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "passed": self.passed,
            "observed": self.observed,
            "limit": self.limit,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class HealthReport:
    """Structured recorder health at one instant."""

    checked_at_utc: datetime
    checks: list[HealthCheck] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(check.passed for check in self.checks)

    @property
    def failures(self) -> list[HealthCheck]:
        return [check for check in self.checks if not check.passed]

    def to_dict(self) -> dict[str, Any]:
        return {
            "checked_at_utc": self.checked_at_utc.isoformat(),
            "ok": self.ok,
            "checks": [check.to_dict() for check in self.checks],
        }


def clock_skew_seconds(received_utc: datetime, wall_now_utc: datetime | None = None) -> float:
    """Difference between the stamped receive time and the current wall clock.

    Used only as a synchronization health signal. The recorded receive time is
    never rewritten to hide skew (plan 5.3).
    """
    wall = wall_now_utc or datetime.now(UTC)
    return abs((received_utc - wall).total_seconds())


def evaluate(
    recorder: Recorder,
    as_of_utc: datetime | None = None,
    max_quote_age_ms: float = 5_000.0,
    expected_symbols: int | None = None,
    wall_now_utc: datetime | None = None,
) -> HealthReport:
    """Run every capture health check against the recorder's live state."""
    now = as_of_utc or datetime.now(UTC)
    counters = recorder.counters
    book: QuoteStateBook = recorder.book
    checks: list[HealthCheck] = []

    written = counters.written
    checks.append(
        HealthCheck(
            name="raw_writes_recorded",
            passed=written == counters.received,
            observed=written,
            limit=counters.received,
            detail="every received message must produce exactly one raw event",
        )
    )
    checks.append(
        HealthCheck(
            name="parser_exceptions",
            passed=counters.parse_errors == 0,
            observed=counters.parse_errors,
            limit=0,
            detail="messages recorded but not parseable",
        )
    )
    checks.append(
        HealthCheck(
            name="unknown_message_types",
            passed=not counters.unknown_types,
            observed=dict(counters.unknown_types),
            limit={},
            detail="flagged, never dropped; an empty result means no schema drift seen",
        )
    )
    pending = book.awaiting_snapshot()
    checks.append(
        HealthCheck(
            name="subscriptions_awaiting_snapshot",
            passed=not pending,
            observed=[f"{e}|{t}" for e, t in pending],
            limit=[],
            detail="every subscription needs a fresh tk in the current generation",
        )
    )
    if expected_symbols is not None:
        valid_symbols = sum(1 for state in book.states.values() if state.state_valid)
        checks.append(
            HealthCheck(
                name="valid_symbol_count",
                passed=valid_symbols >= expected_symbols,
                observed=valid_symbols,
                limit=expected_symbols,
                detail="symbols with a valid reconstructed state",
            )
        )
    stale = stale_states(book, now, max_quote_age_ms)
    checks.append(
        HealthCheck(
            name="stale_quotes",
            passed=not stale,
            observed={f"{e}|{t}": round(ms, 1) for (e, t), ms in sorted(stale.items())},
            limit=f"<= {max_quote_age_ms} ms",
            detail="time since last valid update",
        )
    )
    if counters.first_received_utc is not None and wall_now_utc is not None:
        skew = clock_skew_seconds(counters.first_received_utc, wall_now_utc)
        checks.append(
            HealthCheck(
                name="wall_clock_skew",
                passed=skew <= MAX_WALL_CLOCK_SKEW_SECONDS,
                observed=round(skew, 3),
                limit=f"<= {MAX_WALL_CLOCK_SKEW_SECONDS} s",
                detail="first stamped receive time vs the current wall clock",
            )
        )
    checks.append(
        HealthCheck(
            name="no_connection_anomalies",
            passed=not book.connection_anomalies,
            observed=list(book.connection_anomalies[:10]),
            limit=[],
            detail="gaps, late snapshots and other connection-level anomalies",
        )
    )
    return HealthReport(checked_at_utc=now, checks=checks)


def should_stop_emitting(report: HealthReport) -> tuple[bool, list[str]]:
    """Plan 14.2 stop decision for *decisions*; raw capture keeps running.

    Stops on: stale critical quotes, a subscription without a fresh snapshot
    after reconnect, unknown message/schema version, and clock problems. Write
    backlog and disk usage are operational concerns handled by the caller.
    """
    stopping = {
        "stale_quotes",
        "subscriptions_awaiting_snapshot",
        "unknown_message_types",
        "wall_clock_skew",
        "no_connection_anomalies",
    }
    reasons = [
        f"{check.name}: {check.detail} (observed {check.observed})"
        for check in report.failures
        if check.name in stopping
    ]
    return bool(reasons), reasons


def worst_staleness_ms(book: QuoteStateBook, as_of_utc: datetime) -> float:
    """Largest quote age across all states, for the session's top-line number."""
    if not book.states:
        return 0.0
    return max(state.quote_age_ms(as_of_utc) for state in book.states.values())
