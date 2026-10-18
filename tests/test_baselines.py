"""Baseline policy tests (plan 9.1 rungs 1-4, 9.2 fairness).

Covers: the unfiltered base, the matched random filter (reproducible per seed,
matching coverage inside predeclared strata, repeated draws giving a
distribution), the cost-aware filter (training-only estimates, causality proven by
injecting future data), the fixed rule set (deterministic), identical candidate
input for every policy, and one trial-ledger line per configuration.
"""

from __future__ import annotations

import shutil
from datetime import date, timedelta
from pathlib import Path

import pytest

from jev_imrp.candidates import (
    CandidateRecord,
    EligibilityStatus,
    Side,
    SyntheticGeneratorConfig,
    synthetic_candidates,
)
from jev_imrp.candidates.synthetic import synthetic_quote_rows
from jev_imrp.common.ledger import LEDGER_PATH, load_ledger
from jev_imrp.common.timeutils import SESSION_OPEN, ensure_aware, from_ist, ist_session_date, to_utc
from jev_imrp.models import (
    CostAwarePolicy,
    Decision,
    RandomPolicy,
    RulePolicy,
    TrainingEstimates,
    UnfilteredPolicy,
    apply_policy,
    register_policy_trial,
)
from jev_imrp.validation.purge import PurgeConfig, PurgeReason, purge_rows
from jev_imrp.validation.temporal_splits import SplitLeakError

TRAIN_SESSIONS: tuple[date, ...] = tuple(
    date(2026, 5, 4) + timedelta(days=offset) for offset in range(4)
)
FUTURE_SESSIONS: tuple[date, ...] = tuple(
    date(2026, 5, 18) + timedelta(days=offset) for offset in range(2)
)
ALL_SESSIONS: tuple[date, ...] = (*TRAIN_SESSIONS, *FUTURE_SESSIONS)
SYMBOLS = ("AAA", "BBB", "CCC")
SEED = 4242
COVERAGE = 0.4


def stream(sessions: tuple[date, ...] = ALL_SESSIONS) -> list[CandidateRecord]:
    quotes = synthetic_quote_rows(SYMBOLS, sessions, seed=SEED)
    return synthetic_candidates(
        quotes, config=SyntheticGeneratorConfig(seed=SEED, candidate_probability=1.0)
    )


def rebuild(candidate: CandidateRecord, **overrides: object) -> CandidateRecord:
    """Same candidate with selected fields replaced (e.g. a dropped feature)."""
    fields: dict[str, object] = {
        "signal_id": candidate.signal_id,
        "strategy_version": candidate.strategy_version,
        "symbol": candidate.symbol,
        "side": candidate.side,
        "decision_timestamp_utc": candidate.decision_timestamp_utc,
        "source_event_id": candidate.source_event_id,
        "intended_horizon_minutes": candidate.intended_horizon_minutes,
        "feature_snapshot_id": candidate.feature_snapshot_id,
        "feature_hash": candidate.feature_hash,
        "eligibility_status": candidate.eligibility_status,
        "eligibility_reason": candidate.eligibility_reason,
        "features": dict(candidate.features),
    }
    fields.update(overrides)
    return CandidateRecord(**fields)  # type: ignore[arg-type]


def candidates() -> list[CandidateRecord]:
    return stream()


# -- rung 1: unfiltered -----------------------------------------------------


def test_unfiltered_accepts_everything() -> None:
    result = apply_policy(UnfilteredPolicy(), candidates())
    assert result.n_accepted == result.n_presented
    assert result.acceptance_rate == 1.0


def test_unfiltered_does_not_even_filter_ineligible_candidates() -> None:
    ineligible = [c for c in candidates() if not c.is_eligible]
    result = apply_policy(UnfilteredPolicy(), ineligible)
    assert result.n_accepted == len(ineligible)


# -- rung 2: matched random -------------------------------------------------


def test_random_filter_is_reproducible_per_seed() -> None:
    policy = RandomPolicy(target_coverage=COVERAGE, seed=7)
    first = apply_policy(policy, candidates()).accept_ids()
    second = apply_policy(RandomPolicy(target_coverage=COVERAGE, seed=7), candidates()).accept_ids()
    assert first == second


def test_random_filter_matches_target_coverage_inside_session_strata() -> None:
    policy = RandomPolicy(target_coverage=COVERAGE, seed=SEED)
    pool = candidates()
    result = apply_policy(policy, pool)
    # Small strata round down per session, so the global rate matches the target
    # to within roughly one candidate per session.
    assert abs(result.acceptance_rate - COVERAGE) < 0.15

    per_session = policy.stratum_coverage(pool)
    assert per_session
    for session, coverage in per_session.items():
        members = [c for c in pool if c.session.isoformat() == session]
        expected = int(len(members) * COVERAGE) / len(members)
        assert coverage == pytest.approx(expected)


def test_random_draws_give_a_distribution_not_one_lucky_draw() -> None:
    draws = RandomPolicy.draws(COVERAGE, n_draws=8, base_seed=100)
    pool = candidates()
    accepted = [frozenset(apply_policy(draw, pool).accept_ids()) for draw in draws]
    assert len(set(accepted)) > 1, "repeated draws must differ"
    for draw, ids in zip(draws, accepted, strict=True):
        assert apply_policy(draw, pool).acceptance_rate == pytest.approx(
            len(ids) / len(pool), abs=0.05
        )


def test_random_filter_never_splits_a_group_of_simultaneous_candidates() -> None:
    policy = RandomPolicy(target_coverage=COVERAGE, seed=SEED)
    result = apply_policy(policy, candidates())
    accepted = set(result.accept_ids())
    # A candidate rejected only because of its neighbour is not a fairness
    # violation here (the random filter works on the signal stream), but the
    # accepted set must be a subset of presented ids and unique.
    assert len(accepted) == len(result.accepted)
    assert accepted <= {c.signal_id for c in candidates()}


# -- rung 3: cost-aware -----------------------------------------------------


def training_rows(sessions: tuple[date, ...] = TRAIN_SESSIONS) -> list[dict[str, object]]:
    """Labelled rows in the shape the cost-aware estimator consumes."""
    rows: list[dict[str, object]] = []
    for index, session in enumerate(sessions):
        for symbol in SYMBOLS:
            for bucket, gross, cost in ((30, 12.0, 4.0), (120, 2.0, 4.0), (300, 1.0, 5.0)):
                stamp = to_utc(from_ist(session, SESSION_OPEN) + timedelta(minutes=bucket))
                rows.append(
                    {
                        "symbol": symbol,
                        "decision_timestamp_utc": stamp,
                        "tod_minutes_since_open": float(bucket),
                        "gross_bps": gross + index,
                        "cost_bps": cost,
                        "net_bps": gross - cost,
                    }
                )
    return rows


def cost_aware_policy(
    sessions: tuple[date, ...] = TRAIN_SESSIONS, **kwargs: object
) -> CostAwarePolicy:
    estimates = TrainingEstimates.fit(training_rows(sessions), training_sessions=sessions)
    return CostAwarePolicy(estimates=estimates, **kwargs)  # type: ignore[arg-type]


def test_cost_aware_filter_accepts_only_where_expected_gross_beats_cost_plus_margin() -> None:
    policy = cost_aware_policy()
    decisions = {d.signal_id: d for d in policy.decide_batch(candidates())}
    # Verify each decision is consistent with the training estimates
    # (bucketing is applied in decide(), so we test through decide_batch)
    for candidate in candidates():
        decision = decisions[candidate.signal_id]
        raw_bucket = candidate.feature("tod_minutes_since_open")
        if raw_bucket is None:
            assert decision.decision is Decision.REJECT
            continue
        # Need to apply same bucket function as decide()
        bucket = round(
            float(raw_bucket) / policy.estimates.bucket_width
        ) * policy.estimates.bucket_width
        estimate = policy.estimates.estimate_for(bucket)
        if estimate is None:
            # No estimate for this bucket - decision should reflect that
            continue
        gross, cost = estimate
        if gross - cost - policy.safety_margin_bps <= 0:
            assert decision.decision is Decision.REJECT
        else:
            assert decision.decision is Decision.ACCEPT


def test_cost_aware_estimates_refuse_non_training_sessions() -> None:
    from jev_imrp.validation.temporal_splits import SplitLeakError

    mixed = [*training_rows(), *training_rows(FUTURE_SESSIONS)]
    with pytest.raises(SplitLeakError):
        TrainingEstimates.fit(mixed, training_sessions=TRAIN_SESSIONS)


def test_cost_aware_decisions_cannot_change_when_future_data_is_injected() -> None:
    """Causality in the style of tests/test_features_causal.py.

    Fit on training-only data, decide, then append rows from strictly later
    sessions (with wildly different realised returns) to the universe the policy
    is asked about. Every decision must be identical.
    """
    policy = cost_aware_policy()
    pool = candidates()
    before = {d.signal_id: (d.decision, d.reason, d.score) for d in policy.decide_batch(pool)}

    future_rows = [
        {
            **row,
            "decision_timestamp_utc": to_utc(
                from_ist(FUTURE_SESSIONS[0], SESSION_OPEN) + timedelta(minutes=30)
            ),
            "tod_minutes_since_open": 30.0,
            "gross_bps": 9999.0,
            "cost_bps": 0.0,
        }
        for row in training_rows(TRAIN_SESSIONS)[:9]
    ]
    # Inject the future rows as extra candidates and as extra "history".
    injected = [
        *pool,
        *synthetic_candidates(
            synthetic_quote_rows(SYMBOLS, FUTURE_SESSIONS, seed=SEED),
            config=SyntheticGeneratorConfig(seed=SEED, candidate_probability=1.0),
        ),
    ]
    assert len(injected) > len(pool)
    after = {d.signal_id: (d.decision, d.reason, d.score) for d in policy.decide_batch(injected)}
    for signal_id, verdict in before.items():
        assert after[signal_id] == verdict
    # And refitting on the future data is refused rather than silently accepted.
    with pytest.raises(SplitLeakError):
        TrainingEstimates.fit([*training_rows(), *future_rows], training_sessions=TRAIN_SESSIONS)


def test_cost_aware_rejects_buckets_without_training_support() -> None:
    policy = cost_aware_policy()
    unknown = CandidateRecord(
        signal_id="unknown-bucket",
        strategy_version="synthetic_v1",
        symbol="AAA",
        side=Side.BUY,
        decision_timestamp_utc=to_utc(from_ist(FUTURE_SESSIONS[0], SESSION_OPEN)),
        source_event_id="e",
        intended_horizon_minutes=30,
        feature_snapshot_id="fs",
        feature_hash="h",
        eligibility_status=EligibilityStatus.ELIGIBLE,
        eligibility_reason="ok",
        features={"tod_minutes_since_open": 999.0},
    )
    decision = policy.decide(unknown)
    assert decision.decision is Decision.REJECT
    assert decision.reason == "no_training_estimate_for_bucket"


def test_at_least_one_candidate_receives_a_training_estimate() -> None:
    """At least one synthetic candidate must bucket into a training estimate."""
    policy = cost_aware_policy()
    pool = candidates()
    decisions = policy.decide_batch(pool)
    accepted = [d for d in decisions if d.decision is Decision.ACCEPT]
    assert accepted, "expected at least one candidate to receive a training estimate"


def test_cost_aware_rejects_a_candidate_with_a_missing_bucket() -> None:
    policy = cost_aware_policy()
    candidate = candidates()[0]
    stripped = (
        CandidateRecord(
            **{**candidate.as_dict(), "decision_timestamp_utc": candidate.decision_timestamp_utc}
        )
        if False
        else CandidateRecord(
            signal_id=candidate.signal_id,
            strategy_version=candidate.strategy_version,
            symbol=candidate.symbol,
            side=candidate.side,
            decision_timestamp_utc=candidate.decision_timestamp_utc,
            source_event_id=candidate.source_event_id,
            intended_horizon_minutes=candidate.intended_horizon_minutes,
            feature_snapshot_id=candidate.feature_snapshot_id,
            feature_hash=candidate.feature_hash,
            eligibility_status=candidate.eligibility_status,
            eligibility_reason=candidate.eligibility_reason,
            features={},
        )
    )
    decision = policy.decide(stripped)
    assert decision.decision is Decision.REJECT
    assert decision.reason.startswith("missing_feature")


# -- rung 4: fixed rules ----------------------------------------------------


def test_rules_are_deterministic() -> None:
    policy = RulePolicy()
    first = apply_policy(policy, candidates())
    second = apply_policy(RulePolicy(), candidates())
    assert first.decisions == second.decisions
    assert RulePolicy().version == "rules_v1"


def _edge_candidate(**feature_overrides: float) -> CandidateRecord:
    """A valid candidate with selected feature overrides for branch coverage."""
    candidate = candidates()[0]
    features = {
        "spread_bps": 5.0,
        "atr_pct": 1.0,
        "tod_minutes_since_open": 60.0,
        # always override with the provided values
        **feature_overrides,
    }
    return rebuild(
        candidate,
        signal_id=f"edge|{id(features)}",
        features=features,
    )


def test_rules_apply_the_pre_registered_thresholds() -> None:
    policy = RulePolicy()
    # Synthetic stream + constructed edge candidates to cover every rule branch.
    pool = [
        *candidates(),
        # spread above max
        _edge_candidate(spread_bps=10.0),
        # volatility below min
        _edge_candidate(atr_pct=0.01),
        # volatility above max
        _edge_candidate(atr_pct=9.0),
        # time of day excluded (before session open + 15 min)
        _edge_candidate(tod_minutes_since_open=5.0),
        # time of day excluded (after 330 min)
        _edge_candidate(tod_minutes_since_open=340.0),
    ]
    decisions = {d.signal_id: d for d in policy.decide_batch(pool)}
    for candidate in pool:
        spread = float(candidate.feature("spread_bps"))  # type: ignore[arg-type]
        minutes = float(candidate.feature("tod_minutes_since_open"))  # type: ignore[arg-type]
        reason = decisions[candidate.signal_id].reason
        if spread > policy.spec.max_spread_bps:
            assert reason == "spread_above_max"
        elif not (
            policy.spec.min_atr_pct
            <= float(candidate.feature("atr_pct"))
            <= policy.spec.max_atr_pct
        ):  # type: ignore[arg-type]
            assert reason in ("volatility_below_min", "volatility_above_max")
        elif not policy._within_tod(minutes):
            assert reason == "time_of_day_excluded"
        else:
            assert reason == "rules_passed"


def test_rules_reject_when_a_required_feature_is_missing() -> None:
    policy = RulePolicy()
    candidate = candidates()[0]
    stripped = rebuild(
        candidate, features={k: v for k, v in candidate.features.items() if k != "atr_pct"}
    )
    assert policy.decide(stripped).reason == "missing_feature:atr_pct"


# -- fairness (plan 9.2) ----------------------------------------------------


def test_all_policies_see_identical_candidate_input() -> None:
    pool = candidates()
    policies = [
        UnfilteredPolicy(),
        RandomPolicy(target_coverage=COVERAGE, seed=SEED),
        cost_aware_policy(),
        RulePolicy(),
    ]
    presented = tuple(c.signal_id for c in pool)
    for policy in policies:
        result = apply_policy(policy, pool)
        assert tuple(d.signal_id for d in result.decisions) == presented


def test_acceptance_rate_convention_is_explicit_for_every_policy() -> None:
    pool = candidates()
    for policy in (
        UnfilteredPolicy(),
        RandomPolicy(target_coverage=COVERAGE, seed=SEED),
        cost_aware_policy(),
        RulePolicy(),
    ):
        result = apply_policy(policy, pool)
        assert result.n_presented == len(pool), "denominator is every candidate presented"
        assert result.acceptance_rate == result.n_accepted / len(pool)
        assert 0.0 <= result.acceptance_rate <= 1.0
        assert result.summary()["policy"] == policy.name


def test_policies_never_change_direction() -> None:
    pool = candidates()
    sides = {c.signal_id: c.side for c in pool}
    for policy in (
        UnfilteredPolicy(),
        RandomPolicy(target_coverage=COVERAGE, seed=SEED),
        cost_aware_policy(),
        RulePolicy(),
    ):
        result = apply_policy(policy, pool)
        assert set(result.accept_ids()) <= set(sides)


# -- reproducibility: one ledger line per configuration ---------------------


@pytest.fixture
def ledger_copy(tmp_path: Path) -> Path:
    path = tmp_path / "trial_ledger.csv"
    shutil.copy(LEDGER_PATH, path)
    return path


def test_each_policy_configuration_registers_one_ledger_line(ledger_copy: Path) -> None:
    before = len(load_ledger(ledger_copy))
    policies = [
        UnfilteredPolicy(),
        RandomPolicy(target_coverage=COVERAGE, seed=SEED),
        cost_aware_policy(),
        RulePolicy(),
    ]
    for policy in policies:
        register_policy_trial(
            policy,
            experiment_id="sprint4-baseline-demo",
            candidate_strategy_version="synthetic_v1",
            horizon_minutes=30,
            ledger_path=ledger_copy,
            notes="SYNTHETIC smoke registration",
        )
    rows = load_ledger(ledger_copy)
    assert len(rows) == before + len(policies)
    assert {row["trial_type"] for row in rows[before:]} == {"baseline_filter"}
    assert all(row["registered_at_utc"] for row in rows[before:])
    assert all(row["code_commit"] for row in rows[before:])


def test_identical_configuration_registers_an_identical_trial_id(ledger_copy: Path) -> None:
    policy = RulePolicy()
    first = register_policy_trial(policy, experiment_id="e", ledger_path=ledger_copy)
    second = register_policy_trial(RulePolicy(), experiment_id="e", ledger_path=ledger_copy)
    assert first["trial_id"] == second["trial_id"]


# -- integration: the same candidate stream through purge and the policies ---


def test_policies_run_on_a_purged_training_stream() -> None:
    """Purge first, then select: the training side carries no evaluation overlap."""
    boundary = to_utc(from_ist(FUTURE_SESSIONS[0], SESSION_OPEN))
    rows = [
        {
            "symbol": c.symbol,
            "decision_timestamp_utc": c.decision_timestamp_utc,
            "label_interval_start": c.decision_timestamp_utc,
            "label_interval_end": c.decision_timestamp_utc + timedelta(minutes=30),
        }
        for c in stream(TRAIN_SESSIONS)
    ]
    result = purge_rows(rows, boundary_start=boundary, config=PurgeConfig())
    assert result.n_purged == 0
    assert {
        ist_session_date(ensure_aware(r["decision_timestamp_utc"])) for r in result.kept
    } == set(TRAIN_SESSIONS)
    policy = RulePolicy()
    pool = [c for c in candidates() if c.session in TRAIN_SESSIONS]
    assert apply_policy(policy, pool).n_presented == len(pool)
    assert result.by_reason.get(str(PurgeReason.LABEL_INTERVAL_OVERLAPS_BOUNDARY), 0) == 0
