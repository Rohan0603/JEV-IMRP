# Data-health gate report

**Date:** 2026-11-07  
**Status:** `OFFLINE FIXTURE EVIDENCE ONLY — LIVE GATE NOT PASSED`  
**Related issue:** ORC-67

## Offline evidence

The deterministic scoreboard evaluates raw events without repairing them. It
records sessions, connections, subscription generations, sequence gaps,
out-of-session events, symbol/day counts, source-timestamp completeness,
duplicates, out-of-order events, crossed quotes, missing touchline, invalid
sizes, required-symbol coverage, expected interval coverage, and missing
intervals. Threshold violations produce explicit blocked reasons.

| Gate dimension | Offline state | Live acceptance |
| --- | --- | --- |
| Source timestamp required | Tested/fail closed | Market-hours capture required |
| Session/holiday bounds | Tested with explicit timezone/bounds | Dated NSE calendar required |
| Duplicate/order/sequence checks | Tested | Real feed sample required |
| L1 touchline validity | Tested | Source-clock/L1 parity required |
| Expected interval coverage | Tested | Per-symbol/day denominators required |
| Reconnect generations | Tested | Daily recovery evidence required |
| 20-symbol coverage | Fixture/config only | Authorized FYERS master/feed required |

## Required live artifact

For every symbol/day, preserve a redacted manifest containing provider,
transport, adapter/schema/master versions, raw payload hash, source and receive
timestamps, session/calendar source, expected/received intervals, blocked
reasons, reconnect gaps, and reviewer decision. Missing source time or
touchline makes fill eligibility unknown; receipt time must not substitute for
it.

No live artifact is present in the repository. The data gate remains blocked
until licensing, security, credentialed access, effective tick source, and
market-hours qualification are independently accepted.
