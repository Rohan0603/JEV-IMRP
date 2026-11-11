# Preregistration reconciliation checkpoint

**Date:** 2026-11-07  
**Status:** `DRAFT — NOT APPROVED; NO STUDY DATA INSPECTED`  
**Related issues:** ORC-57, ORC-58, ORC-113

This checkpoint compares the original research contract with the current
configuration without changing the original hypothesis or opening any holdout.
It is a prospective reconciliation record, not an effective amendment.

## Confirmed discrepancy

`config/experiment.yaml` currently contains `delta_min: 0.0001`. If the value is
a fractional return, it is **1 basis point**, not the inline comment's 10 bps.
The primary metric is also named `paired_daily_portfolio_net_pnl_difference`
without yet declaring whether the stored difference is INR or a normalized
return. No result was inspected to reach this finding.

## Reconciliation table

| Contract item | Original/current evidence | Required prospective decision | State |
| --- | --- | --- | --- |
| `delta_min` unit | `0.0001` fractional value conflicts with “10 bps” comment | Owner/reviewer must choose and justify a unit before data inspection | OPEN |
| Primary metric unit | Metric name says portfolio net P&L difference; currency/normalization is unspecified | Declare INR or fractional return, calculation and aggregation | OPEN |
| Candidate generator | Original registration says TBD; config names `synthetic_v1` | Record rationale, effective date, and whether confirmatory or exploratory | OPEN |
| Entry latency / quote age | Original registration leaves both `TBD` | Freeze numerical limits, units, source clock, and fail-closed behavior | OPEN |
| Position sizing | Notional and cap values remain `TBD` | Freeze INR notional, one-position rule, and capital/exposure limits | OPEN |
| Session/calendar | Original registration leaves holiday source `TBD`; implementation exists | Attach dated exchange source and reviewer decision | OPEN |
| Cost stress scenarios | Stress spread, latency, slippage, and unfilled-exit values remain `TBD` | Freeze formulas and apply identically to every comparator | OPEN |
| Statistical thresholds | Alpha, power, sidedness, multiplicity, and `delta_min` provenance are not signed | Freeze units, test, rejection rule, and correction method | OPEN |
| Split/embargo/holdout | Config has draft boundaries, but effective amendment and chronology are absent | Record source SHA, session-count rationale, and approval before opening | OPEN |
| Feature parameters / missingness | Original registration carries unresolved feature and outlier `TBD`s | Freeze registry, missingness, outlier, and adjustment rules | OPEN |
| Trial and stopping rules | Config has `max_trials: 1000`; signed provenance is absent | Freeze maximum, failure logging, and stop rule without outcome inspection | OPEN |
| Fees and licensing | FYERS schedule is a public-rate draft; IPFT/GST conflict and rights remain | Resolve sources, freeze base/stress formulas, and obtain written rights | OPEN |
| Provider switch | Draft amendment exists and preserves historical Shoonya provenance | Owner and independent reviewer approval required | OPEN |

## Required sign-off

No configuration value in this checkpoint becomes an approved confirmatory
contract until the owner and an independent reviewer record a dated decision,
the source/config hashes are captured, and the amendment is appended to
`PREREGISTRATION.md` with a version bump. Until then, project data inspection,
locked-holdout access, and confirmatory inference remain prohibited.

| Role | Identity | Decision | Date/time UTC | Evidence |
| --- | --- | --- | --- | --- |
| Owner | PENDING | PENDING | PENDING | PENDING |
| Independent reviewer | PENDING | PENDING | PENDING | PENDING |
