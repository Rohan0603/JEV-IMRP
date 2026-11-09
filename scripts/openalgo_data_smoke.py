"""Optional, data-only OpenAlgo smoke; never calls an order endpoint.

The command is intentionally opt-in because a real run requires the operator's
local OpenAlgo credentials and a market-data decision. Offline contract tests
cover the same request construction without network access.
"""

from __future__ import annotations

import argparse
import json

from jev_imrp.ingest.openalgo_client import OpenAlgoClient, OpenAlgoConfig


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="make the authorized read-only calls")
    parser.add_argument("--symbol", default="RELIANCE")
    parser.add_argument("--exchange", default="NSE")
    parser.add_argument("--start-date", default="2026-01-01")
    parser.add_argument("--end-date", default="2026-01-02")
    args = parser.parse_args()

    if not args.live:
        print("Not run: pass --live only in an authorized data-only environment.")
        return 0

    client = OpenAlgoClient(OpenAlgoConfig.from_env())
    quote = client.quote(symbol=args.symbol, exchange=args.exchange)
    history = client.history(
        symbol=args.symbol,
        exchange=args.exchange,
        interval="1m",
        start_date=args.start_date,
        end_date=args.end_date,
    )
    print(json.dumps({"quote": quote, "history_rows": len(history)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
