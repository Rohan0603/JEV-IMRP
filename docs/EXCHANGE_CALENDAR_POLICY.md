# Exchange calendar policy — 2026-11-07

The frozen universe uses an explicit, effective-dated exchange calendar. A
session record must identify its source URI and SHA-256, whether the date is
open or closed, and local open/close times for open sessions. Half-days are
represented by their shortened close time; missing dates, malformed records,
and missing source lineage fail closed.

`src/jev_imrp/data/exchange_calendar.py` provides the immutable boundary and
`tests/test_exchange_calendar.py` covers regular sessions, a half-day, a
holiday, missing dates, invalid records, and lineage validation.

The official NSE calendar snapshot and its authorized retention/licensing
evidence are **NOT RUN** here. No holiday or exceptional-session values are
invented by the active configuration, and this artifact does not establish
market-data qualification.
