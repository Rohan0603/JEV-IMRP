# JEV-IMRP provider-boundary amendment — OpenAlgo-backed FYERS

**Amendment ID:** `JEV-IMRP-PROVIDER-SWITCH-2026-11-11-v2`  
**Status:** `DRAFT — OWNER/INDEPENDENT REVIEW REQUIRED`  
**Research boundary:** OpenAlgo is the gateway for both data and execution.
Analyze/Sandbox is the default; live order execution remains separately gated.

## Decision

The active runtime route is OpenAlgo, with its upstream broker explicitly
pinned to FYERS. OpenAlgo is a transport/gateway boundary, not permission to
silently substitute another broker. Direct FYERS modules remain available for
protocol and historical qualification, but direct FYERS credentials are not
required by the active runtime.

Historical Shoonya and direct-FYERS artifacts retain their original provider
labels. They are not relabeled as OpenAlgo/FYERS results.

## Active contract

| Contract field | Active decision | Acceptance evidence |
| --- | --- | --- |
| Provider/transport | OpenAlgo data API/WebSocket, upstream pinned to FYERS | Gateway version, provider pin, route allowlist |
| Symbol identity | OpenAlgo symbol mapping with frozen research names | 20/20 mapping and provider-symbol manifest |
| L1/depth | OpenAlgo quote/depth stream; missing fields fail closed | Fresh bid/ask, depth levels, source/receive clocks |
| Historical bars | OpenAlgo history response attributed to FYERS | 1m/daily coverage, gaps, hashes, timezone and adjustment review |
| Replay lineage | `provider=openalgo:fyers`, gateway/schema/master hashes | Raw Parquet manifest and replay parity |
| Security | OpenAlgo API key in local secret storage only | Redaction and key-containment evidence |
| Execution | OpenAlgo order/cancel routes, FYERS upstream pinned | Analyze payload tests; live authorization and audit trail |
| Safety | Analyze/Sandbox default; live orders require explicit opt-in | Mode proof, route allowlist and negative tests |

## Explicit limitations

OpenAlgo results do not by themselves prove the direct FYERS wire contract.
They qualify the OpenAlgo-backed FYERS route. If direct FYERS packet semantics,
OAuth permissions, or source timestamps are later required, that is a separate
direct-provider qualification gate.

## Approval record

| Role | Decision | Date/time | Evidence |
| --- | --- | --- | --- |
| Owner | PENDING | PENDING | PENDING |
| Independent reviewer | PENDING | PENDING | PENDING |

This amendment does not itself authorize a live order. Live execution requires
the explicit `OPENALGO_ALLOW_LIVE_ORDERS=true` control, an Analyze-mode-off
preflight, owner authorization for the specific run, and the applicable
licensing, security, review, and data-use gates.
