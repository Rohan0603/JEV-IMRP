# FYERS/NSE data-use, retention, and sensitive-data policy

**Date:** 2026-11-07  
**Status:** `DRAFT — OWNER/LEGAL APPROVAL REQUIRED`  
**Scope:** research-only, no orders, no redistribution

This policy records the current evidence boundary for FYERS and NSE market data.
API access is not treated as a grant to archive, redistribute, publish, or
commercially replay exchange data. Unknown permissions fail closed.

## Source register

| Source | What it establishes | What it does not establish |
| --- | --- | --- |
| [FYERS API documentation](https://fyers.in/api-docs) | API operation and access documentation | A licence to redistribute or retain exchange data indefinitely |
| [FYERS API terms](https://fyers.in/terms-and-conditions-api) | Contract terms and restrictions requiring review | Permission for this project's exact capture, replay, derived-data, or shadow-use plan |
| [FYERS data-feed support article](https://support.fyers.in/portal/en/kb/articles/do-i-need-to-pay-for-datafeeds) | Whether a user must pay for the data feed | Redistribution, archival, or third-party forwarding rights |
| [NSE consolidated circulars](https://www.nseindia.com/static/resources/consolidated-master-circulars) | Exchange rule source and effective-date discovery | A project-specific licence or owner approval |

## Capability matrix

| Proposed use | Current decision | Required evidence before enabling |
| --- | --- | --- |
| Local read-only qualification with synthetic/fixture data | PERMITTED | No broker credential or live payload in tracked artifacts |
| Private capture of live FYERS/NSE raw payloads | UNCLEAR — BLOCKED | Written FYERS/NSE permission, retention period, access controls |
| Private replay/backtest of captured raw payloads | UNCLEAR — BLOCKED | Written permission covering replay and retention |
| Derived indicators and aggregate research metrics | UNCLEAR — BLOCKED for live licensed data | Written terms covering derivatives and publication boundary |
| Internal dashboard displaying live or historical quotes | UNCLEAR — BLOCKED | Terms confirming internal display and retention |
| Redistribution, publication, or sharing of raw/near-raw data | PROHIBITED unless separately licensed | Explicit written exchange/provider licence |
| Historical Shoonya artifacts already held | PRESERVE ORIGINAL PROVENANCE | Do not fetch new Shoonya data; apply existing retention/access controls |

## Required controls

- Keep FYERS app secrets, access tokens, and contract notes in
  protected local secret storage only; never place them in Linear, Git, logs,
  screenshots, fixtures, or test output.
- Store only redacted request metadata and content hashes in tracked evidence.
- Restrict any live-data directory to the owner account, encrypt it at rest,
  define a deletion date before capture, and record deletion evidence.
- Keep raw payloads separate from derived metrics; do not infer permission from
  the fact that an API endpoint is technically reachable.
- Keep the direct FYERS adapter disabled for confirmatory research until this
  policy and the provider amendment are approved.

## Approval and unresolved evidence

| Required decision | State | Owner/evidence |
| --- | --- | --- |
| FYERS written clarification for private research capture/replay | PENDING | Owner/legal response required |
| NSE/exchange terms applicable to the exact data plan | PENDING | Effective-dated terms required |
| Retention period and deletion record | PENDING | Owner decision required before capture |
| Security/key-exposure gate ORC-116 | PENDING | Authorized key owner only |

Until every applicable row is resolved, the FYERS/NSE data-use gate remains
blocked. This document is evidence of the boundary and open decisions, not
legal advice or an approval to collect live data.
