# No-order shadow-study protocol

**Date:** 2026-11-07  
**Status:** `DRAFT — NOT APPROVED; NOT ACTIVE`  
**Related issues:** ORC-95, ORC-98, ORC-105, ORC-113

This is a prospective design artifact only. It authorizes no live collection,
paper simulation, order routing, holdout access, or outcome inspection.

## Activation prerequisites

All rows must be accepted and linked before the first observation:

| Gate | Required evidence | State |
| --- | --- | --- |
| Research contract | Signed dated amendment, version/hash alignment | NOT READY |
| Provider amendment | Owner and independent reviewer approval | NOT READY |
| Licensing | Written FYERS/NSE permission for exact use and retention | NOT READY |
| Security | ORC-116 key containment/rotation evidence | NOT READY |
| Data quality | Market-hours source-clock, L1/depth and reconnect qualification | NOT READY |
| Tick source | Effective November 2026 NSE file and 13 reconciliations | NOT READY |
| Cost/labels | Contract-note fee resolution and independent label review | NOT READY |

## Prespecified design fields

- Window, session count, candidate rule, model versions, universe and cost
  schedule must be copied from the effective signed amendment.
- Every decision records decision time, provider/source clock, config hash,
  candidate status, missingness reason, and deterministic manifest reference.
- No order endpoint, position endpoint, funds endpoint, or live-routing path is
  permitted. The process must fail closed if any prohibited capability is
  reachable or if source time/required touchline is unavailable.
- Report the complete planned period, including outages, missing candidates,
  failed labels, and maintenance windows; never backfill or selectively omit
  unfavorable days.
- Stop conditions include contract drift, missing manifest, unbounded clock
  age, unresolved fee/licensing change, security incident, or unexplained data
  loss. A stop is recorded as NOT RUN/INCOMPLETE, not repaired retrospectively.

## Approval record

| Role | Identity | Decision | Date/time UTC | Evidence |
| --- | --- | --- | --- | --- |
| Owner | PENDING | PENDING | PENDING | PENDING |
| Independent reviewer | PENDING | PENDING | PENDING | PENDING |

Until both approvals and every prerequisite are present, the only permitted
validation is offline fixtures and synthetic data.
