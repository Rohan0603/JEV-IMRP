# Copy-ready issue and comment examples

**Execution model:** The human owner manually invokes or schedules Codex, OpenCode, ChatGPT Work, or another tool as a Chief/Worker session. The session reads the live GitHub Project and selects eligible work. GitHub never starts an AI model when a role label changes. Use a unique per-session claim and reconcile overlapping claims before doing work.


These are **examples only**. Replace numbers, SHA values, and links. Never assert that a test ran or approval happened because the example contains those words.

## Session claim and release (advisory, not a lock)

Each separately started session checks **all** issue comments and live Project state before work (or explicitly records the Project access gap in a metadata-bootstrap-only session). These example timestamps and IDs are illustrative, not proof of a real agent run.

```text
<!-- claim:ISSUE-142:codex-20261108T090000Z-1 -->
START — 2026-11-08T09:00:00Z
Role: worker; tool: Codex; run_id: codex-20261108T090000Z-1
Expires: 2026-11-08T10:00:00Z
Baseline: <actual-sha>; intended work: <approved scope>.
```

Read the START back, re-fetch claims and Project Status, and **stop without changing Status** if a different active or ambiguous claim appears. Even two sessions using this sequence do not have an atomic lock.

At actual completion, escalation or deliberate stop, publish and verify the evidence/handoff **before** publishing:

```text
<!-- claim-end:ISSUE-142:codex-20261108T090000Z-1 -->
CLAIM RELEASED — 2026-11-08T09:40:00Z
Run ID: codex-20261108T090000Z-1
Outcome: <verified handoff / blocked / closeout URL>
Next owner: <chief / worker / none>
```

A claim with missing release or uncertain expiry needs Chief reconciliation. Never infer successful completion merely from elapsed time.

## 0. First handoff: GitHub metadata setup (required on every new issue)

Chief includes this section even when the metadata is already complete. It is an **example**, not actual evidence:

```text
GITHUB METADATA SETUP — FIRST HANDOFF
Project: https://github.com/users/OWNER/projects/7 (owner=OWNER, number=7)
Project membership: VERIFIED, evidence <exact item URL / API read-back>
Project Status: Backlog VERIFIED; intended after Chief DoR: Ready
Priority: P2 Medium — selected by Chief, VERIFIED / TO SET
Milestone: NOT REQUIRED (or approved existing milestone #3 and title)
Dependencies/sub-issues: NOT REQUIRED (or exact issue URLs + native relationship)
Assignee/other fields: NOT REQUIRED (or explicitly chosen values)
Missing actions: none / list each operation to be completed by authorized Worker
Mode: Normal implementation handoff ONLY if full DoR and Project Ready read-back succeed.
```

### Bootstrap-only alternative when ChatGPT cannot configure Projects/milestones/dependencies

An **unprojected** Issue is never treated as implementation-ready merely because ChatGPT can give it the `worker` label. If Chief is blocked on metadata operations but knows the **approved** target values, Chief creates the repo Issue with `chief`, then writes:

```text
<!-- metadata-handoff:ISSUE-143-chief-001 -->
METADATA BOOTSTRAP HANDOFF — ISSUE #143
Role: worker; scope: METADATA ONLY; IMPLEMENTATION NOT AUTHORIZED
Selected Project: https://github.com/users/OWNER/projects/7
Priority: P2 Medium (Chief decision); Project status during bootstrap: Backlog
Milestone: existing #3 "M1"; DO NOT CREATE an unapproved milestone
Dependencies: #139 blocks #143 (add native relationship); assignee: NOT REQUIRED
Current evidence: Project membership/Status/Priority NOT VERIFIED (ChatGPT API 403)
To do: add/check Project membership, set/read Backlog + P2, assign milestone #3,
set/check native dependency #139, return each field's read-back evidence.
After evidence: worker -> chief; remove metadata-bootstrap; release claim.
Chief then performs the separate DoR and normal implementation handoff.
```

Chief updates issue labels to `worker` + `metadata-bootstrap` **only** and reads them back. This is the **only** exception to requiring Project membership for a Worker *metadata task*. A **separately owner-invoked** Worker may receive the issue number directly or, in explicitly requested bootstrap queue mode, search the repository for both labels. Ordinary implementation queues must exclude `metadata-bootstrap` issues.

Worker confirms there is no conflicting claim, posts/reads back START, performs **only** the authorized metadata writes and reads them back. The successful handback has **separate ordered steps**, not one prematurely optimistic comment:

```text
<!-- metadata-prepared:ISSUE-143-worker-001 -->
METADATA HANDOFF PREPARED — ISSUE #143
Project: <exact item API/URL>; Status: Backlog (read back); Priority: P2 (read back)
Milestone: #3 "M1" (read back); blocked-by: #139 (read back)
Outstanding/NOT VERIFIED: none / <each exact blocked or unknown field>
Next: update complete labels to chief, remove metadata-bootstrap, read back labels.
IMPLEMENTATION NOT AUTHORIZED; this comment is NOT a completed handback.
```

After posting and reading back `METADATA HANDOFF PREPARED`, Worker performs the **one full-label mutation** from `worker + metadata-bootstrap` to `chief`, preserving unrelated classification labels. Worker reads back the entire label set and verifies the primary role is exactly `chief` and the bootstrap flag absent. **Only then** may it post/read back:

```text
<!-- metadata-verified:ISSUE-143-worker-001 -->
METADATA VERIFIED — ISSUE #143
Prepared evidence: <exact prepared-comment URL and verified values>
Handback labels: chief only (actual read-back URL/result)
Outstanding/NOT VERIFIED: none; Project Ready was NOT asserted.
No product implementation performed.
```

If any field is missing despite successful label handback, post/read `METADATA BLOCKED` instead with the exact unresolved values. After a verified final comment, post/read the matching `CLAIM RELEASED` marker. Missing metadata never silently authorizes code or a PR. If milestone creation is required, Chief supplies exact approved title/scope or escalates to owner; Worker never guesses.

### Bootstrap reconciliation after competing or interrupted work

Claims are advisory, not locks. If two Workers have active or ambiguous claims, both stop before metadata writes and Chief reconciles the issue. A clean audit or pending-bootstrap marker does not resolve that conflict or authorize a write.

For every Project mutation, record the handoff ID and read back the complete item list and the specific field after the write. If an add or field update times out, returns an incomplete response, or is interrupted, do not repeat it blindly: re-query first, detect whether the item/field already exists, and retry only the missing operation. More than one matching Project item is a duplicate-assignment blocker; stop and escalate instead of selecting one. Record successful fields separately from unresolved fields.

If the prepared comment, `worker` → `chief` label change, bootstrap-label removal, final handback comment, or any read-back fails, do not publish `METADATA VERIFIED` or claim completed release. Re-fetch the **actual** issue labels; post `RECONCILIATION REQUIRED` with exact successful, failed and unknown writes when safe, then stop and report to Chief/human. Never assert Chief ownership when `worker` remains. Require fresh verification before compensation. The offline validator can flag structural duplicates and invalid status/role combinations, but cannot prove claim exclusivity, API atomicity, retry safety or handback success; those remain **NOT VERIFIED** until live read-back.

Only after Chief independently certifies the full Definition of Ready and reads back `worker` + Project `Ready` does the normal implementation sequence in examples A–D apply.

## A. Chief creates a new implementation issue

**GitHub Issue:** `#142 Add idempotent request replay guard`

**Project Status:** `Backlog`; labels: `chief`.

**Outcome:** On repeated identical request IDs, return the persisted result without issuing another external side effect. Supports concurrency control and retry-safety.

**Baseline:** `main` observed SHA `<actual-sha>`; existing files: `<paths>`; API version `<version>`.

**Dependencies:** `#139 durable request store` (native `blocked by`). Explicitly record status and evidence before Ready.

**In scope:** storage-level uniqueness, read-before-write, restart recovery, unit/concurrency tests; no provider change or new runtime permissions.

**Acceptance:**
- Given the same request ID and payload on retried calls, when second call arrives, then exactly one side effect is attempted and a stable result is returned.
- Given same ID but different payload, then fail safely with a specified conflict status.
- Given restart after a committed result, then replay returns the persisted result.
- Given a concurrent collision, then the losing worker returns/retries according to documented policy without double execution.

**Exact checks:** `<real-command>` (unit tests), `<real-command>` (integration), `<real-command>` (lint), CI `<workflow-name>`. Record all PASS/FAIL/NOT RUN.

**Failure/rollback:** Protect existing migration path, require forward/backward schema compatibility and an explicit backout plan. Risk `Elevated` if live data migration applies.

**Chief prepares handoff:**

```text
<!-- handoff-id:142-chief-worker-001 -->
CHIEF RESPONSE/HANDOFF — 2026-11-07T12:00:00Z
DoR: PASS on issue #142; #139 Done verified by commit <sha>.
Decision: choose database unique constraint + recorded replay result.
Approved file scope: <paths>. Rejected: in-memory-only deduplication.
Worker instructions: implement Given/When/Then cases above and capture actual commands/CI.
Risk: Normal for fixture implementation; any production migration escalates.
Next action: change chief -> worker and Project Status -> Ready; read back.
```

Before Worker begins, a real API read-back must show **only** `worker`, `Status=Ready`, and the HANDOFF comment.

## B. Worker requests Chief assistance

```text
<!-- handoff-id:142-worker-chief-002 -->
REQUEST FOR CHIEF — 2026-11-07T15:15:00Z
Current head: <commit-sha>, branch feat/142-replay.
Problem: recorded replay payload can contain sensitive account identifiers.
Evidence: failing negative test <path>, command <command>, exit 1.
Options: hash canonical payload with salt; store redacted fields; encrypt payload.
Decision needed: approved retention/trust boundary and compatibility of old records.
Blocked scope: persistence schema change. Other changes paused to avoid rework.
Next action: change worker -> chief; Project Status -> Blocked; read back.
```

Chief cannot silently invent approval. Research alternatives, record source-backed choice and if needed `chief`+`human` while the Project is `Blocked`. When resolved, post an actionable Chief handoff, remove `human` if present, change `chief` → `worker`, and restore the correct Project status.

## C. Human-only gate, with Chief managing labels

```text
HUMAN ACTION REQUIRED — 2026-11-07T16:00:00Z
Owner must approve deployment of migration #142 on exact PR head <sha>.
Why human: production irreversible data changes; owner release approval required.
Acceptance evidence: issue comment with scope, head SHA and explicit approval.
Until then: chief + human; Project Status=Blocked; no merge/deploy.
```

After the owner records an action, Chief verifies the head and evidence, posts `HUMAN RESOLVED`, **removes `human` itself**, and completes the authorized workflow. The human never has to relabel issues.

## D. Worker completion and Chief acceptance

```text
REVIEW HANDOFF — 2026-11-07T17:00:00Z
Issue #142, PR #188, exact head <sha>, base main@<sha>.
Changed: <file paths>. Acceptance matrix: <table with proof URLs>.
Checks: <command 1> exit 0, <command 2> exit 0; CI <URL> success on <sha>.
NOT RUN: hardware integration; linked validation #191 with owner.
Security/rollback: <specific impacts>. Remaining risk <...>.
Next action: worker -> chief; Project Status=In Review; independent review.
```

Chief reviews the exact head and may request changes (`chief` → `worker`) or authorize a deliberate normal-risk merge after real CI/reviewer gates. For elevated/critical, require owner approval on exact head. Post CLOSEOUT with final merged SHA and verify Project Done + closed issue.

## E. Discussion → actionable decision

Research discussion: `D#21: Compare SQLite-backed lease vs. GitHub-only advisory lease`. Chief and specialists debate options in Discussions. Chief summarizes the approved outcome in issue `#210 ADR: Dispatch serialization`, links the Discussion, records real sources/version dates, and creates blocked-by implementation issues with DoR. No Worker executes a task directly from D#21.
