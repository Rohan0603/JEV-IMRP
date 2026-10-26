# JEV-IMRP

Research platform for one question (see `PREREGISTRATION.md`):

> Given the same candidate trades and point-in-time information, does a frozen
> JEV decision policy improve net portfolio outcomes over pre-registered simple
> rules and a supervised model, under realistic Indian cash-equity costs and
> execution assumptions?

The authoritative specification is
[`JEV_IMRP_Complete_Implementation_Plan_v2.md`](JEV_IMRP_Complete_Implementation_Plan_v2.md).
This repository currently contains the Sprint 0 research contract and
foundations only: configuration, pre-registration, trial ledger and shared
utilities. Ingestion, features, labels, models and evaluation arrive in later
sprints.

> ## ⚠️ Research only — no live trading
>
> This project is research, historical replay and (later) shadow/paper
> observation only. It contains **no order-placement code, no broker
> credentials, and no live trading capability**, and none may be added without
> passing the separate operational and regulatory gate in Sprint 10 of the plan.
> The fee schedule must be re-checked against the broker's contract note before
> any execution-relevant result; nothing here may be used to justify real money.

> ## 🔌 Shoonya broker contract (Sprint 1)
>
> The broker is **Shoonya** (Finvasia). All broker interactions go through
> `tools/shoonya_instrument_fetch.py`, which uses the official
> [`NorenRestApiPy`](https://github.com/Shoonya-Dev/ShoonyaApi-py) SDK.
>
> **OAuth is the only supported login flow** — the old TOTP auto-login
> (`login(uid, password, twoFA, vendor_code, ...)`) is rejected by the live API
> with `Invalid Vendor code`. The correct flow is:
>
> 1. Redirect to `https://api.shoonya.com/OAuthlogin/authorize/oauth?client_id=<CLIENT_ID>`
> 2. Capture the `code` from the redirect URL
> 3. Exchange it via `GenAcsTok` with
>    `checksum = SHA256(client_id + secret_code + auth_code)`
> 4. Use the returned `susertoken` as the `Authorization: Bearer` token
>
> The script handles steps 1–3 automatically (local redirect server on
> `localhost:8080`) and step 4 via the SDK's `getAccessToken()`.
>
> **Endpoint paths matter.** The SDK ships with `NorenWClientTP/` but the live
> API only accepts `NorenWClientAPI/`. The old path returns 502 Bad Gateway;
> the documented path returns `400 Invalid Vendor code` for bad credentials
> and `200 Ok` for good ones. Always verify against
> [`shoonya.com/api-documentation`](https://shoonya.com/api-documentation)
> before changing anything broker-related.
>
> **`searchscrip` is fuzzy.** It matches partial trading symbols, so `"SBIN"`
> returns `SBINMID150-EQ` (token 761741) before `SBIN-EQ` (token 3045). The
> script requires an exact `SYMBOL-EQ` match; see the match logic in
> `tools/shoonya_instrument_fetch.py`.
>
> **Symbol masters.** Downloadable per exchange at
> `https://api.shoonya.com/<EX>_symbols.txt.zip` (NSE, BSE, NFO, CDS, MCX,
> BFO). These bypass login entirely and are useful for cross-checking tokens.
>
> **Universe note.** The original `TATAMOTORS` entry was replaced with
> `TVSMOTOR-EQ` (token 8479) because no instrument named `TATAMOTORS` exists on
> any Shoonya exchange symbol master. All 20 tokens are now resolved.

## Layout

```text
config/
  universe.yaml            frozen 20-name NSE cash-equity universe + eligibility rule
  cost_schedule.yaml       per-leg statutory fees, effective-date versioned (verified 2026-10-17)
  execution_policy.yaml    sizing, overlap, latency, quote age, session, fill policy
  feature_config.yaml      feature definition/version registry (computation lands in Sprint 2)
  experiment.yaml          frozen research-contract pointers and holdout rules
  experiments/
    baseline_v1.yaml       baseline ladder skeleton
    jev_v1.yaml            JEV challenger skeleton (contract NOT_VERIFIED)
src/jev_imrp/common/       config loading, error types, logging with redaction, tz helpers
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
