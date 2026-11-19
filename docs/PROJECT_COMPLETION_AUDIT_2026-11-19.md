# JEV-IMRP project completion checkpoint — 2026-11-19

## Verified locally

- Direct FYERS v3 latest-quote transport is implemented and is the forward
  provider path. It maps exact `NSE:<SYMBOL>-EQ` symbols, validates a positive
  bid/ask touchline, sends one sanitized state to JEV, and writes a redacted
  result artifact.
- Expired FYERS access tokens fail closed, launch the user-mediated loopback
  OAuth flow, exchange the one-time authorization code, atomically update the
  ignored `.env` token, and retry the read-only quote exactly once.
- No code in the direct path calls an order, position, account-trading,
  modify, or cancel endpoint. The live smoke result records order flags as
  false.
- The live read-only smoke completed during NSE market hours using
  `scripts/fyers_live_jev.py --symbol RELIANCE`. FYERS returned a quote and
  JEV returned a completed decision; the decision was `reject` and no order
  action was attempted. The generated artifact is intentionally ignored under
  `data/live/fyers_jev/` because it is runtime data.
- Offline quality gates pass at this checkpoint:
  - `uv run pytest -q`: 489 passed
  - `uv run ruff check .`: passed
  - `uv run ruff format --check .`: 150 files formatted
  - `uv run mypy src`: 64 source files, no issues
  - `uv run python -m compileall -q src scripts tests`: passed
  - `git diff --check`: passed

## Not claimed complete

These gates require evidence or authorization outside the source tree and
remain explicitly open:

- Credentialed FYERS market-hours socket qualification across the required
  universe, including reconnect, keepalive, source-time fidelity, depth
  coverage, dropped-event limits and replay lineage.
- FYERS account-tier/quota evidence, licensing and retention approval, and
  independent review of live qualification artifacts.
- Contract-note confirmation for the active cost schedule and resolution of
  the documented official-source conflict.
- Full preregistered data, label, baseline, JEV replay, locked holdout and
  statistical acceptance gates. A live smoke is integration evidence only.
- Human approval, hosted CI/PR review, GitHub Project metadata and any other
  external governance state. No live order placement is authorized by this
  checkpoint.

OpenAlgo code and qualification artifacts remain only as historical,
explicitly labeled evidence. They are not used by the direct FYERS smoke path
and must not be reactivated as a broker substitute.
