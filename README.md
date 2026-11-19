# JEV-IMRP

Research platform for one question (see `PREREGISTRATION.md`):

> Given the same candidate trades and point-in-time information, does a frozen
> JEV decision policy improve net portfolio outcomes over pre-registered simple
> rules and a supervised model, under realistic Indian cash-equity costs and
> execution assumptions?

The authoritative specification is
[`JEV_IMRP_Complete_Implementation_Plan_v2.md`](JEV_IMRP_Complete_Implementation_Plan_v2.md).
The repository contains the research contract and working components for
ingestion/replay, causal features, labels, execution and cost modelling,
validation, baselines, JEV attempt handling, configuration and the trial
ledger. The direct FYERS read-only handoff to JEV is implemented; no
confirmatory research result is claimed here.

> ## ⚠️ Research only — no live trading
>
> This project is research, historical replay and (later) shadow/paper
> observation only by default. Any execution surface remains separately gated
> and defaults to no live orders; no live order may be sent without the
> separate operational and regulatory gate in Sprint 10 of the plan.
> The fee schedule must be re-checked against the broker's contract note before
> any execution-relevant result; nothing here may be used to justify real money.

> ## 🔌 Provider status and broker boundary
>
> **Direct FYERS v3 is the sole forward provider boundary for research-only
> market-data capture.** OpenAlgo is retired from active development and is
> retained only as immutable historical qualification evidence. Direct FYERS
> credentials, source-time fidelity, replay lineage and live qualification
> remain separate gates.
>
> The runtime must record the provider as `fyers` and must not silently fall
> back or switch brokers.
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

- Credentialed market-hours FYERS L1/depth provenance, source-time fidelity,
  reconnect behavior and replay lineage for the socket recorder.
- Licensing/retention approval, exposed-key containment, broker account-tier
  evidence and independent review of live qualification artifacts.
- The research data, label, cost, baseline and JEV evaluation gates in the
  implementation plan; the live smoke is integration evidence, not research
  acceptance.

Direct FYERS remains unqualified for research conclusions until source-time,
bid/ask fidelity, reconnect behavior, upstream provenance and replay lineage
are independently verified. The historical OpenAlgo qualification procedure
is retained as immutable evidence in
[`docs/OPENALGO_FYERS_QUALIFICATION.md`](docs/OPENALGO_FYERS_QUALIFICATION.md).
The current project-wide checkpoint is recorded in
[`docs/PROJECT_COMPLETION_AUDIT_2026-11-19.md`](docs/PROJECT_COMPLETION_AUDIT_2026-11-19.md).

The direct read-only smoke path is opt-in:

```powershell
uv run python scripts/fyers_live_jev.py --symbol RELIANCE
```

It loads the ignored `.env` file, requests the latest FYERS quote, and sends
one sanitized state to JEV. If FYERS returns `401`, it opens the configured
loopback OAuth callback, waits for the user-mediated login, exchanges the
one-time authorization code, atomically replaces `FYERS_ACCESS_TOKEN`, and
retries the quote once. It never calls an order, position, or account-trading
endpoint.

The first research history slice can be fetched for the frozen 20-symbol
universe with the documented, read-only history endpoint:

```powershell
uv run python scripts/fyers_history_research.py `
  --from 2026-10-17 --to 2026-11-18 --resolution 1D
```

The command writes an ignored `data/research/fyers_history/` dataset and a
SHA-256 manifest. It uses a conservative request delay and fails closed on an
expired token or any non-research endpoint.

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

The test suite, Ruff, mypy, formatting, compilation and diff checks are the
currently verified offline quality gates. Green offline checks do not establish
live-data qualification, licensing, independent review or research acceptance.

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
