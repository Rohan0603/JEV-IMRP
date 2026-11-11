# JEV-IMRP — current GitHub Chief/Worker governance (2026-11-09)

**Task authority migration:** GitHub Issues and the verified [JEV-IMRP Chief/Worker Governance Project #1](https://github.com/users/Rohan0603/projects/1) are the intended development queue, with roles `chief`, `worker`, and Chief-managed `human`. Existing labels or comments do NOT prove Project Status/Priority; an authorized GitHub Projects read-back verified Project membership and board identity on 2026-11-10, while unset issue field values remain **NOT VERIFIED**. Chief sets first-handoff `GITHUB METADATA SETUP`; owner-invoked `worker + metadata-bootstrap` may perform **only approved metadata operations**, followed by return to Chief. Product implementation requires separate Project `Ready` read-back and full DoR.

**Agents are never launched by GitHub changes.** The owner alone manually invokes/schedules Chief and Worker tools. No automated dispatcher, trading or live order placement is authorized by this PR.

**JEV-IMRP safety:** Preserve existing source-code/finance/security/testing and authorship constraints below. **Do not reactivate Shoonya:** the historical Shoonya broker-contract section below describes an older integration; owner policy excludes Shoonya from new implementations. Do not silently substitute a broker. Retain data-only/Analyze-mode testing and never place live orders without separate authorization.

**Canonical portable contract:** `docs/GITHUB_AGENT_GOVERNANCE.md`. This current GitHub routing section supersedes legacy task-tool instructions only; existing product-specific safety rules remain binding. Existing source files and CI unchanged.

---

## Preserved JEV-IMRP-specific instructions

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
