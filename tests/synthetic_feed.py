"""Synthetic feed fixtures and a deterministic clock for recorder tests.

Every fixture under ``tests/fixtures/synthetic_feed/`` is hand-written and
labelled ``data_origin: synthetic``. Nothing here is a capture of a real market
session; no test may present it as market data, and no test opens a network
connection.

The clock is injected so receive timestamps and monotonic readings are exactly
reproducible. That determinism is what makes replay parity testable at all.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "synthetic_feed"

#: Feed epoch seconds in the fixtures start here; synthetic, not a real session.
SYNTHETIC_SESSION_START_UTC = datetime(2026, 7, 1, 3, 45, 0, tzinfo=UTC)


@dataclass
class FixedClock:
    """Monotonic fake clock: one fixed step per call, starting at a known instant."""

    start: datetime = SYNTHETIC_SESSION_START_UTC
    step: timedelta = timedelta(milliseconds=10)
    calls: int = 0
    _origin_ns: int = 1_000_000_000

    def __call__(self) -> tuple[datetime, int]:
        moment = self.start + self.step * self.calls
        monotonic_ns = self._origin_ns + int(self.step.total_seconds() * 1e9) * self.calls
        self.calls += 1
        return moment, monotonic_ns

    def advance(self, seconds: float) -> None:
        self.start = self.start + timedelta(seconds=seconds)


def load_fixture(name: str) -> dict[str, Any]:
    """Load a synthetic fixture by file name, asserting its synthetic label."""
    data = json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))
    assert data["data_origin"] == "synthetic", "fixtures must declare themselves synthetic"
    assert "warning" in data, "fixtures must carry the synthetic-data warning"
    return data


@dataclass
class FixtureRun:
    """The outcome of replaying one fixture through a recorder."""

    events: list[Any] = field(default_factory=list)
    states: list[Any] = field(default_factory=list)

    @property
    def messages(self) -> list[str]:
        return [event.raw_payload_json for event in self.events]


def tick_sizes(fixture: dict[str, Any]) -> dict[tuple[str, str], float]:
    return {
        (entry["exchange"], entry["token"]): float(entry["tick_size_inr"])
        for entry in fixture["instruments"]
    }
