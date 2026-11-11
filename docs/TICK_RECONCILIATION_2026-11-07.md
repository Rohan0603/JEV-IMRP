# NSE tick reconciliation checkpoint

Status: `UNRESOLVED — NOT APPROVED`

The governing public rule is [NSE/CMTR/67133](https://nsearchives.nseindia.com/content/circulars/CMTR67133.pdf), effective 2025-04-15, as carried into the [NSE/CMTR/73927 consolidated circular](https://nsearchives.nseindia.com/content/circulars/CMTR73927.zip). It requires monthly review using the previous trading month's last available close, while the effective-dated exchange security file remains the source for individual applicability and exceptions. The November 2026 individual security-file download and its SHA-256 were not available in this workspace on 2026-11-07; the table therefore remains `NOT RUN` rather than inferring values from the generic price bands or a broker master.

This artifact records the 13 known discrepancies without inventing November
2026 security-file values. The frozen research membership is unchanged. The
Shoonya-era configured tick is retained as historical metadata only; it is not
treated as the current exchange source of truth.

| Research symbol | FYERS symbol | Series | Stored tick | November 2026 NSE file tick | Decision |
| --- | --- | --- | ---: | --- | --- |
| RELIANCE | NSE:RELIANCE-EQ | EQ | 0.05 | NOT RUN | BLOCKED |
| TCS | NSE:TCS-EQ | EQ | 0.05 | NOT RUN | BLOCKED |
| ICICIBANK | NSE:ICICIBANK-EQ | EQ | 0.05 | NOT RUN | BLOCKED |
| INFY | NSE:INFY-EQ | EQ | 0.05 | NOT RUN | BLOCKED |
| BHARTIARTL | NSE:BHARTIARTL-EQ | EQ | 0.05 | NOT RUN | BLOCKED |
| LT | NSE:LT-EQ | EQ | 0.05 | NOT RUN | BLOCKED |
| AXISBANK | NSE:AXISBANK-EQ | EQ | 0.05 | NOT RUN | BLOCKED |
| HINDUNILVR | NSE:HINDUNILVR-EQ | EQ | 0.05 | NOT RUN | BLOCKED |
| MARUTI | NSE:MARUTI-EQ | EQ | 0.05 | NOT RUN | BLOCKED |
| SUNPHARMA | NSE:SUNPHARMA-EQ | EQ | 0.05 | NOT RUN | BLOCKED |
| ADANIENT | NSE:ADANIENT-EQ | EQ | 0.05 | NOT RUN | BLOCKED |
| ASIANPAINT | NSE:ASIANPAINT-EQ | EQ | 0.05 | NOT RUN | BLOCKED |
| ONGC | NSE:ONGC-EQ | EQ | 0.05 | NOT RUN | BLOCKED |

Required before resolution:

1. Capture the official NSE effective-dated security file for November 2026,
   record its retrieval date and SHA-256, and preserve the raw file.
2. Record prior-month reference close/date and exception applicability for each
   symbol; the generic price-band rule is not enough for individual acceptance.
3. Compare the official file with the provider master and stored metadata,
   then obtain owner and independent review before any config or research-data
   change.

The repository has a fail-closed resolver and boundary tests for effective-dated
tick evidence, but those tests are not evidence that any of the 13 live
reconciliations passed.

No historical provider labels or frozen-universe membership may be rewritten
   by this checkpoint.
