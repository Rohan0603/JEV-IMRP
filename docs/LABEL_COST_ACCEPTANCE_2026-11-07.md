# Label and cost acceptance matrix

**Date:** 2026-11-07  
**Status:** `DRAFT — BLOCKED FOR RESEARCH ACCEPTANCE`  
**Related issues:** ORC-71, ORC-72, ORC-73

Offline implementation and hand-worked fixtures are necessary but not
sufficient for the label/cost gate. This matrix records the distinction.

| Check | Offline evidence | Current decision |
| --- | --- | --- |
| Entry/exit side | Long/short fixture and causal label tests | PASS offline; live touchline NOT RUN |
| Fixed horizon | Timestamp and horizon tests | PASS offline; source-time qualification pending |
| Quote staleness/latency | Fail-closed policy and synthetic cases | PASS offline; numerical amendment pending |
| Missing/unfillable exit | Explicit missing-path tests | PASS offline; no live acceptance |
| Tick/rounding | Effective-dated resolver and boundary tests | PASS policy; November 2026 13-symbol evidence pending |
| Brokerage | FYERS draft cap/rate fixture | PUBLIC RATE CHECKED; contract-note parity pending |
| STT/exchange/SEBI/stamp | Versioned schedule fixture | PUBLIC RATE CHECKED; effective-date evidence pending |
| IPFT | Both official FYERS values retained | UNRESOLVED SOURCE CONFLICT |
| GST base | Both official FYERS treatments retained | UNRESOLVED SOURCE CONFLICT |
| Lineage | Provider/source/config fields and hashes | PASS offline; authorized sample hashes pending |
| Independent review | Required by acceptance | NOT PRESENT |

## Gate rule

No cost-sensitive label, baseline, or holdout result may be accepted while any
row is unresolved. A fixture pass may be reported as infrastructure evidence,
not as market-data or contract-note qualification. No bid/ask may be inferred
from OHLC candles, and no historical Shoonya observation may be relabeled as
FYERS.

## Required evidence bundle

The final bundle must contain the exact reviewed source/config SHA, redacted
contract-note or written FYERS clarification, effective NSE tick file and
hash, sample label cases with source timestamps, deterministic output hashes,
full test/CI commands and exit codes, and an independent reviewer decision.

| Owner | Independent reviewer | Decision | Date/time UTC |
| --- | --- | --- | --- |
| PENDING | PENDING | PENDING | PENDING |
