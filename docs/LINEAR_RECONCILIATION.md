# GitHub–Linear reconciliation policy

This repository uses Git and its versioned research contract as the source of
truth for code, configuration, preregistration and reproducibility artifacts.
Linear is the execution tracker: it records ownership, dependencies, status,
milestones, decisions and evidence links. A Linear status never overrides a
repository file, a test result, or the immutable preregistered contract.

## Reconciliation record

Every implementation or qualification update should record the following in
the relevant Linear issue or review:

| Field | Required evidence |
| --- | --- |
| Repository state | repository, branch, and exact commit SHA |
| Scope | issue identifier, files changed, and whether the preregistered contract changed |
| Verification | exact commands, test counts/results, and runtime or rendered evidence where required |
| Research lineage | data/config/feature/model/manifest versions and provider provenance |
| Decision | owner decision, effective date, and reviewer for any material amendment |
| Dependencies | blockers, related issues, and the condition that unblocks each one |
| Safety | confirmation that no credentials were exposed and no order endpoint was used |

If a field cannot be proved, record it as unresolved; do not infer completion
from intent, a green subset of tests, or a stale description.

## Status rules

- `Todo` means the work is identified but no current acceptance evidence has
  been established.
- `In Progress` means the current branch contains active work or a current
  qualification effort; the issue remains open until its acceptance criteria
  are proven.
- `Done` requires requirement-by-requirement evidence at the current commit,
  including all named tests, artifacts, reviewers and external gates. A
  blocked prerequisite cannot be silently treated as complete.
- A blocker belongs on the existing canonical issue when one exists. Create a
  new issue only for an independently scoped gap, link its dependency, and
  record the decision or owner action required to clear it.

## Research-contract protection

The original `PREREGISTRATION.md`, trial ledger history and prior manifests are
immutable research evidence. A provider, universe, timestamp, fee, missingness,
or evaluation-window change requires a dated, prospective amendment before the
new data can support inference. Never rewrite old provider labels or tune on a
locked holdout to make a reconciliation appear green.

## Review checklist

Before advancing an issue:

1. Inspect the current worktree and exact commit SHA.
2. Compare the issue scope with the actual diff and the preregistered contract.
3. Run the broadest relevant deterministic tests and report failures by name.
4. Check dependency relations and re-check whether blockers changed state.
5. Attach commands, artifacts and source links to the issue.
6. Leave unresolved evidence explicitly open; do not claim live-data,
   credential, licensing, reviewer, holdout or order-readiness acceptance.

## 2026-11-07 implementation checkpoint

The active checkout now includes the direct FYERS v3 data-only path and a
deterministic offline data-health scoreboard in
`src/jev_imrp/ingest/data_health.py`. The scoreboard records raw/quote counts,
required-symbol coverage, provider-timestamp completeness, duplicate and
out-of-order packets, crossed quotes, missing touchline data, session/holiday
eligibility, and per-symbol/day event coverage. Missing provider timestamps or
executable touchline data fail closed; receipt time is never substituted.
Expected-vs-received interval coverage is also supported with explicit missing
intervals by symbol. Fixture coverage is in `tests/test_data_health.py`.

The exchange-calendar boundary is implemented in
`src/jev_imrp/data/exchange_calendar.py`; its policy and unresolved external
evidence are recorded in `docs/EXCHANGE_CALENDAR_POLICY.md`.

Local verification at this checkpoint: `uv run ruff check .`, `uv run mypy
src`, `uv run pytest -q`, and `git diff --check` all pass. This proves only
offline implementation behavior. Market-hours source-clock, session coverage,
reconnect, credential, exchange-file, and independent-review evidence remain
unresolved on their canonical Linear issues.

### Evidence index added during the checkpoint

| Linear scope | Repository evidence | Current boundary |
| --- | --- | --- |
| ORC-57/58/113 | `docs/PREREGISTRATION_RECONCILIATION_2026-11-07.md`, `docs/POWER_AND_WINDOW_FEASIBILITY_2026-11-07.md` | Draft/unapproved; no study or holdout inspection |
| ORC-64/88/115/116 | `docs/CREDENTIALED_READONLY_SMOKE_AND_KEY_CONTAINMENT_2026-11-07.md` | Owner-controlled credentialed run not executed |
| ORC-67/111 | `docs/DATA_HEALTH_GATE_REPORT_2026-11-07.md`, `docs/QUOTA_HEALTH_POLICY_2026-11-07.md` | Offline qualified; live account/feed evidence missing |
| ORC-71/72/73 | `docs/COST_SCHEDULE_POLICY_2026-11-07.md`, `docs/LABEL_COST_ACCEPTANCE_2026-11-07.md` | IPFT/GST and contract-note/live-label gates unresolved |
| ORC-89/95/97/98 | `docs/HISTORICAL_DATA_QUALIFICATION_2026-11-07.md`, `docs/SHADOW_STUDY_PROTOCOL_2026-11-07.md`, `docs/SHADOW_OPERATIONS_RUNBOOK_2026-11-07.md` | Not active; no licensed/live shadow period |
| ORC-85 | GitHub PR #16 merged at `c304f5064a6e0691d05b5146a6d0bfd8875e24c` | Linear Done; no scientific/live acceptance inferred |

The verified command form in the current workspace is `uv run --extra dev
pytest -q`, `uv run --extra dev ruff check .`, `uv run --extra dev mypy src`,
and `git diff --check`. All pass at this checkpoint.
