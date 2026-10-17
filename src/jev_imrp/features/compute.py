"""The one shared feature computation library (plan 7.1).

Both historical replay and live/shadow mode call :meth:`FeatureEngine.compute`.
There is no second implementation: the engine only ever reads observations whose
availability timestamp is at or before the requested ``as_of``, so a value that
was not knowable at decision time cannot reach a feature, whether it arrived
before the replay started, during the replay, or never.

Declared assumptions (frozen, not inferred from data):

* VWAP accumulates ``(last_traded_price, last_traded_volume)`` from quote-state
  rows carrying a positive trade, one trade per row, resetting each IST session.
* ``atr_pct`` uses completed bars of the current session only.
* ``relative_volume`` compares current-session cumulative volume with the mean
  cumulative volume at the same minutes-since-open point over strictly prior
  sessions. The current session's final volume is never used.
* ``dist_pdh_pct``/``dist_pdl_pct`` use the prior session's completed bars only.

Missing or invalid input produces ``None`` plus a reason code. No value is
invented and no invalid observation is turned into a valid one.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

# pandas-stubs is not in the pinned dev dependencies (see needs-input note); until
# it is, the pandas import is the only untyped boundary in this package.
import pandas as pd  # type: ignore[import-untyped]

from jev_imrp.common.timeutils import ensure_aware, ist_session_date, minutes_since_open
from jev_imrp.features.definitions import (
    BAR_AVAILABILITY_COL,
    BAR_COLUMNS,
    CONTEXT_AVAILABILITY_COL,
    CONTEXT_COLUMNS,
    INDIA_VIX_SERIES,
    MINUTES_IN_SESSION,
    NIFTY_SERIES,
    QUALITY_OK,
    QUOTE_AVAILABILITY_COL,
    QUOTE_COLUMNS,
    TRAINING_FEATURES,
    FeatureParams,
    feature_versions,
    params_from_config,
)

_EMPTY = pd.DataFrame(columns=list(BAR_COLUMNS))
_QUOTE_EMPTY = pd.DataFrame(columns=list(QUOTE_COLUMNS))
_CONTEXT_EMPTY = pd.DataFrame(columns=list(CONTEXT_COLUMNS))


@dataclass(frozen=True)
class FeatureValue:
    """One feature result: value, validity flag, quality reason."""

    value: float | str | None
    valid: bool
    quality: str = QUALITY_OK

    @staticmethod
    def ok(value: float | str) -> FeatureValue:
        return FeatureValue(value, True, QUALITY_OK)

    @staticmethod
    def missing(quality: str) -> FeatureValue:
        return FeatureValue(None, False, quality)


@dataclass(frozen=True)
class FeatureRecord:
    """A complete point-in-time feature row (plan 7.1 / 6.1 feature_snapshots)."""

    symbol: str
    feature_timestamp_utc: datetime
    as_of_timestamp_utc: datetime
    feature_config_version: str
    feature_set_version: str
    features: dict[str, FeatureValue]
    lineage: dict[str, str]
    params: FeatureParams

    @property
    def quality_state(self) -> str:
        """``ok`` when every feature is valid, ``partial`` otherwise, plus reasons."""
        bad = {n: v.quality for n, v in self.features.items() if not v.valid}
        if not bad:
            return "ok"
        return "partial:" + "|".join(f"{n}={q}" for n, q in sorted(bad.items()))

    def to_row(self) -> dict[str, Any]:
        """Flat table row: values, ``<name>_valid``, ``<name>_quality``, then metadata."""
        row: dict[str, Any] = {
            "symbol": self.symbol,
            "feature_timestamp_utc": self.feature_timestamp_utc,
            "as_of_timestamp_utc": self.as_of_timestamp_utc,
            "feature_config_version": self.feature_config_version,
            "feature_set_version": self.feature_set_version,
            "quality_state": self.quality_state,
        }
        for name, value in self.features.items():
            row[name] = value.value
            row[f"{name}_valid"] = value.valid
            row[f"{name}_quality"] = value.quality
        for key, manifest in self.lineage.items():
            row[f"lineage_{key}"] = manifest
        return row

    def training_row(self) -> dict[str, float | str | None]:
        """Only the allowlisted training inputs (plan 7.4 parity)."""
        return {name: self.features[name].value for name in TRAINING_FEATURES}


def as_of_frame(frame: pd.DataFrame, availability_col: str, as_of: datetime) -> pd.DataFrame:
    """Rows whose local availability is at or before ``as_of``.

    The single as-of gate used by every feature. A valid timestamp alone is not
    proof of causality (plan 7.1), so the join is on availability, not event time.
    """
    if frame.empty:
        return frame
    return frame[frame[availability_col] <= as_of]


def _ordered(frame: pd.DataFrame, columns: tuple[str, ...]) -> pd.DataFrame:
    """Deterministic observation order (stable sort, ties broken by the tuple)."""
    if frame.empty:
        return frame
    return frame.sort_values(list(columns), kind="stable")


def _session_of(series: pd.Series) -> pd.Series:
    return series.map(ist_session_date)


def _normalise_utc(frame: pd.DataFrame, columns: tuple[str, ...]) -> pd.DataFrame:
    """Coerce timestamp columns to tz-aware UTC so as-of comparisons are exact."""
    out = frame.copy()
    for column in columns:
        if column in out.columns and len(out):
            out[column] = pd.to_datetime(out[column], utc=True)
    return out


def _time_of_day(minutes: float, buckets: tuple[tuple[str, int, int | None], ...]) -> FeatureValue:
    """Minutes since open plus its predeclared bucket; invalid outside the session."""
    if not 0.0 <= minutes <= MINUTES_IN_SESSION:
        return FeatureValue.missing("outside_session")
    for name, start, end in buckets:
        if minutes >= start and (end is None or minutes < end):
            return FeatureValue(name, True)
    raise AssertionError(f"no time-of-day bucket covers {minutes} minutes after the open")


class FeatureEngine:
    """Stateful-by-accumulation feature engine used by replay and shadow alike.

    The engine holds ingested observations; every read goes through
    :func:`as_of_frame`, so ingesting rows that are only knowable later cannot
    change an earlier ``compute`` result.
    """

    def __init__(
        self,
        quotes: pd.DataFrame | None = None,
        bars: pd.DataFrame | None = None,
        context: pd.DataFrame | None = None,
        params: FeatureParams | None = None,
    ) -> None:
        if quotes is None or "symbol" not in quotes.columns:
            quotes = _QUOTE_EMPTY
        if bars is None or "bar_start_utc" not in bars.columns:
            bars = _EMPTY
        if context is None or "series" not in context.columns:
            context = _CONTEXT_EMPTY
        self.quotes = _normalise_utc(quotes, ("feed_timestamp_utc", QUOTE_AVAILABILITY_COL))
        self.bars = _normalise_utc(bars, ("bar_start_utc", "bar_end_utc", BAR_AVAILABILITY_COL))
        self.context = _normalise_utc(context, ("source_timestamp_utc", CONTEXT_AVAILABILITY_COL))
        self.params = params_from_config() if params is None else params

    # -- ingestion ---------------------------------------------------------

    def observe_quote(self, quote: pd.DataFrame) -> None:
        """Append quote-state rows (live path calls this per row)."""
        self.quotes = _normalise_utc(
            pd.concat([self.quotes, quote], ignore_index=True),
            ("feed_timestamp_utc", QUOTE_AVAILABILITY_COL),
        )

    def observe_bars(self, bars: pd.DataFrame) -> None:
        self.bars = _normalise_utc(
            pd.concat([self.bars, bars], ignore_index=True),
            ("bar_start_utc", "bar_end_utc", BAR_AVAILABILITY_COL),
        )

    def observe_context(self, context: pd.DataFrame) -> None:
        self.context = _normalise_utc(
            pd.concat([self.context, context], ignore_index=True),
            ("source_timestamp_utc", CONTEXT_AVAILABILITY_COL),
        )

    # -- views (all availability-filtered) ---------------------------------

    def _quote_view(self, symbol: str, as_of: datetime) -> pd.DataFrame:
        view = as_of_frame(self.quotes, QUOTE_AVAILABILITY_COL, as_of)
        if view.empty:
            return view
        return _ordered(
            view[view["symbol"] == symbol],
            ("received_timestamp_utc", "feed_timestamp_utc", "event_id"),
        )

    def _bar_view(self, symbol: str, as_of: datetime, session: Any) -> pd.DataFrame:
        view = as_of_frame(self.bars, BAR_AVAILABILITY_COL, as_of)
        if view.empty:
            return view
        view = view[(view["symbol"] == symbol) & (_session_of(view["bar_start_utc"]) == session)]
        # Completed bars only: a partial bar's values are not final (plan 7.2).
        return _ordered(view[view["is_complete"].astype(bool)], ("bar_start_utc",))

    def _prior_session_bars(self, symbol: str, as_of: datetime, session: Any) -> pd.DataFrame:
        view = as_of_frame(self.bars, BAR_AVAILABILITY_COL, as_of)
        if view.empty:
            return view
        view = view[view["symbol"] == symbol]
        dates = _session_of(view["bar_start_utc"])
        prior = view[(dates < session) & view["is_complete"].astype(bool)]
        return _ordered(prior, ("bar_start_utc",))

    def _context_view(self, series: str, as_of: datetime) -> pd.DataFrame:
        view = as_of_frame(self.context, CONTEXT_AVAILABILITY_COL, as_of)
        if view.empty:
            return view
        return _ordered(
            view[view["series"] == series], ("available_timestamp_utc", "source_timestamp_utc")
        )

    # -- features ----------------------------------------------------------

    def compute(self, symbol: str, as_of: datetime) -> FeatureRecord:
        """All features for ``symbol`` at decision time ``as_of``."""
        as_of = ensure_aware(as_of)
        session = ist_session_date(as_of)
        params = self.params

        latest_quote, quote_quality = self._latest_quote(symbol, as_of)
        quote_view = self._quote_view(symbol, as_of)
        bars = self._bar_view(symbol, as_of, session)

        features: dict[str, FeatureValue] = {}
        features["spread_bps"] = _spread_bps(latest_quote, quote_quality)
        features["touch_size_imbalance"] = _touch_size_imbalance(latest_quote, quote_quality)
        features["atr_pct"] = _atr_pct(bars, params.atr_lookback_bars)
        features["relative_volume"] = self._relative_volume(symbol, as_of, session, bars)
        features["dist_vwap_pct"], vwap_lineage = self._dist_vwap_pct(
            quote_view, session, latest_quote
        )
        features["dist_pdh_pct"], features["dist_pdl_pct"], pdh_lineage = self._dist_pdh_pdl_pct(
            symbol, as_of, session, latest_quote
        )
        vix = self._india_vix(as_of)
        features["india_vix"], features["india_vix_change_pct"], vix_lineage = vix
        nifty, nifty_lineage = self._nifty_returns(as_of)
        features.update(nifty)

        minutes = minutes_since_open(as_of)
        features["tod_minutes_since_open"] = FeatureValue(minutes, True, QUALITY_OK)
        features["tod_bucket"] = _time_of_day(minutes, params.time_of_day_buckets)

        lineage = {
            "quote": _quote_lineage(latest_quote),
            "bars": f"{len(bars)} completed bars of session {session.isoformat()}",
            "vwap": vwap_lineage,
            "pdh_pdl": pdh_lineage,
            "vix": vix_lineage,
            "nifty": nifty_lineage,
        }
        config_version, set_version = feature_versions()
        return FeatureRecord(
            symbol=symbol,
            feature_timestamp_utc=as_of,
            as_of_timestamp_utc=as_of,
            feature_config_version=config_version,
            feature_set_version=set_version,
            features=features,
            lineage=lineage,
            params=params,
        )

    # -- individual pieces -------------------------------------------------

    def _latest_quote(self, symbol: str, as_of: datetime) -> tuple[pd.Series | None, str]:
        """Newest quote row available at ``as_of``, plus why it is unusable."""
        view = self._quote_view(symbol, as_of)
        if view.empty:
            return None, "missing_observation"
        latest = view.iloc[-1]
        if not bool(latest["state_valid"]):
            return latest, "invalid_quote_state"
        age = max(
            (as_of - latest[QUOTE_AVAILABILITY_COL]).total_seconds() * 1000.0,
            float(latest["quote_age_ms"]) if pd.notna(latest["quote_age_ms"]) else 0.0,
        )
        if age > self.params.max_quote_age_ms:
            return latest, "stale_quote"
        if not _positive(latest["bid_price_1"]) or not _positive(latest["ask_price_1"]):
            return latest, "invalid_quote_state"
        if float(latest["ask_price_1"]) < float(latest["bid_price_1"]):
            return latest, "invalid_quote_state"
        return latest, QUALITY_OK

    def _relative_volume(
        self, symbol: str, as_of: datetime, session: Any, bars: pd.DataFrame
    ) -> FeatureValue:
        params = self.params
        current = float(bars["volume"].sum()) if not bars.empty else 0.0
        minutes = minutes_since_open(as_of)
        prior = self._prior_session_bars(symbol, as_of, session)
        if prior.empty:
            return FeatureValue.missing("warmup")
        dates = _session_of(prior["bar_start_utc"])
        # Session dates strictly before today, most recent first.
        recent = sorted(set(dates), reverse=True)[: params.rel_volume_prior_sessions]
        reference_values: list[float] = []
        for day in recent:
            day_bars = prior[dates == day]
            # Cumulative volume of that session up to the same clock time: bars
            # that had already ended by ``minutes`` minutes after the open.
            same_clock = day_bars[day_bars["bar_end_utc"].map(minutes_since_open) <= minutes]
            reference_values.append(float(same_clock["volume"].sum()))
        reference_values = [v for v in reference_values if v > 0.0]
        if not reference_values:
            return FeatureValue.missing("warmup")
        if current <= 0.0:
            return FeatureValue.missing("missing_observation")
        return FeatureValue.ok(current / (sum(reference_values) / len(reference_values)))

    def _dist_vwap_pct(
        self, quote_view: pd.DataFrame, session: Any, latest: pd.Series | None
    ) -> tuple[FeatureValue, str]:
        """Percent distance from the accumulated session VWAP available at as_of."""
        if quote_view.empty or latest is None:
            return FeatureValue.missing("missing_observation"), "no quotes available"
        dates = _session_of(quote_view[QUOTE_AVAILABILITY_COL])
        trades = quote_view[
            (dates == session)
            & (quote_view["last_traded_volume"] > 0)
            & (quote_view["last_traded_price"] > 0)
        ]
        volume = float(trades["last_traded_volume"].sum())
        if volume <= 0.0:
            return FeatureValue.missing("warmup"), "no trades available by as_of"
        vwap = float((trades["last_traded_price"] * trades["last_traded_volume"]).sum() / volume)
        price = (
            float(latest["last_traded_price"]) if _positive(latest["last_traded_price"]) else None
        )
        if price is None:
            return FeatureValue.missing("missing_observation"), "no last traded price available"
        manifest = (
            f"vwap={vwap:.6f} from {len(trades)} trades/{volume:.0f} qty, "
            f"reset={self.params.vwap_reset_rule}"
        )
        return FeatureValue.ok(100.0 * (price - vwap) / vwap), manifest

    def _dist_pdh_pdl_pct(
        self, symbol: str, as_of: datetime, session: Any, latest: pd.Series | None
    ) -> tuple[FeatureValue, FeatureValue, str]:
        params = self.params
        price = (
            float(latest["last_traded_price"])
            if latest is not None and _positive(latest["last_traded_price"])
            else None
        )
        if price is None:
            return (
                FeatureValue.missing("missing_observation"),
                FeatureValue.missing("missing_observation"),
                "no last traded price available",
            )
        prior = self._prior_session_bars(symbol, as_of, session)
        if prior.empty:
            return (
                FeatureValue.missing("warmup"),
                FeatureValue.missing("warmup"),
                "no prior completed session",
            )
        dates = _session_of(prior["bar_start_utc"])
        last_prior = dates.max()
        prior_day = prior[dates == last_prior]
        if len(prior_day) < params.min_complete_bars_for_pdh_pdl:
            reason = "insufficient_prior_session"
            return (
                FeatureValue.missing(reason),
                FeatureValue.missing(reason),
                f"prior session {last_prior} incomplete",
            )
        pdh = float(prior_day["high_price"].max())
        pdl = float(prior_day["low_price"].min())
        if not (_positive(pdh) and _positive(pdl)):
            reason = "missing_observation"
            return (
                FeatureValue.missing(reason),
                FeatureValue.missing(reason),
                f"prior session {last_prior} levels invalid",
            )
        gap = bool(prior_day["has_gap"].any())
        manifest = (
            f"prior_session={last_prior.isoformat()} bars={len(prior_day)} "
            f"pdh={pdh:.4f} pdl={pdl:.4f}"
        )
        quality = "gap_in_prior_session" if gap else QUALITY_OK
        return (
            FeatureValue(100.0 * (price - pdh) / pdh, True, quality),
            FeatureValue(100.0 * (price - pdl) / pdl, True, quality),
            manifest,
        )

    def _india_vix(self, as_of: datetime) -> tuple[FeatureValue, FeatureValue, str]:
        view = self._context_view(INDIA_VIX_SERIES, as_of)
        if view.empty:
            reason = "missing_observation"
            return FeatureValue.missing(reason), FeatureValue.missing(reason), "no vix rows"
        limit = self.params.india_vix_max_staleness_seconds
        fresh = view[(as_of - view["available_timestamp_utc"]).dt.total_seconds() <= limit]
        if fresh.empty:
            reason = "stale_context"
            return (
                FeatureValue.missing(reason),
                FeatureValue.missing(reason),
                f"latest vix older than {limit}s",
            )
        latest = fresh.iloc[-1]
        manifest = (
            f"vix@{latest['source_timestamp_utc'].isoformat()} "
            f"available@{latest['available_timestamp_utc'].isoformat()}"
        )
        previous = view.iloc[:-1]
        change = FeatureValue.missing("warmup")
        if not previous.empty:
            before = previous["value"].iloc[-1]
            if float(before) > 0:
                change = FeatureValue.ok(
                    100.0 * (float(latest["value"]) - float(before)) / float(before)
                )
        return FeatureValue.ok(float(latest["value"])), change, manifest

    def _nifty_returns(self, as_of: datetime) -> tuple[dict[str, FeatureValue], str]:
        view = self._context_view(NIFTY_SERIES, as_of)
        out: dict[str, FeatureValue] = {}
        for horizon in self.params.nifty_horizons_minutes:
            name = f"nifty_ret_{horizon}_pct"
            if view.empty:
                out[name] = FeatureValue.missing("missing_observation")
                continue
            now = float(view["value"].iloc[-1])
            cutoff = as_of - timedelta(minutes=horizon)
            reference = view[view["available_timestamp_utc"] <= cutoff]
            if reference.empty or float(reference["value"].iloc[-1]) <= 0:
                out[name] = FeatureValue.missing("warmup")
                continue
            past = float(reference["value"].iloc[-1])
            out[name] = FeatureValue.ok(100.0 * (now - past) / past)
        return out, f"{len(view)} nifty points available by as_of"


def compute_feature_series(
    engine: FeatureEngine, symbol: str, as_of_times: list[datetime]
) -> list[FeatureRecord]:
    """Replay path: one record per decision time, in the given order."""
    return [engine.compute(symbol, as_of) for as_of in as_of_times]


def _positive(value: Any) -> bool:
    return value is not None and pd.notna(value) and float(value) > 0.0


def _spread_bps(latest: pd.Series | None, quality: str) -> FeatureValue:
    """10000 * (ask1 - bid1) / midpoint, for a valid fresh positive touchline.

    A stale or invalid touchline yields NULL plus its reason code, never a value.
    """
    if latest is None or quality != QUALITY_OK:
        return FeatureValue.missing(quality)
    bid, ask = float(latest["bid_price_1"]), float(latest["ask_price_1"])
    mid = (bid + ask) / 2.0
    if mid <= 0.0:
        return FeatureValue.missing("invalid_quote_state")
    return FeatureValue.ok(10000.0 * (ask - bid) / mid)


def _quote_lineage(latest: pd.Series | None) -> str:
    if latest is None:
        return "no quote available"
    return (
        f"event_id={latest['event_id']} feed={latest['feed_timestamp_utc'].isoformat()} "
        f"available={latest[QUOTE_AVAILABILITY_COL].isoformat()}"
    )


def _touch_size_imbalance(latest: pd.Series | None, quality: str) -> FeatureValue:
    """(bid_qty1 - ask_qty1) / (bid_qty1 + ask_qty1); NULL and flagged if the
    denominator is zero (plan 7.2, L1 touchline only -- not order-book depth)."""
    if latest is None or quality in {"missing_observation", "stale_quote", "invalid_quote_state"}:
        return FeatureValue.missing(quality)
    bid_qty, ask_qty = float(latest["bid_qty_1"]), float(latest["ask_qty_1"])
    denominator = bid_qty + ask_qty
    if denominator <= 0.0:
        return FeatureValue.missing("zero_denominator")
    return FeatureValue.ok((bid_qty - ask_qty) / denominator)


def _atr_pct(bars: pd.DataFrame, lookback: int) -> FeatureValue:
    """ATR / close in percent, from COMPLETED bars of the current session."""
    if len(bars) < lookback + 1:
        return FeatureValue.missing("warmup")
    window = bars.iloc[-(lookback + 1) :]
    previous_close = window["close_price"].shift(1)
    true_range = pd.concat(
        [
            window["high_price"] - window["low_price"],
            (window["high_price"] - previous_close).abs(),
            (window["low_price"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = float(true_range.iloc[1:].mean())
    close = float(window["close_price"].iloc[-1])
    if close <= 0.0:
        return FeatureValue.missing("missing_observation")
    return FeatureValue.ok(100.0 * atr / close)
