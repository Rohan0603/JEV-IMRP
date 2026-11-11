# FYERS quota, health, and safe-failure policy

**Date:** 2026-11-07  
**Status:** `DRAFT — OFFLINE QUALIFIED; ACCOUNT TIER NOT VERIFIED`  
**Related issue:** ORC-111

## Quota contract

The adapter must use an explicitly declared account-tier limit set. The default
offline profile is conservative and credential-free:

| Limit | Default profile |
| --- | ---: |
| Requests/second | 5 |
| Requests/minute | 50 |
| Requests/day | 5,000 |
| Quote symbols/request | 50 |
| WebSocket symbols/session | 100 |

These defaults are not a claim about the owner's live FYERS plan. The live tier
and effective provider limits must be recorded before any credentialed run.

## Required behavior

- Reserve requests atomically; a rejected reservation leaves usage unchanged.
- Enforce second, minute, and provider-local-day windows with timezone-aware
  timestamps. Never use receipt time as a provider event time.
- Reject oversized quote batches and WebSocket subscriptions before sending.
- Persist only credential-free quota snapshots and reports; never log keys,
  tokens, authorization headers, or raw secret-bearing responses.
- On quota exhaustion, return a typed failure and stop/retry only under a
  bounded, predeclared policy. Do not silently drop data or widen limits.
- On malformed snapshots, timezone errors, provider errors, schema changes,
  or uncertain usage, fail closed and preserve the incident metadata.

## Health evidence matrix

| Signal | Required output | Current state |
| --- | --- | --- |
| Quota | limits, used, remaining, UTC timestamp | Offline tests pass |
| Auth | redacted expiry/error class | Credentialed run NOT RUN |
| Feed | source clock, receive lag, reconnect generation | Fixture tests pass; live NOT RUN |
| Storage | write/recovery/manifest state | Offline writer tests pass |
| Schema | provider/schema/master hashes and unknown fields | Offline contract tests pass |

The live account tier, quota headers, market-hours behavior, and provider
license remain external evidence requirements. This document does not authorize
credentialed collection or order-related calls.
