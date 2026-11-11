# OpenAlgo → FYERS qualification

This is the active gateway qualification path. OpenAlgo is the gateway for
both market data and execution; the configured upstream is FYERS. The capture
tool is data-only and never calls an order endpoint.

## Evidence procedure

Run from the repository with the OpenAlgo key present in the child process:

```powershell
$key=(Get-ItemProperty HKCU:\Environment -Name OPENALGO_API_KEY).OPENALGO_API_KEY
$env:OPENALGO_API_KEY=$key
$env:OPENALGO_GATEWAY_VERSION=(Get-Item 'C:\Users\ponna\AppData\Local\OpenAlgo Desktop\openalgo-desktop.exe').VersionInfo.ProductVersion
uv run python scripts/openalgo_live_qualification.py --duration 300 --reconnects 2
```

Each run creates an ignored local directory under
`data/qualification/openalgo_fyers/<run-id>/` containing:

- raw normalized gateway frames in Parquet, including the original OpenAlgo
  wrapper, source timestamp, UTC receipt timestamp, and monotonic receive time;
- an append-only `manifest.jsonl` with row counts, provider/gateway versions,
  universe-master hash, file sizes, and SHA-256 hashes;
- `qualification_report.json` with the precommitted threshold results,
  fill-eligibility counts, manifest verification, replay parity, approval-gate
  status, and live-order status;
- `qualification_report.sha256`, the exact hash of the report file.

The gateway application version is supplied as `OPENALGO_GATEWAY_VERSION` and
the protocol is recorded as `openalgo-data-api-v1`. When present, the report
also hashes the gateway executable. FYERS does not expose a separate upstream
software version through the OpenAlgo frames, so the report records that
limitation explicitly rather than inventing one.

The capture sends one batched Quote subscription and one batched Depth
subscription per connection so sequential subscription writes do not create an
avoidable measurement queue.
It refuses to run outside the NSE cash-equity session (`09:15-15:30 IST`),
because an after-hours empty book is not a valid live-depth qualification.

Quote frames with no explicit bid/ask are recorded but are not fill-eligible.
Depth level one is the only permitted source for a missing quote touchline. LTP,
OHLC, midpoint, or any other inferred value is never used.

## Precommitted gates

The versioned limits are in `config/openalgo_fyers_qualification.yaml`:

- source-to-receipt lag ≤ 2,000 ms;
- stale receive gap ≤ 5,000 ms;
- zero crossed books, missing Depth level-one sides, duplicate events,
  out-of-order source timestamps, and missing source timestamps;
- reconnect gap ≤ 5,000 ms.

The capture must pass manifest checks and compare the online `QuoteStateBook`
with replay from manifest-listed Parquet only. A failed qualification report is
evidence of a failed gate, not a reason to relax the threshold.

On 2026-11-11, a 30-second two-connection-free steady-state run recorded 1,416
market events but failed the precommitted lag/staleness/order gates: maximum
source-to-receipt lag was 7,617.582 ms, maximum stale gap was 6,028.881 ms, and
two source-timestamp regressions occurred. This is consistent with an
OpenAlgo/FYERS gateway/provider freshness issue, not a recorder serialization
issue: the same run passed manifest verification and replay parity.

## Approval and execution boundary

Independent review, security/key-containment, licensing/retention, tick-grid,
and preregistration approval are recorded as explicit external gates in each
report. They cannot be self-approved by this capture. Live orders remain
separately gated; the qualification command runs Analyze-only and is not live
order authorization.
