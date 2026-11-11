# OpenAlgo strategy integration analysis

## Decision

Use OpenAlgo as the FYERS-backed gateway for market data and, only after a
separate live-order approval, as the downstream execution gateway. Keep JEV as
the authority for raw capture, source/receipt clocks, manifest hashes, replay,
quote-state validation, causal features, candidate signals, fill simulation, and
qualification.

The new `OpenAlgoMarketDataBridge` is the first integration seam. It records
the original gateway frame through `OpenAlgoRecorder`, applies the same
`QuoteStateBook` used by replay, and publishes the resulting touchline into the
existing `FeatureEngine`. Invalid or stale observations remain visible to JEV
and fail closed; they are not repaired with LTP, OHLC, or inferred prices.

## Options considered

| Option | Fit for JEV | Decision |
| --- | --- | --- |
| JEV strategy + OpenAlgo gateway | Preserves raw lineage, point-in-time causality, replay parity, and existing candidate/policy contracts. | **Recommended** |
| OpenAlgo Strategy module | Useful for broker-side multi-leg lifecycle and signal/webhook orchestration, but its API is primarily strategy lifecycle/status and execution. It does not replace JEV's raw-event manifest, replay, feature lineage, or qualification evidence. | Later execution adapter only |
| OpenAlgo Python strategy host | Can calculate indicators and call order APIs, with Analyzer/Live modes. It would duplicate JEV's feature and audit path unless constrained to a shadow consumer. | Shadow/prototype only |

## Boundary contract

1. OpenAlgo WebSocket Quote and Depth frames are transport input. Depth is the
   fill-eligibility source when Quote lacks bid/ask.
2. `OpenAlgoRecorder` writes every frame, including ACKs and reconnect frames,
   to the append-only raw-event manifest.
3. `QuoteStateBook` is the only online state reconstruction path; replay calls
   the same state transition code.
4. `OpenAlgoMarketDataBridge` adapts valid and invalid state rows to the
   existing JEV `FeatureEngine`. The feature engine uses local availability,
   never merely source time, for point-in-time decisions.
5. Candidate generation and policy remain JEV-owned. A future execution bridge
   may accept an already-approved candidate, but it must not create a signal or
   bypass the qualification gates.

## Why not put JEV's strategy in OpenAlgo Strategy?

OpenAlgo's Strategy module is appropriate when the strategy definition and
execution lifecycle are owned by OpenAlgo: batch strategies can orchestrate
multi-leg option orders, and signal strategies can receive entry/exit webhooks.
Its live mode is an execution capability, not a research/replay contract.

For JEV, moving the strategy there would make it harder to prove:

- which raw frame was available at decision time;
- whether replay and online behavior are identical;
- which manifest/hash and provider versions produced a candidate;
- that stale, crossed, missing-depth, or reconnect-invalid data was rejected;
- that the same candidate stream was consumed by every model and policy.

Therefore OpenAlgo Strategy can be evaluated later as an execution-side
orchestrator for an already-qualified JEV signal, especially for multi-leg
options. It must remain downstream of JEV's candidate and approval gates.

## Next stage

- Run the updated OpenAlgo 1.0.7 capture during NSE cash-market hours and
  require source lag, freshness, depth, reconnect, manifest, replay, and
  independent-review gates to pass.
- Add a JEV session runner that consumes the bridge and emits feature snapshots
  and candidate records in Analyze mode.
- Add a dry-run execution contract that records the exact OpenAlgo order payload
  that would be sent, without sending it.
- Only after independent security, licensing, tick-grid, preregistration, and
  owner approval, evaluate a separately configured OpenAlgo live execution
  adapter. No live order is authorized by this integration.
