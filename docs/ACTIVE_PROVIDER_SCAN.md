# Active provider scan — 2026-11-11

This report records the OpenAlgo-backed FYERS active-provider boundary. It is a
static evidence artifact; it does not establish live-provider parity or
credentialed acceptance.

## Active runtime result

The active ingestion package exposes the OpenAlgo read-only gateway pinned to
FYERS. Direct FYERS v3 modules remain for protocol/history qualification and
immutable provenance, but are not the active runtime route. No retired broker
module or silent provider fallback is part of the active package boundary.
The regression guard is `tests/test_provider_boundary.py`.

New captures use the provider-neutral `feed_type=market_data` partition.
`ManifestEntry.provider` records `openalgo:fyers` and the gateway API version. The
active frozen universe contains research symbols and no broker-specific
instrument tokens.

## Removal result

No retired-broker runtime, onboarding, credential, or executable test path
remains in the active project. Immutable historical manifests and research
records retain their original provider labels and are not relabeled or
rewritten.

## Verification

The active checks are:

```text
uv run ruff check src tests
uv run mypy src
uv run pytest -q
git diff --check
```

Live OpenAlgo/FYERS market-hours evidence, upstream-provider attribution,
effective-dated exchange-file reconciliation, key rotation, and independent PR
review remain separate acceptance gates.
