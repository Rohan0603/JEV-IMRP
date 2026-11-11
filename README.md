# JEV-IMRP

Research platform for one question (see `PREREGISTRATION.md`):

> Given the same candidate trades and point-in-time information, does a frozen
> JEV decision policy improve net portfolio outcomes over pre-registered simple
> rules and a supervised model, under realistic Indian cash-equity costs and
> execution assumptions?

The authoritative specification is
[`JEV_IMRP_Complete_Implementation_Plan_v2.md`](JEV_IMRP_Complete_Implementation_Plan_v2.md).
The repository contains the research contract and working offline components
for ingestion/replay, causal features, labels, execution and cost modelling,
validation, baselines, JEV attempt handling, configuration and the trial
ledger. A completed FYERS migration or confirmatory research result is not
claimed here.

> ## ⚠️ Research only — no live trading
>
> This project is research, historical replay and (later) shadow/paper
> observation only by default. The OpenAlgo gateway has a separately gated
> execution surface, defaulting to Analyze/Sandbox; no live order may be sent
> without the separate operational and regulatory gate in Sprint 10 of the plan.
> The fee schedule must be re-checked against the broker's contract note before
> any execution-relevant result; nothing here may be used to justify real money.

> ## 🔌 Provider status and broker boundary
>
> **OpenAlgo pinned to FYERS is the active provider boundary for research-only
> market-data capture.** The repository contains offline-tested, data-only
> gateway and replay boundaries. Credentialed gateway freshness, source-time
> fidelity, replay lineage and migration acceptance are not yet verified.
>
> OpenAlgo may connect to multiple brokers, but this project records the
> upstream provider as `fyers` and must not silently fall back or switch it.
> Historical datasets retain their original provider labels in immutable
> manifests; no legacy broker runtime or onboarding path is shipped.

## Layout

```text
config/
  universe.yaml            frozen 20-name NSE cash-equity universe + eligibility rule
  cost_schedule.yaml       dated FYERS/NSE fee draft (public rates checked 2026-11-07; contract note pending)
  execution_policy.yaml    sizing, overlap, latency, quote age, session, fill policy
  feature_config.yaml      feature definition/version registry (computation lands in Sprint 2)
  experiment.yaml          frozen research-contract pointers and holdout rules
  experiments/
    baseline_v1.yaml       baseline ladder skeleton
    jev_v1.yaml            JEV challenger skeleton (contract NOT_VERIFIED)
src/jev_imrp/ingest/       provider clients, raw events, replay state and file writing
src/jev_imrp/features/     causal feature definitions and computation
src/jev_imrp/labels/       forward-return labels
src/jev_imrp/execution/    fills, costs and portfolio accounting
src/jev_imrp/validation/   temporal splits, purge and holdout protection
src/jev_imrp/models/       rules, linear baseline and JEV client boundaries
src/jev_imrp/common/       config loading, errors, redacted logging and time helpers
tests/                     deterministic tests; no network, no credentials
experiments/
  trial_ledger.csv         append-only trial record (see TRIAL_LEDGER.md)
  runs/                    immutable per-run config/manifests/results
data/{raw,normalized,features,labels,manifests}/   gitignored, populated by later sprints
db/research.duckdb         rebuildable analytics store, not the source of truth
reports/                   generated reports
```

`data/`, `db/`, `reports/` output and `experiments/runs/` content are ignored by
git; the DuckDB file must always be rebuildable from versioned inputs and
manifests (plan 13).

## Current gates

The following are intentionally unresolved and must not be bypassed:

- OpenAlgo credentialed market-hours L1/depth provenance and source-time
  fidelity, with the upstream provider pinned and recorded as FYERS.
- A dated, prospective provider-switch amendment before collecting or
  analysing new OpenAlgo/FYERS results.
- Licensing/retention approval, exposed-key containment, and reproducible
  offline quality gates (pytest, Ruff and mypy).

OpenAlgo/FYERS remains unqualified for research conclusions until source-time,
bid/ask fidelity, upstream provenance and replay lineage are independently
verified. Analyze-mode execution and synthetic/offline tests are not evidence
of live-data or live-order acceptance. The recorder-backed qualification
procedure and precommitted gates are documented in
[`docs/OPENALGO_FYERS_QUALIFICATION.md`](docs/OPENALGO_FYERS_QUALIFICATION.md).

## Setup

Pinned to Python 3.14 (supports 3.12–3.14):

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
cp .env.example .env   # placeholders only; fill real secrets locally, never commit
```

## Running the checks

```powershell
pytest                 # deterministic; no network, no credentials
ruff check .           # lint
mypy                   # strict type check
```

The test suite is the currently verified offline gate. Ruff and mypy remain
separate quality gates until their existing baseline findings are resolved;
green pytest alone does not establish migration or research acceptance.

Config validation is exercised by the tests and can be run directly:

```powershell
python -c "from jev_imrp.common import config; print(sorted(config.load_universe()))"
```

To inspect the frozen contract and where the unresolved items are, read
`PREREGISTRATION.md` section 7 and grep for `TBD` / `UNVERIFIED` in `config/`.

## Dataframe engine

pandas is the single primary dataframe engine (plan 4.2 requires choosing one).
scikit-learn and LightGBM consume pandas/numpy objects directly, so pandas keeps
the model path conversion-free; DuckDB interoperates over Arrow without an extra
dependency. Revisit only if a measured profile shows pandas is limiting.
