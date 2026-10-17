# JEV-IMRP: Complete Implementation and Research Plan

**Version:** 2.0  
**Review date:** 17 October 2026  
**Purpose:** Determine whether JEV adds incremental, repeatable, executable net trading value over simple rules and supervised models for Indian cash equities.  
**Initial operating mode:** Research, historical replay, live shadow and paper trading only. No live order routing until a separate operational and regulatory gate is passed.

---

## 0. How to use this plan

This plan consolidates the original JEV-IMRP implementation blueprint and the subsequent meta-review. It prioritizes defects that could invalidate the experiment, distinguishes design quality from code readiness, and intentionally defers sophistication that is not needed to answer the first research question.

Labels used throughout:

- **MUST:** required before the affected result can be trusted.
- **SHOULD:** strongly recommended, but can follow an initial feasibility test.
- **LATER:** only implement if earlier results justify it.
- **DO NOT ASSUME:** an unverified capability or statistical claim.

The initial goal is not to maximize the number of features, the sophistication of the architecture, or JEV's chance of winning. The goal is to produce a reliable answer—even a negative answer.

## 1. Executive summary

### 1.1 Primary research question

> Given the same candidate trades and point-in-time information, does a frozen JEV decision policy improve net portfolio outcomes over pre-registered simple rules and a supervised model, under realistic Indian cash-equity costs and execution assumptions?

### 1.2 Secondary questions

1. Does the unfiltered base strategy have any positive net expectancy at all?
2. Does a simple cost/liquidity filter capture most of the attainable improvement?
3. Does JEV rank candidates better than a matched random filter?
4. Does JEV add value over Logistic/linear models and LightGBM?
5. Does adding JEV outputs to LightGBM improve out-of-sample performance over LightGBM alone?
6. Do any gains survive cost sensitivity, different market regimes and a later prospective shadow period?

### 1.3 Current state

The original proposal has a sensible goal and an appropriate low-cost stack: Python, immutable raw files, Parquet, DuckDB, scikit-learn, LightGBM and a separate JEV challenger. But the supplied snippets are not yet reliable enough to validate an edge.

The highest-priority issues are:

- Shoonya incremental updates are treated like full snapshots in the supplied recorder.
- The recorder example captures level-1 touchline fields, not five-level depth.
- Receive time and feed time are not stored with adequate distinction.
- The signals schema and forward-label SQL disagree about required columns.
- The cost helper calculates one-sided charges while the labeler treats `cost_bps` as a full-trade cost.
- The split code uses a row-count buffer rather than an actual time/label-interval purge.
- The locked holdout is described but not enforced by the shown training code.
- The JEV example uses an API request/response shape that differs from current documented TypeSafe System One documentation.
- The JEV path does not durably persist failed calls, creating a risk of selective evaluation.
- The proposed model comparison does not consistently give every model the same inputs and prediction target.

These are mostly repairable. They mean the current implementation is **not research-ready**, not that the broader idea must be abandoned.

### 1.4 Operating principle

Build the platform as a research engine independent of JEV. The dataset, labeler, cost model, validation framework and experiment ledger are the durable assets. JEV is replaceable.

---

## 2. Scope and non-goals

### 2.1 In scope for the first experiment

- Indian NSE cash-equity intraday candidates.
- A small, explicitly defined universe of approximately 20 liquid shares.
- Prospective Shoonya feed capture, with verified fields and feed behavior.
- Causal one-minute features and point-in-time market context.
- A single pre-registered primary horizon, initially 30 minutes.
- A fill/cost convention that models both entry and exit.
- Baselines: unfiltered signals, matched random filter, fixed rules, linear model and LightGBM.
- JEV as a frozen filter/ranker evaluated after the simpler pipeline works.
- Chronological validation, paired daily outcome analysis, block-bootstrap uncertainty and a locked final holdout.
- Live shadow/paper observation after historical/replay gates pass.

### 2.2 Explicitly out of scope at first

- HFT or exchange-colocated execution claims.
- A sub-millisecond latency architecture.
- Full order-book reconstruction unless the subscribed vendor feed actually provides the required depth.
- Options-derived features unless a specific, pre-registered hypothesis requires them.
- A large feature store, streaming platform or cloud deployment before local reliability is proven.
- Automated live trading or capital allocation based on unvalidated model outputs.
- Hyperparameter/feature/prompt search against the final holdout.

### 2.3 Universe policy

Define the initial universe in a versioned configuration file. Record instrument token, symbol, exchange, tick size, lot/quantity conventions where relevant, and effective dates. For a prospective fixed-universe experiment, freeze membership at the start and document that the initial test estimates performance on that fixed universe—not the entire historical Nifty 50.

If membership is reconstituted over time, use a pre-registered rule based only on data available at each rebalance date (for example, trailing traded value), and retain a time-versioned eligibility table. Never use today's constituents or today's liquidity ranks to retroactively determine historical eligibility.

---

## 3. Pre-registration and research contract

Create `PREREGISTRATION.md` and commit it before examining strategy-level results. Changes require a dated amendment and a new experiment version. No one may silently overwrite the original hypothesis after seeing results.

### 3.1 Freeze these definitions

- Universe and eligibility rules.
- Candidate-signal generator and its code/version.
- Primary horizon: 30 minutes, unless changed before data inspection.
- Entry and exit convention, maximum quote age, model/submit latency assumption, position sizing and handling of overlapping signals.
- Primary cost schedule and stress-cost scenarios.
- Primary comparator: **LightGBM policy frozen by the validation process**, with any fallback/strongest-baseline rule specified in advance. Do not choose the comparator after viewing final test results.
- Primary metric: paired difference in daily portfolio-level net P&L or return, subject to fixed capital/risk and execution rules.
- Secondary metrics: per-trade net expectancy, win rate, Q1–Q5 performance, top-versus-bottom spread, drawdown, turnover, tail losses and calibration if probabilities are claimed.
- Minimum economically meaningful effect `delta_min`, selected from plausible economics before the locked test. Do not mechanically assume +5 bps is valid until the cost and return conventions are corrected.
- Significance level, power target and correction for secondary comparisons.
- Validation windows, embargo/purge rules, locked holdout boundaries and conditions under which the holdout may be opened.
- Rules for missing data, failed API calls, retries, stale quotes, ambiguous barriers and outlier treatment.
- Maximum experiment/trial count and prompt-tuning protocol.

### 3.2 Hypotheses

Use one primary confirmatory comparison and separate secondary claims.

**Primary:**

- `H0`: JEV's paired expected net portfolio performance improvement over the prespecified primary comparator is less than or equal to `delta_min`.
- `H1`: JEV's paired expected net portfolio performance improvement is greater than `delta_min`.

Whether the test is one- or two-sided must be fixed in advance and be aligned with the scientific claim. A one-sided superiority test is appropriate only when the direction and rejection rule were genuinely pre-specified.

**Secondary:**

- JEV policy versus matched random selection.
- JEV policy versus fixed rules.
- JEV policy versus the linear baseline.
- JEV policy versus LightGBM.
- LightGBM + JEV outputs versus LightGBM alone.
- JEV top-score outcomes versus lower-score outcomes.
- Absolute net expectancy above zero after all modeled costs.

Do not interpret a non-significant difference as evidence of equivalence. An equivalence claim requires a predeclared equivalence margin and an appropriate equivalence test.

### 3.3 Multiple testing and prompt discipline

Maintain `experiments/trial_ledger.csv` (or a table) with every feature configuration, prompt, model version, threshold, horizon, target, cost scenario and test attempted. Failed and abandoned experiments count as trials for selection-bias accounting.

Prompts/questions may be developed on the development/training and validation windows only. The final prompt and model ID are frozen before the final holdout. No prompt editing based on holdout observations.

---

## 4. System architecture

### 4.1 Logical flow

```text
Shoonya websocket + context feeds
              |
              v
Append-only raw event recorder
              |
              v
Feed normalization + quote-state reconstruction + QA
              |
              v
Causal feature snapshots / minute bars
              |
              v
Versioned candidate-signal stream
              |
              v
Execution simulator + fee engine + forward labels
              |
              v
Chronological train/validation/test partitions
      |           |            |             |
      v           v            v             v
 Random         Rules       Linear / LGBM    JEV
      |           |            |             |
      +-----------+------------+-------------+
                         |
                         v
      Paired daily portfolio evaluation + block uncertainty
                         |
                         v
               Locked holdout decision
                         |
                         v
             Prospective shadow / paper stage
```

### 4.2 Recommended low-cost stack

- Python for capture, features, labeling, baselines and evaluation.
- `asyncio` only where concurrency is needed; do not let concurrency obscure durability.
- `pyarrow`/Parquet for append-only raw and normalized datasets.
- DuckDB for analytical joins, feature/label tables and cohort analysis.
- pandas/Polars depending on measured needs; choose one primary dataframe engine initially.
- scikit-learn for preprocessing, logistic/linear models and metrics.
- LightGBM as nonlinear tabular challenger.
- `httpx` with bounded concurrency and retries for TypeSafe API calls.
- pytest plus property-based/fuzz tests for timestamp, cost, label and feed-state logic.
- Git for code/config and immutable experiment metadata.

Do not add Kafka, a cloud warehouse, a feature-serving service, a complex web dashboard or a separate low-latency execution service until local profiling proves it necessary.

---

## 5. Data capture and feed qualification

### 5.1 Verify feed capability before designing features

The Shoonya `Subscribe Market Feed` documentation describes one full snapshot per instrument followed by incremental updates (`tf`) in which only changed fields are sent. The client must merge updates into locally maintained state and resubscribe instruments after reconnect. The documented touchline `bp1`/`sp1` fields are Level 1. Do not describe this recorder as a five-level-depth recorder unless a separately verified feed product actually provides levels 1–5.

**Gate:** capture a short session and inspect the real raw messages before finalizing the schema. Record feed subscription, API/SDK version and the exact fields observed for each instrument type.

### 5.2 Raw event schema

For every received message, store at least:

- `event_id` (unique, deterministic or collision-resistant ID).
- `session_id` / connection ID and `subscription_generation`.
- `exchange`, `token`, `symbol_as_received`.
- `message_type` (`tk`, `tf`, error, connection event, etc.).
- `feed_timestamp_raw` and parsed `feed_timestamp_utc` where valid.
- `received_timestamp_utc` from a synchronized wall clock.
- `received_monotonic_ns` for local duration/latency calculations.
- `raw_payload_json` exactly as received.
- `parser_version`, `schema_version` and ingestion status.

Do not replace missing market data with zero. Preserve the missing field as absent/null, validate the state, and mark unusable observations explicitly.

### 5.3 Clock semantics

Use distinct fields:

- Feed timestamp: useful for source event-time and latency diagnosis, subject to documented semantics.
- Local receive timestamp: the earliest reliable point at which the local strategy could know the message.
- Decision timestamp: after processing and feature calculation.
- Simulated order arrival timestamp: decision time plus the declared latency assumption.
- Fill/exit timestamp: the actual or simulated event time.

Use UTC timestamps for storage and derive IST session time from a timezone-aware conversion. Do not store a naive timestamp named `timestamp_utc` unless it is genuinely UTC. Keep a wall-clock synchronization health check.

### 5.4 Quote-state reconstruction

Maintain state keyed by exchange and instrument token. For each initial `tk`, create/replace a validated initial state. For each `tf`, merge only the fields that are present. Do not reset unchanged fields to zero or null. Record raw updates even when the normalized state is invalid; flag the state invalid until a valid resubscription snapshot repairs it.

Track:

- time since last valid update;
- reconnect periods and re-subscription acknowledgement;
- message gaps or suspicious timestamp regressions;
- crossed/locked quotes;
- non-positive bid/ask prices;
- missing sizes/prices;
- out-of-range ticks relative to tick-size rules;
- duplicate/replayed messages if detectable;
- parser exceptions.

### 5.5 Persistence and rotation

- Append raw messages to Parquet in bounded batches.
- Rotate by time and/or maximum file size; do not create one tiny file per message.
- Flush on normal shutdown and handle SIGINT/SIGTERM.
- Use a durable manifest listing completed files, counts, time ranges, checksums and parser version.
- Do not mark an output file complete until writing and closing succeeded.
- After restart, detect unfinalized temporary files and quarantine/recover them explicitly.
- Partition primarily by trading date and feed type. Partition by symbol only if measurement shows it improves queries without producing excessive small files.
- Maintain an off-machine backup of completed raw data and a simple restore test.

### 5.6 Recorder acceptance tests

A recorder build passes only when:

1. A captured initial snapshot creates expected state.
2. A sparse incremental message changes only listed fields.
3. Unchanged fields remain unchanged.
4. Missing fields are not converted to zero.
5. An update without LTP can still update relevant quote state.
6. Reconnect causes all subscriptions to be restored and state reinitialized from new snapshots.
7. Invalid/stale quote data is marked, never silently accepted.
8. A forced process stop does not silently lose already completed batches.
9. Feed time and receive time remain separately queryable.
10. A replay of recorded raw events reconstructs the same state as the online parser for the same event sequence.

---

## 6. Data model and storage schema

Avoid a single mutable `signals` table that mixes market data, features, labels, predictions and outcomes. Separate tables by data responsibility so that features cannot accidentally read labels and model runs are reproducible.

### 6.1 Core tables

**`raw_events`**

Append-only source messages, as in Section 5.

**`quote_states`**

One row per reconstructed quote-state update or sampled state:

- `event_id`, `exchange`, `token`, `symbol`, `feed_timestamp_utc`, `received_timestamp_utc`;
- `bid_price_1`, `bid_qty_1`, `ask_price_1`, `ask_qty_1`;
- last traded price/volume and other genuinely provided fields;
- `quote_age_ms`, `state_valid`, `invalid_reason`, `parser_version`.

Do not create fictitious levels 2–5 when only L1 is available.

**`bars_1m`**

One row per symbol/minute, with the declared bar convention: OHLCV, bar start/end, whether the bar is complete, source and gap flags. Preserve the difference between a bar timestamp and the time its final values become available.

**`market_context`**

Timestamped Nifty/VIX/sector/breadth data, each with source time and local availability time. Join only observations available by the candidate decision time and impose maximum staleness.

**`candidate_signals`**

- `signal_id`, `strategy_version`, `symbol`, `side`, `decision_timestamp_utc`;
- source event ID, candidate creation time, intended horizon;
- feature snapshot ID and canonical feature hash;
- eligibility status and reason.

**`feature_snapshots`**

- `feature_snapshot_id`, `signal_id`, `as_of_timestamp_utc`, `feature_version`;
- explicit values and validity flags;
- source event IDs/timestamp lineage or a compact lineage manifest.

**`simulated_trades`**

- candidate ID, entry/exit side, decision/arrival/fill timestamps;
- expected/filled price, quantity, order-type convention, quote age;
- entry and exit turnover, gross P&L, fees by component, slippage and net P&L;
- fill status and simulation version.

**`forward_labels`**

- candidate ID, horizon, gross return, entry/exit executable reference prices;
- net P&L, net bps, market/sector adjusted outcome where defined;
- max favourable/adverse excursion and barrier outcome if implemented;
- label version and label availability interval.

**`model_predictions`**

- experiment ID, candidate ID, model family/version, input feature hash;
- predicted score/probability/value, prediction timestamp;
- latency/error/status fields and raw-response reference for JEV.

**`experiment_runs` and `trial_ledger`**

Code commit, data manifest, configuration hash, model version, prompt version, feature version, train/validation/test dates, trial status and results.

### 6.2 Data contracts

Use schema validation at every boundary. Reject or quarantine rows when required fields are absent; do not silently default important financial fields. Use explicit units in names/comments (`_bps`, `_pct`, `_qty`, `_price`, `_utc`). A code review must verify that all SQL columns exist in the declared schema and that the labels being updated were generated from a consistent source.

---

## 7. Feature engineering plan

### 7.1 Feature contract

Every feature must be reproducible from data observable no later than its declared `as_of_timestamp`. A valid timestamp alone is not proof of causality; the underlying bar/quote's actual availability must be respected.

Build features in one shared library used for historical replay and shadow mode. Never implement one version for backtesting and a slightly different version for live signals.

### 7.2 Initial features

| Feature | Definition / cautions | Initial priority |
|---|---|---|
| `spread_bps` | `10000 * (ask1 - bid1) / midpoint`; only for valid, fresh, positive quotes | Mandatory |
| `touch_size_imbalance` | `(bid_qty1 - ask_qty1)/(bid_qty1 + ask_qty1)` when sizes are valid; NULL/flag if denominator is zero | Optional initial feature |
| `atr_pct` | ATR over completed bars only, divided by price; freeze indicator definition and lookback | Medium |
| `relative_volume` | Current information versus a point-in-time intraday/time-of-day reference; never use final session volume | Medium |
| `dist_vwap_pct` | VWAP using only accumulated trades/volume available by decision time; define reset/session rules | Medium |
| `dist_pdh_pdl_pct` | Distance to prior completed session's high/low; ensure corporate action/session handling | Medium |
| `india_vix` / change | Latest published value available by the signal timestamp, with staleness limit | Medium |
| Nifty returns/trend | Prefer causal multi-horizon returns and volatility to a vague label; if categorical, freeze thresholds on training data | Medium |
| time-of-day | Minutes since open, cyclical representation or predeclared buckets | Medium |
| event flags | Only if event source and publication/availability time are recorded; no retrospective event classifications | Later |
| sector returns / breadth | Point-in-time contemporaneous context; useful after core pipeline is correct | Later |
| realized volatility | Causal multi-window estimates to complement ATR and help risk normalization | Later |
| order-flow velocity | Requires adequate event coverage and verified update cadence; do not infer from sparse L1 updates | Later |

### 7.3 Missing depth

The supplied Shoonya recorder demonstrates L1 touchline, not five-level depth. Do not calculate a field called `depth_imbalance` from L1 and describe it as five-level order-book imbalance. If only L1 is available, call the field `touch_size_imbalance` or similar. If richer depth is later purchased/validated, create a distinct data source and feature definition with its own coverage and quality checks.

### 7.4 Feature parity and ablation

The feature lists provided to Logistic/linear, LightGBM and JEV must carry the same underlying information for the primary experiment. Encoding may differ by model but must not introduce extra data into one model.

Perform ablation on training/validation only:

1. Price/return/time only.
2. Add volatility and volume.
3. Add spread and touch-size features.
4. Add broad market/sector context.
5. Add other features only when data quality and hypothesis warrant it.

If a feature group does not improve validation robustness, leave it out. Do not add fields merely because they are available.

### 7.5 Causality tests

Build tests where:

- future rows are appended to a dataset and previously computed features remain unchanged;
- a future bar's close/high/low/volume changes and earlier feature values do not change;
- changing any source timestamp to after the decision time makes the join reject or exclude the value;
- the live calculation and historical replay yield identical results from identical ordered observations;
- a deliberately leaked future column is rejected by the training feature allowlist.

---

## 8. Candidate strategy, execution and labels

### 8.1 Define candidate generation separately

JEV and supervised filters must receive the same candidate signals. Do not let one model choose direction while another only filters direction unless that is a separate experiment.

First experiment: an existing candidate strategy supplies `BUY`/`SELL` plus a decision timestamp. Each filter makes an `ACCEPT`/`REJECT` decision. This isolates selection quality.

### 8.2 Fix the trading policy

Before testing, define:

- quantity/notional per trade and any capital cap;
- whether signals can overlap in the same symbol;
- maximum concurrent positions and total exposure;
- duplicate-signal handling;
- entry latency, maximum quote age and valid market session;
- exit rule/horizon and square-off convention;
- whether the simulator models marketable or passive orders;
- how unavailable or ambiguous prices are handled;
- whether failed fills count as skipped trades or execution losses.

Do not treat every candidate as an independent simultaneously investable trade when the portfolio could not hold all candidates. For a first filter study, a fixed-notional, one-position-per-symbol policy may be adequate if preregistered and consistently applied.

### 8.3 Executable entry and exit conventions

For a conservative marketable-order approximation using L1 quotes:

- Long entry reference: valid ask at modeled arrival.
- Long exit reference: valid bid at modeled exit.
- Short entry reference: valid bid at modeled arrival.
- Short exit reference: valid ask at modeled exit.

This convention is a model of execution, not proof of an actual fill. If price data is only received locally, the exact exchange-arrival quote may not be observable. Use a declared latency delay, valid/stale-quote rejection, and sensitivity tests. Do not use the signal timestamp's mid-price as an executable fill without a documented reason.

Record the decision time, simulated order-arrival time, chosen quote timestamp and quote age. If the quote required for the simulated fill is too old or missing, mark the signal as unfillable under the primary policy rather than inventing a price.

### 8.4 Cost model: calculate each leg in currency first

Use the applicable dated schedule from the broker and exchange. Rates must be versioned by effective date. Based on the current Shoonya pricing page reviewed on 17 October 2026, equity intraday brokerage is listed as 0.03% or ₹5 per executed order, whichever is lower; NSE equity transaction charges are listed as 0.00307% per trade on buy and sell effective 1 March 2026. Confirm these rates against the broker's current schedule/contract note before a real execution test.

Calculate charges per actual/simulated executed leg:

- Brokerage, per executed order, following the schedule and cap.
- STT: applicable sell-side intraday rate.
- Exchange transaction fee on applicable turnover, for each side.
- SEBI turnover fee on applicable turnover.
- Stamp duty on applicable buy-side turnover.
- GST on the applicable taxable fee components (currently the broker schedule specifies brokerage, SEBI charges and transaction charges).
- IPFT/other segment levies when applicable and actually charged.
- Slippage and impact separately from statutory fees.

Keep fees in rupees until all entry/exit executions are computed. Then normalize:

`net_pnl = signed_gross_pnl - sum(all_fee_components) - modeled_slippage_and_impact`

`net_bps = 10000 * net_pnl / defined_entry_notional`

The denominator must be fixed and documented. Do not subtract a one-side `cost_bps` from a full round-trip return.

Test the fee function against hand-calculated cases at small and large notionals, both long and short, each effective rate date and partial fills. Brokerage is not a universal constant in basis points: the ₹5 cap means the effective rate varies with notional.

### 8.5 Cost sensitivity

Produce at least three scenarios:

- **Base:** broker charges plus conservative executable side prices and the chosen latency convention.
- **Stress 1:** spread/slippage assumption increased by a predeclared amount or factor.
- **Stress 2:** greater latency and slippage, with occasional unfilled/failed-exit cases.

Use the same scenario across all models. If the strategy is only profitable under an optimistic cost case, classify it as not established.

### 8.6 Forward labels

Primary target: 30-minute net outcome. Secondary horizons may include 5, 15 and 60 minutes plus EOD, but these are secondary outcomes and must not become a hidden way to select the best result.

For each candidate, label from the defined entry fill to the exit fill under the execution policy. Store gross P&L, fees, net P&L, net return/bps and timestamps. A target interval begins when the position could have been entered and ends when the label outcome is fully known.

**Short-market adjustment:** do not blindly subtract Nifty return from side-signed trade returns in the same way for BUY and SELL. If a beta-adjusted result is needed, estimate beta using training-only data and specify the side/exposure convention. Otherwise, report raw net return and a separately defined benchmark-relative portfolio measure.

### 8.7 Triple barrier is optional in stage one

Do not build sophisticated triple-barrier logic before fixed-horizon labels work. If added later:

- define target and stop as a function of training-only or point-in-time volatility;
- evaluate the earliest barrier touched using the best available time resolution;
- if both barriers can be touched within one bar and order is unknown, mark the case ambiguous or apply a conservative adverse assumption;
- do not infer intrabar order from OHLC extrema alone;
- record label interval start/end for purge logic;
- test edge cases at session close, gaps, missing bars, corporate actions and trading halts.

---

## 9. Baseline and JEV model protocol

### 9.1 Model ladder

Run in the following order:

1. **Unfiltered base signal:** reveals whether the candidate strategy has a net opportunity at all.
2. **Matched random filter:** randomly accepts at the same coverage/acceptance rate as each model, matched within predeclared day/session and optional liquidity/time strata.
3. **Cost-aware deterministic filter:** reject candidates whose training-only expected gross opportunity is insufficient relative to estimated cost plus a preregistered safety margin. Do not use future returns to estimate the expected move.
4. **Fixed mechanical rules:** small set of pre-registered spread, volatility and time-of-day filters. Avoid a huge grid search.
5. **Linear baseline:** choose logistic regression if the target is binary (e.g., net return > 0) or linear/ridge regression if the target is continuous net return. Define the task once and compare like with like.
6. **LightGBM:** regression/classification target matching the chosen task, with train-only preprocessing and bounded model complexity.
7. **JEV filter:** same candidates and equivalent feature information.
8. **LightGBM + JEV:** optional secondary stack, trained from out-of-fold JEV scores only; compare against LightGBM alone.

LightGBM is a plausible challenger, not guaranteed to win. A simple ruleset can outperform when the useful effect is mostly cost avoidance. A random filter should not systematically rank trade quality, but finite samples may make its observed result look good.

### 9.2 Fairness constraints

Every primary comparison must share:

- same eligible candidates and side;
- same point-in-time feature information;
- same labels and horizon;
- same execution and fee model;
- same test sessions;
- same risk/capital and overlapping-signal constraints;
- same target acceptance rate, or an explicitly defined coverage-performance curve;
- same missing-data policy.

Models can have different internal representations but must not receive extra information. Document unavoidable differences and do not make a clean superiority claim if differences remain.

### 9.3 JEV use

Use JEV as an accept/reject filter first. Direction generation is a separate experiment. Do not ask the first study to simultaneously solve candidate generation, direction, sizing and exit management.

The documented TypeSafe API currently uses `POST https://api.typesafe.ai/v1/systemone`, with `state`, `model` and named `questions`; response fields include `model`, `answers` and `usage`. Confirm the exact live contract from the official docs and run an API contract test before replay. Do not use the older `/v1/decisions`/`judgments`/`decision` sample without verification.

A JEV answer probability/confidence is a property of the model's answer, not automatically a calibrated probability of a profitable trade. It can be evaluated as a ranking/decision score; it must be empirically calibrated before using it as P(profit) or for position sizing.

### 9.4 JEV experiment configuration

Version and log:

- exact requested model name and exact model returned by the API;
- canonical state/payload and input hash;
- question ID, type, instructions, choices/criteria and prompt version;
- raw response, parsed answer, probabilities and confidence if returned;
- API request ID/HTTP metadata when available, latency, timeout, retry and failure reason;
- usage/cost fields provided by the provider;
- candidate ID and output timestamp.

Use a fixed question design and pin the exact model ID for each confirmatory run. `jev-latest` can be tested as a moving alias in a separate operational-drift study, but should not silently change inside one confirmatory replay.

Use bounded concurrency, a rate limiter, explicit timeouts, retry/backoff for transient server/overload failures, and durable persistence of every attempt. Do not launch one unbounded task per historical row. Batch multiple questions for a single state when the API supports it; do not assume an array of states is a batch of independent cases unless the API contract explicitly says so.

Failures must remain visible in the results. Report JEV coverage and failure rate; apply a predeclared failure policy (e.g., reject/skip/fallback) to the candidate stream and apply a fair policy to all relevant comparisons. Never silently drop failed JEV calls and evaluate only successful cases.

### 9.5 Prompt tuning and pretraining contamination

Tune prompts using training/validation periods only. Count every prompt variant as a trial. Freeze prompt/model/question versions before the final holdout.

If replaying data from dates that precede a model's release, memorization/contamination of well-known events is a potential concern. Prefer prospective data captured after model release for the cleanest JEV test. If historical replay is used, include an explicit limitation and consider anonymized-symbol/date sensitivity analyses, while recognizing anonymization does not prove contamination is absent.

---

## 10. Statistical validation and performance metrics

### 10.1 Split by time/session, not by random rows

Create chronological train, validation and locked test periods. The exact ratios depend on how much data is available, but the rules are fixed before model tuning. All securities/observations for a session must be assigned consistently at a boundary; avoid putting same-timestamp rows from one market event in both train and test.

The split unit is a complete trading session. Each label also has an outcome interval. Remove any training row whose label interval overlaps or reaches into the test interval. Purge using timestamps/label intervals, not a fixed count of rows. Any embargo is measured in actual time/sessions and justified by the horizon/dependency structure.

Use expanding-window walk-forward validation for model development. Fit imputation, scaling, encoding, feature selection, calibration and hyperparameter selection only on the training portion of each fold. Do not let validation or test distributions influence feature selection.

### 10.2 Locked holdout

The original 60-calendar-day holdout may serve as a preliminary untouched evaluation, often only about forty trading sessions. That is not strong evidence of robustness by itself. Plan for a longer final forward test when feasible (e.g., at least several months and ideally 120+ sessions), but let a prospective power calculation determine the actual requirement.

Do not repeatedly inspect the locked holdout. Run the final frozen policy once per preregistered confirmatory experiment. If the holdout influences a change, it has become development data; obtain a new holdout/prospective period for confirmation.

### 10.3 Primary unit of analysis

Use daily portfolio outcomes as the primary inference unit, because signals within a day and overlapping horizons are dependent. Trade-level net expectancy is an essential descriptive metric, but should not be treated as if every trade is independent.

For the primary model comparison, construct a paired daily difference:

`daily_diff[d] = JEV_policy_daily_net_pnl[d] - comparator_daily_net_pnl[d]`

Both policies must use identical sessions, candidate set, portfolio constraints, and cost assumptions. Bootstrap paired days/blocks—not two policy series independently.

### 10.4 Bootstrap and inference

- Use a day-block bootstrap for paired policy-return differences.
- Use a sensitivity check with multi-session blocks if serial dependence persists.
- Report effect size and confidence interval, not just a p-value.
- Preserve all-zero/low-trade days in the portfolio series where appropriate; don't condition the metric only on days JEV traded unless that is the preregistered estimand.
- Correct or control multiplicity for secondary model/horizon/feature tests.
- Treat 10,000 resamples as a computational choice, not a cure for a small number of independent sessions or repeated trials.

### 10.5 Metrics

**Primary:** paired difference in daily portfolio net P&L/return versus the preregistered comparator, with confidence interval and test.

**Secondary:**

- net expectancy per executed trade and per candidate;
- acceptance/coverage rate and trade count;
- Q1–Q5 (deciles only as later diagnostic) outcome curves;
- Q5 minus Q1, with uncertainty;
- hit rate and distribution of wins/losses;
- worst day, tail quantiles and maximum drawdown from a proper equity curve;
- turnover, capital utilization and simultaneous exposure;
- results by year/quarter, time of day, liquidity group and broad volatility regime (all predeclared or clearly exploratory);
- calibration reliability plot/Brier score only if a genuine probability of the outcome is claimed;
- API latency, availability and cost for JEV.

Do not use trade-level Sharpe/maximum drawdown computed from overlapping 30-minute returns as if they were a coherent daily portfolio. Build an actual sequence of portfolio P&L with positions, capital constraints and timing first.

### 10.6 Power and time-to-power

Do not set a minimum signal count by intuition alone. Use preliminary data to estimate the standard deviation/autocorrelation of the paired daily performance difference. Pick a minimum economically meaningful effect `delta_min`, significance level and desired power (typically 80% or more for planning), then estimate sessions required.

For an initial independent-observation approximation:

`n ≈ ((z_(1-alpha/2) + z_(power)) * sigma / delta)^2`

This formula is a sanity check, not the final answer. Use a day-block/bootstrap or simulation-based power calculation for the actual design. The input should be daily paired policy differences, not raw count of correlated trade rows.

Report progress in sessions, candidate rate, JEV accepted trade rate and effective sample size. If the expected time-to-power is unacceptably long, simplify the question or expand the prospectively valid universe—do not lower the significance requirement after seeing poor results.

### 10.7 Quintiles

Quintile analysis is a ranking diagnostic. Q5 positive expectancy is not enough if the acceptance policy is unprofitable or capacity-constrained. Strict monotonicity is useful evidence but should not be the sole pass/fail gate. Keep quintiles in the first report; add deciles only after sample depth is adequate and the analysis is pre-specified.

---

## 11. Acceptance criteria and kill rules

Do not decide in advance that JEV must win. Decide in advance what evidence is sufficient to continue.

### 11.1 Data gate: must pass before performance research

- Reconstructed-state tests pass against raw feed fixtures.
- Required fields, quote age, timestamp availability and market session checks are enforced.
- All pipeline outputs have version and input lineage.
- Raw file manifest/recovery and backup restore have been tested.
- No undocumented zero-fill for absent market fields.
- Data completeness and dropped-event rates are measured and within predeclared thresholds.

### 11.2 Label/cost gate

- Every label references valid source prices and times.
- Entry and exit conventions are implemented and documented.
- Each fee component is calculated per leg; hand-worked examples match the implementation.
- Long and short calculations pass unit tests.
- Schema validation finds no missing label columns.
- Changing future observations cannot alter earlier feature values.
- Ambiguous/missing paths are handled by predefined policy.

### 11.3 Baseline gate

- At least the unfiltered strategy, matched random, cost-aware rule, simple rules, linear baseline and LightGBM run end-to-end.
- All contestants use the same candidate stream, test dates, costs and portfolio rules.
- Model results are reproducible from a recorded manifest.
- Validation performance is not dependent on one symbol or a handful of days without being explicitly disclosed.

### 11.4 JEV evaluation gate

JEV is evaluated only after the API contract test, durable failure logging and frozen prompt/model version are operational. It must improve outcomes at matched coverage or yield a clearly stated coverage-performance tradeoff. If its apparent advantage disappears under cost stress or on the locked out-of-sample period, the edge is not established.

### 11.5 Final success criteria

A JEV advantage is **established for research purposes** only if all of the following hold:

1. Positive absolute net performance under the preregistered execution/cost scenario, with appropriate uncertainty.
2. A positive paired improvement over the predeclared primary comparator exceeding the predeclared economically meaningful threshold, with the prescribed confidence test.
3. No material leakage, feed corruption, selection or API-failure issue remains unresolved.
4. Performance is not wholly explained by a lower trade count/turnover unless that is explicitly the intended policy benefit.
5. Sensitivity analysis does not reveal that ordinary increases in costs/latency remove the benefit.
6. Evidence is not concentrated exclusively in one narrow time segment without an explicit limitation.
7. A prospective shadow period supports the offline findings.

If the advantage does not meet the gates, classify the result as **not established**, not automatically proof that JEV can never work.

---

## 12. Implementation roadmap and deliverables

### Sprint 0 — Research contract and repository

**Deliverables**
- `PREREGISTRATION.md`
- `config/universe.yaml`
- `config/cost_schedule.yaml` with effective-date versioning
- `config/experiment.yaml`
- repository layout, environment lockfile and structured logging
- trial ledger schema

**Pass gate**: one primary question, clear target/cost/fill policy, fixed universe rules, fixed primary comparator and locked holdout procedure.

### Sprint 1 — Feed spike and recorder

**Deliverables**
- Confirmed Shoonya API/SDK endpoint and message contract.
- Short raw session capture.
- Raw Parquet append-only recorder, file manifest and recovery test.
- State reconstruction keyed by token.
- Data-health report.

**Pass gate**: tests in Section 5.6 pass and observed feed capabilities match the feature plan.

### Sprint 2 — Point-in-time feature pipeline

**Deliverables**
- Causal minute-bar and L1 feature calculations.
- Market-context as-of joins.
- Feature lineage/versioning and validity flags.
- Feature parity test between replay and shadow computations.
- Future-append causality tests.

**Pass gate**: no future observation changes an already computed feature; stale/missing data is handled consistently.

### Sprint 3 — Execution simulator and cost engine

**Deliverables**
- Currency-level per-leg fee calculator.
- Buy/sell side-by-side executable-price convention.
- Latency and quote-age policy.
- Fixed-horizon labeler, primarily 30m.
- Label interval and fill-status persistence.
- Unit/property tests for costs and returns.

**Pass gate**: fee hand calculations match, round-trip long/short tests pass, all schema fields align and the labeler replay is deterministic.

### Sprint 4 — Validation framework and unfiltered/random/rule baselines

**Deliverables**
- Session-based chronological splitter.
- Label-interval purge and documented embargo.
- Locked-holdout access protection.
- Unfiltered candidate baseline.
- Same-day-/context-matched random selector with repeated draws.
- Cost-aware filter and a small pre-registered rule set.

**Pass gate**: split audit proves no train label overlaps test information; random selector and rules use the same evaluation policy.

### Sprint 5 — Linear baseline

**Deliverables**
- Define the task: binary profitability classification or continuous net-return regression.
- Fit preprocessing inside each training fold only.
- Out-of-fold predictions and validation learning curve.
- Threshold/acceptance-rate selection using validation only.

**Pass gate**: the model improves or fails transparently; no holdout tuning; output manifest is reproducible.

### Sprint 6 — LightGBM challenger

**Deliverables**
- Correctly matched LightGBM task.
- Small preregistered hyperparameter range and limited tuning.
- Feature ablations conducted on training/validation only.
- Learning curve vs training size.
- Model/version/prediction ledger.

**Pass gate**: fair comparison with linear model under the same policy constraints and feature information.

### Sprint 7 — JEV integration and replay

**Deliverables**
- Contract-tested `/v1/systemone` integration based on official docs.
- Frozen model/question/prompt definition.
- Bounded concurrency, rate limiting, timeout/backoff.
- Durable request, response, usage, latency and failure store.
- JEV score/answer evaluation and matched-coverage threshold chosen on validation.
- Trial ledger updated for every variant.

**Pass gate**: all attempts are accounted for; failed calls are not silently removed; the same frozen request produces a traceable result.

### Sprint 8 — Locked evaluation, sensitivity and report

**Deliverables**
- One frozen evaluation run.
- Paired daily policy difference with block-bootstrap confidence interval.
- Secondary metrics and multiple-testing disclosures.
- Costs/latency stress results.
- Stability and concentration report.
- Signed experiment manifest and final verdict: success, not established, or reject JEV for this policy.

**Pass gate**: the final output is reproducible and the locked holdout was not used to modify the system.

### Sprint 9 — Prospective shadow operation

**Deliverables**
- Daily recorder-health report and uptime log.
- Live-vs-replay feature parity monitoring.
- Shadow decisions with no order placement.
- Alerting for feed gaps, schema/API changes, disk capacity and clock drift.
- Comparison of expected versus contemporaneously observable execution assumptions.

**Pass gate**: stable data and decision flow over a predeclared shadow period; no unhandled operational failure; results are consistent with research expectations.

### Sprint 10 — Optional regulated paper/live readiness

Only after the research and shadow gates pass:

- verify broker/API permissions and current regulatory requirements;
- verify required static IP, authentication/session controls and order-type restrictions with the broker and current exchange/regulatory documents;
- add order validation, duplicate-order protection, position reconciliation, hard risk limits, emergency kill switch and auditable order logs;
- test disconnect/reconnect and restart recovery;
- begin with paper trading, then only consider small, explicitly authorized live exposure after independent review.

This sprint is **not needed** to answer the original research question and must not be started just because a backtest looks promising.

---

## 13. Repository layout

```text
jev-imrp/
  config/
    universe.yaml
    feature_config.yaml
    cost_schedule.yaml
    execution_policy.yaml
    experiments/
      baseline_v1.yaml
      jev_v1.yaml
  data/
    raw/                 # immutable captured messages
    normalized/           # reconstructed states and bars
    features/             # versioned point-in-time feature snapshots
    labels/               # versioned labels and simulated fills
    manifests/            # file and data lineage manifests
  db/
    research.duckdb      # rebuildable analytics store, not only source of truth
  src/
    ingest/
      shoonya_recorder.py
      feed_state.py
      file_writer.py
      health_checks.py
    data/
      instrument_master.py
      bars.py
      context_join.py
      quality.py
    features/
      definitions.py
      compute.py
      causal_checks.py
    execution/
      fills.py
      cost_model.py
      portfolio.py
    labels/
      forward_returns.py
      triple_barrier.py  # optional later
    validation/
      temporal_splits.py
      purge.py
      bootstrap.py
      power.py
    models/
      random_filter.py
      rules.py
      linear_model.py
      lightgbm_model.py
      jev_client.py
      stacking.py         # only after JEV validation
    evaluation/
      metrics.py
      comparisons.py
      reports.py
    ops/
      daily_health.py
      alerts.py
  tests/
    test_feed_state.py
    test_timestamps.py
    test_features_causal.py
    test_cost_model.py
    test_labels.py
    test_splits.py
    test_api_contract.py
    test_replay_parity.py
  experiments/
    trial_ledger.csv
    runs/                 # immutable config/manifests/results
  reports/
  PREREGISTRATION.md
  pyproject.toml          # pinned dependencies
  README.md
```

The DuckDB file should be rebuildable from versioned input data and manifests. Do not make a mutable database file the only copy of raw evidence.

---

## 14. Daily operations and reliability

Even a research recorder must have reliable operations; otherwise missing data can be correlated with volatile days and contaminate conclusions.

### 14.1 Daily checklist

- Pre-market: authenticate, verify instrument mapping, confirm subscriptions and disk space.
- At session open: confirm a full snapshot for every expected instrument and timestamp health.
- During session: monitor connection heartbeat, received message count, quote age, parse errors, state validity and write lag.
- On disconnect: mark the gap, reconnect, resubscribe all instruments and wait for new snapshots before re-enabling features.
- On session close: flush files, close handles, write manifest, record coverage and generate a QA summary.
- After close: run normalization, feature and label jobs idempotently; compare row counts and data-quality metrics; back up raw files.

### 14.2 Alerts and stop conditions

Stop emitting candidate decisions (not necessarily stop raw capture) when:

- critical quotes are stale beyond the declared threshold;
- a subscription lacks a fresh snapshot after reconnect;
- timestamps regress or clock synchronization fails;
- the parser detects an unknown message/schema version;
- raw write backlog or disk usage exceeds safe thresholds;
- the current session/instrument is ineligible or trading is halted;
- the model API is outside its failure/latency policy.

All stops and restarts must be logged. Never backfill the missing live interval with future data and label it as if it were available in real time.

### 14.3 Security and reproducibility

- Store broker/API secrets in environment/secret storage, never in source control or payload logs.
- Redact tokens and credentials from logs.
- Pin packages and record Python/runtime versions.
- Record code commit, configuration and input manifests for each result.
- Add a tested backup/restore path.
- Make every transformation idempotent or versioned, so reruns cannot silently duplicate outcomes.

---

## 15. Risk register and mitigations

| Risk | Severity before fix | Mitigation | Gate |
|---|---|---|---|
| Incremental feed mishandled | Critical | Raw storage, state merge and fixture tests | Before features |
| L1 mistaken for L5 depth | High | Use accurate naming; verify subscription capability | Before feature claims |
| Lookahead in bars/context | Critical | Availability timestamps, as-of joins, future-append tests | Before labels/models |
| Row-based purge | High | Session splits and actual label-interval purge | Before model comparison |
| One-sided cost model | Critical | Per-leg currency fees + execution price model | Before performance interpretation |
| Different model inputs | High | Shared candidate/feature contract and parity audit | Before comparison |
| Failed JEV requests dropped | High | Persist every attempt and predeclare failure policy | Before JEV comparison |
| Prompt/model changes | High | Pin versions; trial ledger; holdout lock | Before confirmation |
| Small effective sample | High | Daily paired power analysis; longer prospective observation | Before strong claims |
| Selection/multiple testing | High | Preregister, count all trials, adjust secondary tests | All stages |
| Unachievable fills | High | Entry/exit quote convention, latency and cost stress | Before paper claim |
| Universe selection bias | Medium prospectively / high retrospectively | Freeze prospective universe or reconstruct point-in-time eligibility | Before broad generalization |
| Operational data outages | Medium/high | Health monitoring, gap marking, restart and backup tests | Shadow stage |
| Model API version drift | High | Pin model, log returned version and run contract tests | Before JEV replay |
| Live regulatory/order restrictions | Critical before live | Verify broker/exchange/SEBI requirements before any live order | Before live routing |

---

## 16. Decision dashboard and report format

Every experiment report must include these sections in this order:

1. **Experiment manifest:** dates, universe, code commit, feature/label/cost versions, model/question/prompt version.
2. **Data health:** coverage by symbol/session, invalid quotes, stale observations, gaps, late events, duplicate records and rejected labels.
3. **Candidate stream:** candidate count, signal side, eligible/excluded counts and reasons.
4. **Execution assumptions:** entry/exit convention, notional, latency, quote-age cutoff, fee schedule version and cost scenarios.
5. **Primary policy result:** paired daily net performance versus comparator with confidence interval and predeclared test.
6. **Baseline table:** unfiltered, matched random distribution, cost-aware filter, rules, linear model, LightGBM, JEV and optional stack.
7. **Coverage/ranking:** accepted rate, quintile performance and Q5–Q1 difference.
8. **Risk and robustness:** drawdown, tail days, turnover, regime/time/symbol concentration and cost sensitivity.
9. **Uncertainty/multiplicity:** sample sessions, number of effective units, all trials and adjusted/secondary analyses.
10. **Failure/latency:** JEV error/timeout rate, coverage, p50/p95/p99 latency and API cost where available.
11. **Decision:** pass, fail or inconclusive against each registered gate.
12. **Next action:** one explicit next step—not a new search across many parameters.

A chart or table showing only Q5 expectancy, win rate or headline Sharpe is not a sufficient report.

---

## 17. Final stop/go framework

### GO to model development when

The raw recorder, feature causality and cost/label engine pass all correctness tests.

### GO to JEV replay when

The unfiltered and simple baseline pipeline is reproducible, fair, and the JEV API integration is contract-tested with full failure accounting.

### GO to locked confirmation when

The candidate strategy and JEV policy are frozen, the primary comparator/metric is locked, power requirements are documented and no tuning remains on the test data.

### GO to prospective shadow when

The locked evaluation supports an economically meaningful benefit and the data/execution sensitivity analysis is credible. Continue to run without placing orders and compare predicted versus realized/shadow outcomes.

### GO to live readiness review only when

Research and shadow gates pass, broker permissions and current regulations are verified, risk controls are implemented, restart/reconciliation tests pass, and a separate human approval authorizes any live activity.

### STOP or pause when

- the underlying candidate signal is unprofitable after costs and no credible policy-level improvement is found;
- JEV's performance does not exceed a simple cost/rule baseline after valid validation;
- results depend on a broken data feed, leaked features, one narrow date range or an unrepeatable prompt/model version;
- the expected time-to-power or operating cost exceeds available resources;
- JEV's incremental value is not large enough to justify API cost, latency and failure modes.

A negative result can still be a project success if it is obtained correctly and prevents more expensive overengineering or live losses.

---

## 18. What not to build yet

Defer until results create a specific justification:

- full five-/20-level order-book storage if the available subscription does not provide it;
- order queue/market impact modelling for large positions when the initial study is explicitly tiny-size, marketable-order paper evaluation;
- option surface features;
- dozens of technical indicators and regime classifiers;
- a massive prompt grid or autonomous prompt optimizer;
- high-availability multi-node streaming infrastructure;
- dashboards that precede a trusted data/model pipeline;
- a live execution bot;
- elaborate triple-barrier labels before fixed-horizon labels are verified.

Prioritize evidence over features.

---

## 19. Final recommendation

**Decision: REVISE, THEN PROCEED IN STAGES.**

The idea is worth a controlled, low-cost research effort. The current implementation should not be used to claim alpha. First repair feed reconstruction, timestamps, schema/labels, cost modelling, chronological validation and model parity. Then establish whether the unfiltered candidate strategy has any real net opportunity. Only then evaluate JEV, and explicitly test whether it adds value to an already strong simple model.

The platform should be considered successful if it reaches a reliable answer—not only if JEV wins.

---

## 20. Primary references to re-check before implementation/go-live

These are external references, not substitutes for testing the installed SDK or confirming the applicable broker terms.

1. **Shoonya market feed subscription documentation** — describes the full initial snapshot, incremental field updates, client-side state maintenance, resubscription after reconnect and L1 touchline fields: https://shoonya.com/api-documentation/subscribe-market-feed
2. **Shoonya official pricing page** — fee rates and effective-date schedules: https://shoonya.com/pricing
3. **Shoonya brokerage/charges FAQ** — equity intraday brokerage wording: https://shoonya.com/faq/pricing/what-are-your-brokerage-and-other-charges/
4. **TypeSafe API documentation** — current `/v1/systemone` request/response contract and model discovery: https://docs.typesafe.ai/api
5. **NSE real-time data subscription** — distinguishes Level 1, Level 2 (five best bid/ask levels), Level 3 and tick-by-tick data products: https://www.nseindia.com/static/market-data/real-time-data-subscription
6. **SEBI February 2025 retail algorithmic trading circular** — review current applicability before live routing: https://www.sebi.gov.in/legal/circulars/feb-2025/safer-participation-of-retail-investors-in-algorithmic-trading_91614.html
7. **NSE implementation standards / FAQs for retail algo trading** — verify latest exchange implementation requirements with the broker before live use: https://nsearchives.nseindia.com/content/circulars/INVG67858.pdf

**Reference verification note:** The cited vendor and regulatory pages were checked on 17 October 2026. Re-check the broker's then-current charges, API contract, model catalogue and applicable exchange/regulatory standards at implementation time, especially before enabling orders.
