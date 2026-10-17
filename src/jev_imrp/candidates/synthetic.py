"""DETERMINISTIC SYNTHETIC candidate generator - tests and end-to-end demo only.

The real candidate strategy is a pre-registered open item (plan 8.1). This module
does not implement it and does not pretend to: every row it emits carries
``strategy_version == "synthetic_v1"`` and ``is_synthetic=True`` so a synthetic
stream can never be mistaken for a real one in a manifest, a ledger row or a
report.

It is deterministic: for the same seed and the same ordered input rows it emits
byte-identical candidates, because the input is sorted by
``(symbol, timestamp, event_id)`` before a single seeded ``random.Random`` is
drawn from. No wall-clock, no set/dict iteration order, no hashing of addresses.
"""

from __future__ import annotations

import hashlib
import json
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from jev_imrp.candidates.schema import (
    SYNTHETIC_STRATEGY_VERSION,
    CandidateRecord,
    EligibilityStatus,
    Side,
    validate_candidate_stream,
)
from jev_imrp.common.timeutils import (
    SESSION_OPEN,
    ensure_aware,
    from_ist,
    ist_session_date,
)

SYNTHETIC_GENERATOR_VERSION = "synthetic_gen_v1"

#: Synthetic quote/observation inputs use these keys.
INPUT_KEYS: tuple[str, ...] = (
    "symbol",
    "timestamp_utc",
    "bid_price_1",
    "ask_price_1",
    "event_id",
    "atr_pct",
)


@dataclass(frozen=True, slots=True)
class SyntheticGeneratorConfig:
    """Generator settings. Every value is explicit; there are no defaults that
    are read from the environment or from a file."""

    seed: int
    horizon_minutes: int = 30
    candidate_probability: float = 0.35
    max_candidates_per_symbol_session: int = 3
    max_spread_bps_for_eligible: float = 25.0
    strategy_version: str = SYNTHETIC_STRATEGY_VERSION


def feature_hash(features: Mapping[str, float | str | None]) -> str:
    """Stable content hash of a feature snapshot (sha256 over sorted items)."""
    payload = json.dumps(
        {name: features[name] for name in sorted(features)}, sort_keys=True, default=str
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _normalise_input(rows: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    missing = {
        key
        for row in rows
        for key in ("symbol", "timestamp_utc", "bid_price_1", "ask_price_1")
        if key not in row
    }
    if missing:
        raise ValueError(f"synthetic input rows are missing key(s): {sorted(missing)}")
    normalised: list[Mapping[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["timestamp_utc"] = ensure_aware(item["timestamp_utc"])
        item.setdefault(
            "event_id", f"evt_{item['symbol']}_{int(item['timestamp_utc'].timestamp())}"
        )
        item.setdefault("atr_pct", None)
        normalised.append(item)
    normalised.sort(key=lambda r: (str(r["symbol"]), r["timestamp_utc"], str(r["event_id"])))
    return normalised


def synthetic_candidates(
    rows: Iterable[Mapping[str, Any]],
    *,
    config: SyntheticGeneratorConfig | None = None,
    seed: int = 0,
) -> list[CandidateRecord]:
    """Emit plausible SYNTHETIC candidate rows from quote/bar inputs.

    Deterministic per ``(seed, ordered inputs)``. Features carried to a filter
    are limited to the training allowlist (the constructor enforces it):
    ``spread_bps``, ``atr_pct`` and ``tod_minutes_since_open``.
    """
    cfg = config or SyntheticGeneratorConfig(seed=seed)
    rng = random.Random(cfg.seed)
    ordered = _normalise_input(rows)

    emitted: dict[tuple[str, date], int] = {}
    candidates: list[CandidateRecord] = []
    for row in ordered:
        symbol = str(row["symbol"])
        stamp = row["timestamp_utc"]
        session = ist_session_date(stamp)
        key = (symbol, session)
        if emitted.get(key, 0) >= cfg.max_candidates_per_symbol_session:
            continue
        if rng.random() >= cfg.candidate_probability:
            continue

        bid = float(row["bid_price_1"])
        ask = float(row["ask_price_1"])
        mid = (bid + ask) / 2.0
        if bid <= 0.0 or ask <= 0.0 or mid <= 0.0:
            continue
        spread_bps = 10000.0 * (ask - bid) / mid
        atr_pct = row.get("atr_pct")
        atr_value = float(atr_pct) if atr_pct is not None else None
        minutes = (stamp - from_ist(ist_session_date(stamp), SESSION_OPEN)).total_seconds() / 60.0
        features: dict[str, float | str | None] = {
            "spread_bps": spread_bps,
            "atr_pct": atr_value,
            "tod_minutes_since_open": minutes,
        }
        digest = feature_hash(features)

        eligible = spread_bps <= cfg.max_spread_bps_for_eligible
        reason = (
            "synthetic_eligible" if eligible else f"synthetic_spread_too_wide:{spread_bps:.2f}bps"
        )
        side = Side.BUY if rng.random() < 0.5 else Side.SELL
        index = emitted.get(key, 0)
        candidates.append(
            CandidateRecord(
                signal_id=f"{cfg.strategy_version}|{symbol}|{stamp.isoformat()}|{index}",
                strategy_version=cfg.strategy_version,
                symbol=symbol,
                side=side,
                decision_timestamp_utc=stamp,
                source_event_id=str(row["event_id"]),
                intended_horizon_minutes=cfg.horizon_minutes,
                feature_snapshot_id=f"fs_{digest[:12]}",
                feature_hash=digest,
                eligibility_status=(
                    EligibilityStatus.ELIGIBLE if eligible else EligibilityStatus.INELIGIBLE
                ),
                eligibility_reason=reason,
                features=features,
                is_synthetic=True,
            )
        )
        emitted[key] = index + 1

    validate_candidate_stream(candidates)
    return candidates


def synthetic_quote_rows(
    symbols: Sequence[str],
    sessions: Sequence[date],
    *,
    seed: int = 0,
    decisions_per_session: int = 4,
    base_price: float = 100.0,
) -> list[dict[str, Any]]:
    """Deterministic SYNTHETIC quote inputs for the generator (tests/demo only)."""
    rng = random.Random(seed)
    rows: list[dict[str, Any]] = []
    for session in sorted(set(sessions)):
        for symbol in sorted(symbols):
            price = base_price * (1.0 + 0.01 * (rng.random() - 0.5))
            for slot in range(decisions_per_session):
                stamp = from_ist(session, SESSION_OPEN) + timedelta(
                    minutes=15 + 60 * slot + 5 * (rng.random() - 0.5)
                )
                drift = 1.0 + 0.002 * (rng.random() - 0.5)
                mid = price * drift
                half_spread = max(0.01, mid * (0.0005 + 0.004 * rng.random() / 5.0))
                rows.append(
                    {
                        "symbol": symbol,
                        "timestamp_utc": stamp,
                        "bid_price_1": round(mid - half_spread, 4),
                        "ask_price_1": round(mid + half_spread, 4),
                        "event_id": f"synthetic|{symbol}|{session.isoformat()}|{slot}",
                        "atr_pct": round(0.10 + 0.6 * rng.random(), 4),
                    }
                )
    rows.sort(key=lambda r: (r["timestamp_utc"], r["symbol"]))
    return rows


__all__: tuple[str, ...] = (
    "INPUT_KEYS",
    "SYNTHETIC_GENERATOR_VERSION",
    "SyntheticGeneratorConfig",
    "feature_hash",
    "synthetic_candidates",
    "synthetic_quote_rows",
)
