# Research-phase status — 2026-11-22

This checkpoint describes what is presently evidence-backed. It does not
authorize order placement or convert exploratory data into a confirmatory
result.

## Evidence completed

| Area | Evidence | Status |
|---|---|---|
| Provider boundary | Direct FYERS v3 REST history; no OpenAlgo route | Verified |
| Frozen universe | 20 exact NSE cash-equity symbols from `config/universe.yaml` | Verified |
| Daily history | 440 validated OHLCV rows, 2026-10-17 through 2026-11-18 | Exploratory |
| Five-minute history | 31,780 validated OHLCV rows; 1,589 per symbol | Exploratory |
| Close-to-close diagnostic | 29,140 same-session 30-minute candle-return observations across 20 symbols | Exploratory only |
| Dataset integrity | Zero duplicate symbol/timestamp pairs and zero invalid OHLCV rows | Verified |
| Provenance | Ignored manifests record endpoint, range, symbol list, retrieval time and SHA-256 | Verified |
| Order safety | Research mode and direct scripts expose no order/position/modify/cancel call | Verified |
| Socket capture tooling | Read-only `SymbolUpdate` + `DepthUpdate` recorder command implemented; market-hours run still required | Tooling ready |
| Direct FYERS → JEV bridge | Validated `QuoteState` rows publish into the shared causal `FeatureEngine`; offline regression test passes | Verified offline |

Runtime datasets are under ignored `data/research/`; they are not silently
treated as versioned source artifacts. Re-fetch with
`scripts/fyers_history_research.py` and retain the generated manifest with the
dataset when using it outside this workspace.

## Research gates still blocking confirmatory labels or conclusions

1. FYERS history is OHLCV only. It cannot provide the bid/ask touchline needed
   by `execution.fills.simulate_fill`; no fill price may be inferred from a
   candle close, midpoint, or OHLC value.
2. The direct FYERS socket recorder is parser/recorder-ready but still lacks a
   credentialed market-hours capture proving source-time fidelity, depth/L1
   coverage, reconnect behavior, stale gaps, and replay parity.
3. The cost schedule contains an unresolved official IPFT/GST conflict and
   contract-note parity is pending. Cost-sensitive labels must remain blocked.
4. The research contract is `draft_unapproved`; preregistration amendment,
   licensing/retention approval, tick evidence, security/key-containment
   evidence, and independent review remain external gates.
5. The locked holdout is dated 2027, so the current November 2026 data cannot
   be presented as the locked confirmatory evaluation.
6. The previous FYERS access token was expired. The user-mediated `--reauth`
   flow completed successfully and the current token is valid through
   2026-11-23 00:30 UTC. The one-minute history attempt still failed closed and
   wrote no partial artifact; refresh remains user-mediated and must not be
   bypassed.
7. After the explicit refresh, the one-minute request identified one malformed
   FYERS ADANIENT candle in the requested range (`open=2730.0` while
   `low=2736.6`). The normalizer rejected the complete symbol slice; the value
   was not repaired or silently removed. This is provider-data quality evidence,
   not a research result.

## Safe next sequence

1. Capture direct FYERS `SymbolUpdate` and `DepthUpdate` packets during market
   hours with raw payload, source timestamp, receipt timestamp, session and
   reconnect metadata.
2. Run the unchanged data-health and replay-parity gates.
3. Obtain the required owner/licensing/reviewer approvals and freeze any
   remaining contract amendments before label generation.
4. Generate causal features, execution labels, cost scenarios, baselines and
   JEV replay only after the preceding gates pass.

All observations in this document are research-only. No live order is
authorized, placed, modified, or cancelled.

## Local verification at this checkpoint

The repository verification completed on `main` after the changes recorded
here: `uv run pytest -q` (498 passed), `uv run ruff check .`,
`uv run ruff format --check .`, `uv run mypy src`,
`uv run python -m compileall -q src scripts`, and `git diff --check` all pass.
