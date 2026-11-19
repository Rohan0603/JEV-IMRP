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

The later captured report at
`data/qualification/openalgo_fyers/20261111T101400Z-03934405/` improved the
maximum source-to-receipt lag to 16.494 ms, but still failed the unchanged
precommitted gates: maximum stale gap was 10,460.555 ms and 40 depth frames
lacked level-one sides. Manifest verification and replay parity passed. This
remains a provider/gateway freshness and depth-quality failure, not evidence to
relax thresholds or authorize live use.

On 2026-11-12 during NSE cash-market hours, the newly generated OpenAlgo API
key authenticated successfully. The standard 300-second capture with the
library's default keepalive failed after roughly 90 seconds with `1011
keepalive ping timeout`; no order endpoint was called. A 30-second standard
capture completed at
`data/qualification/openalgo_fyers/20261112T041442Z-0fefa469/` and recorded
1,205 market events, maximum source-to-receipt lag of 1,373.315 ms, maximum
stale gap of 6,084.501 ms, and no missing depth levels, duplicates,
out-of-order events, or crossed books. It failed the unchanged stale-gap gate;
manifest verification and replay parity passed.

As a diagnostic only, a second 300-second capture disabled the client
keepalive without changing the gateway or order settings:
`data/qualification/openalgo_fyers/20261112T041811Z-065fb7a9/`.
It completed 4,636 market events but failed source-to-receipt lag
(75,949.137 ms), stale gap (16,538.547 ms), and out-of-order events (108).
Manifest verification and replay parity passed, and the report records
`analyze_mode=true`, `authorized=false`, `placed=false`. Disabling the client
heartbeat therefore exposes, rather than resolves, a sustained gateway/data
quality failure; it is not a reason to relax thresholds or qualify the feed.

## Forward-use decision — 2026-11-12

**Direct FYERS v3 is the sole forward provider path.** OpenAlgo is retired from
active development and remains only as historical qualification evidence. No
new OpenAlgo integration, comparator capture, fill-label generation, or order
route should be built from this point.

The direct FYERS path must preserve raw provider payloads, source/exchange clock
provenance, local receipt timestamps, symbol-master hashes, reconnect state, and
fail-closed missingness. It must pass the same unchanged market-hours freshness,
ordering, depth, manifest, replay, licensing, security, and independent-review
gates before it can support canonical research labels or any execution claim.

The failed OpenAlgo captures remain immutable historical evidence and must not
be reclassified as FYERS-direct evidence. Live orders remain unauthorized.

## Approval and execution boundary

Independent review, security/key-containment, licensing/retention, tick-grid,
and preregistration approval are recorded as explicit external gates in each
report. They cannot be self-approved by this capture. Live orders remain
separately gated; the qualification command runs Analyze-only and is not live
order authorization.
