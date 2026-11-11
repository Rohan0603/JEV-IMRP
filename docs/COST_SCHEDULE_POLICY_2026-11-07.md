# Cost schedule policy — 2026-11-07

The active schedule is `config/cost_schedule.yaml` version
`v3-fyers-draft`, effective 2026-11-07. It uses the public FYERS charges list
and the NSE statutory-fee page as source references. The model remains
per-executed-leg and preserves exact `Decimal` arithmetic.

The current public-rate draft records FYERS equity brokerage as the lower of
0.03% and ₹20 per executed order, NSE transaction charges at 0.0030699%, STT
for intraday sells, SEBI turnover fees, stamp duty on buys, and public FYERS
IPFT/GST treatment. Hand-calculated fixtures cover the draft rules.

There is an unresolved official-source conflict: the FYERS charges page lists
IPFT at ₹0.01/crore and includes IPFT/SEBI in GST, while the FYERS statutory
support article lists IPFT at ₹10/crore and says GST excludes SEBI/IPFT. The
schedule records both sources and blocks cost-sensitive labels until a redacted
contract note or written FYERS clarification resolves the conflict.

This is not contract-note acceptance. Account-specific brokerage terms,
effective-date confirmation, licensing/retention rights, and owner/independent
review remain pending. No live order or credential was used to produce this
draft, and historical Shoonya results must not be relabeled as FYERS results.

Sources checked 2026-11-07:

- <https://fyers.in/charges-list>
- <https://www.nseindia.com/static/invest/first-time-investor-sebi-turnover-fees-stt-other-levies>
