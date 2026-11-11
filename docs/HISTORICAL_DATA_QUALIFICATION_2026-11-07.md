# FYERS historical-data qualification matrix

**Date:** 2026-11-07  
**Status:** `DRAFT — NOT RUN; NOT SUITABLE FOR RESEARCH ACCEPTANCE`  
**Related issues:** ORC-89, ORC-58, ORC-107

This artifact defines the evidence required before FYERS historical candles
can be used by JEV-IMRP. It does not claim that candles reconstruct executable
bid/ask or that any live account entitlement has been verified.

## Qualification matrix

| Check | Required evidence | Current state | Decision |
| --- | --- | --- | --- |
| Account tier and history entitlement | Redacted owner-controlled proof of Standard/Prime plan and access limits | NOT RUN | BLOCKED |
| Exact symbol and segment | `NSE:<SYMBOL>-EQ`, dated master hash, 20/20 mapping | Offline resolver tests only | BLOCKED |
| 1-minute coverage | Per-symbol requested/returned date ranges, session denominator, gaps and hashes | No credentialed FYERS response in workspace | NOT RUN |
| Daily coverage | Same matrix over daily requests and corporate-action policy | No credentialed FYERS response in workspace | NOT RUN |
| Time semantics | Epoch unit, UTC/IST conversion and exchange-session mapping | Adapter tests only | NOT RUN |
| OHLC validity | High/low/open/close bounds, duplicates, corrections and partial bars | Fixture checks only | NOT RUN |
| Zero-volume bars | Explain halted/non-traded intervals without dropping them | No live sample | NOT RUN |
| Corporate actions | Split/dividend adjustment mode and immutable source/version | No provider response | NOT RUN |
| Licensing/retention | Written permission for raw retention, replay and derived use | Unresolved; see licensing policy | BLOCKED |
| Fill suitability | Historical candles versus required point-in-time bid/ask | Candles cannot establish L1 | BLOCKED |

## Required manifest

Each authorized sample must record provider/API version, exact request and
response hashes, symbol, interval, requested and returned bounds, timezone,
session/calendar source, adjustment flags, missing/zero-volume counts, retrieval
time, retention expiry, and redaction status. Raw payloads remain outside the
repository unless written terms permit retention.

## Explicit non-claims

OHLCV candles do not provide historical executable touchline, displayed size,
quote age, or latency. The label/fill path must remain ineligible whenever those
inputs are absent; no bid/ask may be reconstructed from OHLC extrema.
