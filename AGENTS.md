# Project rules

## Attribution

**Never add a "Co-Authored-By: Codex" line to commits or pull requests.**
All work in this repository is attributed to the human only. Codex is a
tool, not a co-author, and its name must not appear in commit messages, PR
descriptions, or any versioned artifact.

## Shoonya broker contract

Always study `https://shoonya.com/api-documentation` before implementing,
changing, or debugging anything broker-related. The documented contract is the
source of truth; the SDK and any local scripts must conform to it.

Key facts (verified 2026-10-26):

- **OAuth is the only supported login flow.** The old TOTP auto-login
  (`login(uid, password, twoFA, vendor_code, ...)`) is rejected by the live API
  with `Invalid Vendor code`.
- **Endpoint path matters.** The SDK ships with `NorenWClientTP/` but the live
  API only accepts `NorenWClientAPI/`. The old path returns 502 Bad Gateway;
  the documented path returns `400 Invalid Vendor code` for bad credentials and
  `200 Ok` for good ones.
- **`searchscrip` is fuzzy.** It matches partial trading symbols, so `"SBIN"`
  returns `SBINMID150-EQ` before `SBIN-EQ`. Require an exact `SYMBOL-EQ` match.
- **Symbol masters** are downloadable per exchange at
  `https://api.shoonya.com/<EX>_symbols.txt.zip` and bypass login entirely.