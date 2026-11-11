# Evaluation-window and power-feasibility checkpoint

**Date:** 2026-11-07  
**Status:** `DRAFT — NOT APPROVED; NO STUDY RESULTS INSPECTED`  
**Related issues:** ORC-58, ORC-57, ORC-113

This checkpoint defines the calculation and evidence needed to approve the
proposed train/validation/holdout windows. It deliberately contains no
fabricated session counts, candidate counts, or power result.

## Proposed windows under review

| Window | Current proposal | Acceptance state |
| --- | --- | --- |
| Holdout | 2027-01-01 through 2027-03-31 | Draft only; must be frozen by signed amendment |
| Embargo | One complete exchange session after the 30-minute horizon | Draft only; rationale/signoff pending |
| Training/validation | Chronological expanding window | Exact dates and eligible sessions pending calendar/data evidence |

## Required calculation inputs

Before any research-data inspection, record the effective source/config hash for:

1. Exchange sessions and holidays, including half-days and exceptional closures.
2. Provider plan/tier and historical interval limits for every requested fold.
3. Per-symbol returned bars/events, missingness, source-clock quality, and
   executable-touchline eligibility.
4. Paired daily metric units, dependence/block definition, alpha, sidedness,
   multiplicity rule, trial budget, and the economically meaningful threshold.
5. Exact purge and embargo transformation from raw timestamps to eligible rows.

## Calculation record

The approved calculation must report independent day blocks, eligible candidate
and trade counts, assumed paired effect distribution, variance/CI method,
minimum detectable effect, power at the selected threshold, and sensitivity to
missing days and failed fills. If the requested windows cannot support the
predeclared effect and error rates, the result is `INCONCLUSIVE/NOT FEASIBLE`,
not a reason to inspect outcomes and revise the window.

| Input/output | Value | Source/hash | State |
| --- | --- | --- | --- |
| Account tier | UNKNOWN | Owner-controlled evidence required | NOT RUN |
| Eligible exchange sessions | UNKNOWN | Dated calendar required | NOT RUN |
| Valid source-clock/L1 sessions | UNKNOWN | Market-hours qualification required | NOT RUN |
| Paired daily P&L unit | UNSPECIFIED | Signed preregistration amendment required | BLOCKED |
| `delta_min` unit | 0.0001 is 1 bp if fractional; comment conflict recorded | Reconciliation artifact | OPEN |
| Power/MDE result | NOT CALCULATED | Inputs above must be frozen first | NOT RUN |

## Holdout protection

No holdout file, result, or outcome distribution may be read while this record
is unresolved. Any change after observation must close the holdout and classify
the affected analysis as exploratory.

| Owner | Independent reviewer | Decision | Effective version |
| --- | --- | --- | --- |
| PENDING | PENDING | PENDING | PENDING |
