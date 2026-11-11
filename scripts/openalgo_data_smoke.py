"""Opt-in OpenAlgo -> FYERS data smoke; no order endpoint is called.

The command uses the locally configured OpenAlgo API key and records only
credential-free counts/status. It is intentionally separate from the gated
execution gateway.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta

from jev_imrp.common.config import load_universe
from jev_imrp.ingest.openalgo_client import OpenAlgoClient, OpenAlgoConfig


def _symbols() -> list[str]:
    data = load_universe()
    return [str(row["symbol"]) for row in data["instruments"]]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="run authorized read-only calls")
    parser.add_argument("--start-date", default=(date.today() - timedelta(days=2)).isoformat())
    parser.add_argument("--end-date", default=date.today().isoformat())
    args = parser.parse_args()
    if not args.live:
        print("Not run: pass --live in an authorized OpenAlgo Analyze-mode environment.")
        return 0

    client = OpenAlgoClient(OpenAlgoConfig.from_env())
    results: list[dict[str, object]] = []
    for symbol in _symbols():
        quote = client.quote(symbol=symbol, exchange="NSE")
        depth = client.depth(symbol=symbol, exchange="NSE")
        results.append(
            {
                "symbol": symbol,
                "quote_fields": sorted(str(key) for key in quote),
                "depth_fields": sorted(str(key) for key in depth),
            }
        )
    history = client.history(
        symbol="RELIANCE",
        exchange="NSE",
        interval="1m",
        start_date=args.start_date,
        end_date=args.end_date,
    )
    print(
        json.dumps(
            {
                "gateway": "openalgo",
                "upstream_provider": client.config.upstream_provider,
                "symbols_checked": len(results),
                "history_rows": len(history),
                "results": results,
                "order_endpoints_called": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
