# JEV-IMRP prospective provider-switch amendment

**Amendment ID:** `JEV-IMRP-PROVIDER-SWITCH-2026-11-07-v1`  
**Status:** `DRAFT — NOT APPROVED`  
**Effective date:** Not effective until owner and independent reviewer approval are recorded below.  
**Research boundary:** replay/data qualification only; no orders, no live routing, and no locked-holdout access.

## Purpose

This is an additive, forward-looking amendment for the proposed migration from
the historical Shoonya capture path to a FYERS v3 data-only path. The original
[`PREREGISTRATION.md`](../PREREGISTRATION.md), trial ledger, raw artifacts, and
manifests remain immutable and are not reclassified.

## Unchanged scientific contract

- The research question, candidate identity, outcome definitions, cost units,
  baseline policy, acceptance criteria, and reporting rules are unchanged.
- The frozen 20-symbol research membership is unchanged, including the existing
  `TVSMOTOR` substitution; provider mappings do not change membership.
- The locked holdout remains inaccessible until its existing signed gates pass.
- Historical observations retain their original `provider=shoonya` provenance.

## Prospective changes requiring approval

| Contract field | Historical contract | Proposed FYERS contract | Evidence status |
| --- | --- | --- | --- |
| Provider/transport | Shoonya legacy feed and history | Direct FYERS v3 data-only adapters; OpenAlgo diagnostic only | Offline adapter tests pass; live parity **NOT RUN** |
| Symbol identity | Research symbol plus legacy provider token | Exact `NSE:<SYMBOL>-EQ` plus dated master hash | Offline resolver implemented; 20/20 live master proof **PENDING** |
| L1/depth | Historical touchline semantics | FYERS `sf`/`if`/`dp`, nullable source time and fields | Offline packet tests pass; market-hours coverage **PENDING** |
| Historical bars | Legacy provider candles | FYERS `/data/history` OHLCV only | Offline adapter tests pass; credentialed coverage **PENDING** |
| Tick metadata | Historical effective metadata | NSE effective-dated evidence, FYERS master mapping | Policy tests pass; November 2026 file and 13 reconciliations **PENDING** |
| Quotas/retries | Legacy provider limits | Conservative FYERS Standard limits and bounded retry policy | Offline tests pass; account tier **UNKNOWN** |
| Fees/licensing | Existing dated schedule | FYERS/NSE public-rate draft, effective 2026-11-07 | Public rates checked; contract-note parity, rights and owner review **PENDING** |
| Missingness/clock | Existing preregistered gates | Source time distinct from receipt time; fail closed when absent | Offline health tests pass; live qualification **PENDING** |

## Data-use rule

No FYERS data may be collected for confirmatory analysis, and no historical
result may be relabeled, until this amendment has a prospective decision date,
owner approval, independent review, and the dependent FYERS permission,
security, tick-source, licensing, and data-quality gates are resolved.

## Reproducibility record

- **Original preregistration:** preserved unchanged at `PREREGISTRATION.md`.
- **Original trial ledger:** preserved unchanged at `experiments/trial_ledger.csv`.
- **Original provider:** Shoonya, historical only.
- **Proposed provider:** FYERS v3, data-only, not yet approved.
- **Repository state at draft:** working tree contains uncommitted changes;
  record the exact reviewed commit SHA before approval.
- **Required artifact hashes:** FYERS master, raw response manifests, fixture
  bundle, and this amendment must be recorded at approval time.
- **Required downstream updates:** provider manifest schema, data-health report,
  cost/licensing decision, and trial-ledger amendment entry.

## Approval record

| Role | Name/identity | Decision | Date/time (UTC) | Evidence link |
| --- | --- | --- | --- | --- |
| Owner | **PENDING** | **PENDING** | **PENDING** | **PENDING** |
| Independent reviewer | **PENDING** | **PENDING** | **PENDING** | **PENDING** |

Until both rows are completed, this document is a draft and is not evidence of
preregistration approval, holdout validity, provider qualification, or research
performance.
