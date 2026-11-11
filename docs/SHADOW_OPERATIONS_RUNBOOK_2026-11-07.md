# Read-only shadow operations runbook

**Date:** 2026-11-07  
**Status:** `DRAFT — INACTIVE UNTIL LICENSING AND DATA GATES PASS`  
**Related issues:** ORC-97, ORC-98, ORC-105

This runbook is for a future authorized no-order shadow period. It contains no
credentials, live endpoints, or automatic order/re-authentication behavior.

## Daily checks

| Area | Record | Fail-closed response |
| --- | --- | --- |
| Process | startup/version/config and manifest hash | stop session if version/hash differs |
| Feed | heartbeat, source timestamp quality, receive lag, stale count, reconnects | mark observations ineligible and stop after threshold |
| Session | exchange calendar, 09:15/15:30 IST boundaries, holidays | do not synthesize a session |
| Storage | free space, write latency, encrypted backup and recovery marker | stop capture before loss; preserve incident |
| API | quota remaining, HTTP/error class, token expiry | stop and request owner action; never log secrets |
| Schema | provider/schema/master hash and unknown fields | quarantine incompatible payloads |
| Decisions | candidate counts, accepted/rejected/unfillable reasons | report all rows, including failures |

## Fault-injection cases before activation

Weekend/holiday, boundary timestamps, stale source clock, clock regression,
duplicate/out-of-order packet, reconnect gap, malformed payload, disk-full
simulation, partial write, expired token, quota exhaustion, provider outage,
schema drift, and manifest recovery must each have a sanitized result and
reviewer. No live fault injection is permitted before the licensing and security
gates are accepted.

## Incident record

For every incident record UTC time, session date, component/version, redacted
error class, affected symbols, last valid manifest hash, rows quarantined,
recovery action, and whether the planned period is complete. Do not paste
tokens, keys, raw credentials, or unapproved market payloads into the report.

## Activation decision

`NOT ACTIVE`. This runbook does not authorize collecting FYERS/NSE data or
opening the locked holdout. Activation requires the signed shadow protocol,
written data-use permission, ORC-116 security evidence, and all upstream Linear
gates.
