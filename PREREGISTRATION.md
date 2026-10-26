# JEV-IMRP Pre-registration (research contract)

**Version:** 1.0 (contract version `contract_v1`)
**Registered:** 17 October 2026
**Authoritative plan:** `JEV_IMRP_Complete_Implementation_Plan_v2.md` (version 2.0, reviewed 17 October 2026)
**Status:** frozen before any strategy-level result has been inspected. No market
data has been ingested, no model has been trained, and no JEV API call has been
made at the time of registration.

This document is the research contract for the JEV-IMRP platform. It is committed
*before* results are examined and may only be changed by a dated amendment that
also bumps the experiment version (plan section 3). Nothing here may be
silently overwritten after seeing results.

> **RESEARCH ONLY.** This repository and every experiment in it exist to answer
> one research question. There is no order-placement code, no credential
> storage, and no live trading capability, by design (plan section 2.2).

---

## 1. One primary question

> Given the same candidate trades and point-in-time information, does a frozen
> JEV decision policy improve net portfolio outcomes over pre-registered simple
> rules and a supervised model, under realistic Indian cash-equity costs and
> execution assumptions?

Everything else in this project is secondary. A secondary result is never
allowed to become the headline claim, and no secondary result may be used to
select the best-looking version of the system.

## 2. Frozen definitions (plan section 3.1)

| Item | Frozen value | Source |
|---|---|---|
| Universe and eligibility | 20 fixed NSE cash-equity names; membership frozen at experiment start, no retrospective reconstitution | `config/universe.yaml`, plan 2.3 |
| Candidate-signal generator | `TBD — must be fixed before data inspection` | plan 8.1 |
| Candidate role | Upstream strategy emits `BUY`/`SELL` + decision timestamp; every filter only emits `ACCEPT`/`REJECT` | plan 8.1, 9.3 |
| Primary horizon | 30 minutes | plan 8.6, `config/execution_policy.yaml` |
| Secondary horizons | 5, 15, 60 minutes and EOD — secondary only, never a hidden selection route | plan 8.6 |
| Entry convention | Marketable L1 approximation: long entry at valid `ask1`, short entry at valid `bid1` at modelled arrival | plan 8.3, `config/execution_policy.yaml` |
| Exit convention | Long exit at valid `bid1`, short exit at valid `ask1` at modelled exit, at the fixed horizon | plan 8.3 |
| Entry latency | `TBD — must be fixed before data inspection` | plan 8.2 |
| Maximum quote age | `TBD — must be fixed before data inspection` | plan 8.2 |
| Position sizing | Fixed notional, one position per symbol, duplicates rejected while a position is open; notional/cap values `TBD — must be fixed before data inspection` | plan 8.2, `config/execution_policy.yaml` |
| Valid session | IST 09:15–15:30, exchange trading days, exchange holiday calendar `TBD — must be sourced from the exchange, never guessed` | plan 8.2 |
| Cost schedule | `config/cost_schedule.yaml`, per-leg currency fees, effective-date versioned; equity cash/intraday rates `verification: VERIFIED` against the official broker schedule (2026-10-17); the IPFT GST treatment remains `UNVERIFIED_CONTRACT_NOTE_CHECK` | plan 8.4 |
| Cost scenarios | Base, Stress 1 (spread/slippage increased by a predeclared factor — value `TBD`), Stress 2 (greater latency/slippage plus occasional unfilled exits — values `TBD`); identical across all models | plan 8.5, `config/cost_schedule.yaml` |
| Primary comparator | **LightGBM policy, frozen by the validation process** | plan 3.1 |
| Comparator fallback | Declared in advance: if LightGBM cannot be trained on the primary task, the comparator is the best non-JEV baseline from the fixed ladder (plan 9.1), chosen on validation only | plan 3.1, `config/experiment.yaml` |
| Primary metric | Paired difference in **daily portfolio-level net P&L**: `daily_diff[d] = JEV_daily_net_pnl[d] − comparator_daily_net_pnl[d]`, day-block bootstrap | plan 10.3, 10.4 |
| Secondary metrics | Net expectancy per executed trade and per candidate; acceptance/coverage rate; win rate; Q1–Q5 curve; Q5−Q1; worst day; tail quantiles; maximum drawdown from a real equity curve; turnover; capital utilisation; simultaneous exposure; regime/time-of-day/symbol breakdowns; JEV latency, availability and cost | plan 10.5 |
| Minimum economically meaningful effect `delta_min` | `TBD — must be fixed before data inspection`, selected from plausible economics after the cost and return conventions are corrected; **+5 bps is not assumed valid** | plan 3.1, 10.6 |
| Significance level `alpha` | `TBD — must be fixed before data inspection` | plan 3.1, 10.6 |
| Power target | `TBD — must be fixed before data inspection` | plan 3.1, 10.6 |
| Test sidedness | `TBD — must be fixed before data inspection`; a one-sided superiority test is used only if the direction and rejection rule were genuinely pre-specified | plan 3.2 |
| Secondary-comparison multiplicity | `TBD — must be fixed before data inspection`; secondary analyses are corrected or explicitly labelled exploratory | plan 10.4 |
| Split unit | Complete trading session, chronological; train/validation/test boundaries `TBD — must be fixed before data inspection` | plan 10.1 |
| Purge / embargo | Any training row whose label interval overlaps or reaches into the test interval is removed; embargo length in real sessions `TBD — must be justified by the horizon/dependency structure` | plan 10.1, `config/experiment.yaml` |
| Holdout | Locked. Boundaries `TBD` until the prospective session count is known | plan 10.2 |
| Feature set | `fs_v1` registry, definitions frozen in `config/feature_config.yaml`; unknown parameters marked `TBD` there | plan 7.2 |
| Missing data | Never zero-filled. Absent market fields stay null with an explicit validity flag; a missing or stale quote required for a simulated fill marks the candidate **unfillable** instead of inventing a price | plan 5.2, 5.4, 8.3 |
| Failed fills / failed API calls | Recorded with a reason, counted, and reported as coverage/failure rates. Never silently dropped; the JEV failure policy is applied identically to every comparison | plan 9.4, 8.2 |
| Ambiguous barriers / intrabar order | Not implemented in the first stage; if added later, ambiguous cases are marked or resolved adversely, never inferred from OHLC extrema | plan 8.7 |
| Outliers | `TBD — must be fixed before data inspection` | plan 3.1 |
| Maximum trial count | `TBD — must be fixed before data inspection`; every trial, including failures, is logged in `experiments/trial_ledger.csv` | plan 3.3 |
| Prompt-tuning protocol | Prompts and questions are developed on train/validation only; every prompt variant counts as a trial; prompt, model ID and question version are frozen before the holdout; no prompt editing from holdout observations | plan 3.3, 9.5 |

Every item marked `TBD` is genuinely undecided. It must be filled by a dated
amendment **before data inspection**, with the deciding rationale recorded. Filling
one after looking at strategy or holdout results invalidates the confirmation.

## 3. Hypotheses (plan section 3.2)

**Primary, confirmatory:**

- `H0`: JEV's paired expected net portfolio performance improvement over the
  pre-specified primary comparator is less than or equal to `delta_min`.
- `H1`: JEV's paired expected net portfolio performance improvement is greater
  than `delta_min`.

Sidedness is **not** decided here: `TBD — must be fixed before data inspection`.
The plan's requirement stands — one- or two-sided is fixed in advance and aligned
with the scientific claim, and a one-sided superiority test is legitimate only
when the direction and rejection rule were genuinely pre-specified. If the
sidedness is chosen after seeing a result, the test is void.

**Secondary claims (each reported separately, multiplicity-corrected, and never
promoted to the headline result):**

1. JEV policy versus matched random selection.
2. JEV policy versus fixed rules.
3. JEV policy versus the linear baseline.
4. JEV policy versus LightGBM.
5. LightGBM + JEV outputs versus LightGBM alone.
6. JEV top-score outcomes versus lower-score outcomes.
7. Absolute net expectancy above zero after all modelled costs.

A non-significant difference is **not** evidence of equivalence. An equivalence
claim requires a pre-declared equivalence margin and an appropriate equivalence
test, neither of which is registered here.

## 4. Locked holdout procedure (plan 10.2)

1. The holdout is declared and its boundaries frozen before any model is fitted.
   The plan's original 60-calendar-day window is treated as a preliminary
   untouched evaluation only — roughly forty trading sessions, which is not
   strong evidence of robustness on its own. A longer prospective forward test
   (several months, ideally 120+ sessions) is intended, with the length set by a
   prospective power calculation.
2. All fitting — imputation, scaling, encoding, feature selection, calibration,
   hyperparameters — happens inside the training fold of an expanding-window
   walk-forward split. Validation and test distributions never influence feature
   selection.
3. Purging uses label intervals and real session time, never a row count.
4. **Conditions under which the holdout may be opened:** only after the data gate
   (plan 11.1), the label/cost gate (plan 11.2) and the baseline gate (plan 11.3)
   pass, the comparator, metric, `delta_min`, sidedness and cost scenarios are
   frozen, and every attempted trial is present in the trial ledger.
5. The holdout is opened **once** per pre-registered confirmatory experiment, by
   one frozen run of the frozen policy.
6. If the holdout result influences any change at all, the holdout has become
   development data. It is then closed permanently and a new holdout or
   prospective period is required for confirmation.
7. No prompt, threshold, feature or model edit may be made from holdout
   observations, at any time.

## 5. Success, failure and "not established"

An advantage is **established for research purposes** only if all seven
conditions in plan section 11.5 hold. If they do not, the result is reported as
**not established** — which is a legitimate, successful outcome of this project,
not a failure of the experiment. The unfiltered base strategy having no positive
net expectancy at all is likewise a reportable finding, and it stops the study
rather than prompting a search for a better configuration.

## 6. Amendment process

1. Any change to this document is an **amendment**: append a dated entry to the
   table below stating what changed, why, what data had been inspected at the
   time, and whether a new experiment version is required.
2. Bump `experiment_config_version` in `config/experiment.yaml` and the contract
   version in `src/jev_imrp/common/version.py` together with the change.
3. Amendments made after data inspection must say so explicitly. If a change
   could have been informed by strategy-level or holdout results, the affected
   analysis is exploratory and cannot support the confirmatory claim.
4. The original hypothesis is never overwritten. Superseded text is struck
   through in place with its amendment number, so the history stays visible.

| Amendment | Date | Change | Data inspected at time of change? | New experiment version |
|---|---|---|---|---|
| — | 2026-10-17 | Initial registration (contract `contract_v1`) | none | `v1` |

## 7. Known unresolved items carried into Sprint 1

These block execution-relevant results, not the Sprint 0 pass gate:

- Every statutory fee rate in `config/cost_schedule.yaml` carries an explicit
  `source` and `verification` field. The equity cash/intraday rates were
  verified on 2026-10-17 against the official broker schedule (brokerage 0.03%
  or ₹5/order lower-of; STT 0.025% sell-side; NSE transaction 0.00307% per side
  from 2026-03-01, 0.00297% before; SEBI ₹10/crore; stamp duty lower-of 0.003%
  or ₹300/crore, buy side; GST 18% on brokerage + SEBI + transaction charges;
  IPFT ₹0.01/crore). One residual item: the GST treatment of IPFT is
  `UNVERIFIED_CONTRACT_NOTE_CHECK` because the broker's pricing page and FAQ
  conflict. Re-verify the whole schedule against the then-current contract note
  before any execution-relevant result.
- The **feed contract is verified** (accessed 2026-10-17): the Shoonya
  `Subscribe Market Feed` stream sends one full snapshot per instrument (`tk`)
  followed by incremental field updates (`tf`) that the client must merge into
  locally maintained state, exposing **L1 touchline only** (`bp1`/`sp1`) — not
  five-level depth — with resubscription of all instruments after a reconnect and
  reinitialisation from new snapshots. Two items remain `UNVERIFIED` pending the
  Sprint 1 feed spike: the **connect-acknowledgement frame** and the **`ft` clock
  semantics**. Until those are observed in a real capture, no claim about
  connection acknowledgement or feed-clock meaning may be made.
- All `instrument_token` values in `config/universe.yaml` are `null`, to be read
  from the live Shoonya instrument master in Sprint 1.
  **RESOLVED 2026-10-26 (Sprint 1).** All 20 tokens were resolved via the live
  Shoonya `SearchScrip` endpoint using the official `NorenRestApiPy` SDK with
  OAuth login. The original `TATAMOTORS` entry was replaced with
  `TVSMOTOR-EQ` (token 8479) because no instrument named `TATAMOTORS` exists on
  any Shoonya exchange symbol master (NSE/BSE/NFO/CDS/MCX/BFO). See
  `README.md` for the full broker contract notes.
- The TypeSafe `/v1/systemone` contract is recorded as verified in
  `config/experiments/jev_v1.yaml` (Bearer auth; request `state`/`model`/
  `questions`; response `model`/`answers`/`usage` with `usage.input_tokens` and
  `usage.output_tokens`), and the confirmatory model is pinned to `jev-1.13.0`.
  A credentialed Sprint 7 contract test still has to prove the installed client
  actually behaves that way before replay.
