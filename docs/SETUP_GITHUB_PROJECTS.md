# Bootstrap playbook — GitHub Projects for a solo human + multiple AI agents

**This is a setup guide, not a separate task database.** The configured GitHub Project and linked GitHub Issues are the live task truth.

## 1. Scope and prerequisites

- Copy `AGENTS.md`, `.github/ISSUE_TEMPLATE/agent_task.yml` and `.github/pull_request_template.md` into **a new repository**. Replace `[PROJECT_NAME]`, `[OWNER/REPOSITORY]`, `[PROJECT_URL]` and `[DEFAULT_BRANCH]` before dispatching agents.
- Use account/repo/Project visibility appropriate for the research and assets. A public repository and a public Project may expose issue content, attachments, comments, Discussions and PR logs; treat them separately.
- Install GitHub CLI (`gh`) and authenticate interactively. GitHub Projects has the `project` OAuth scope; `repo` may be needed for private repository operations. Grant *least privilege* appropriate to the actual tasks. Example: `gh auth refresh -s project,repo` (avoid copying tokens into scripts or chat).
- Check authorization: `gh auth status`, `gh api user --jq .login`, `gh project list --owner [OWNER]`.
- Neither GitHub Projects labels nor the `gh` CLI trigger AI agents. The human owner invokes or schedules Chief/Worker directly within Codex, OpenCode, ChatGPT Work, a custom orchestrator, or another chosen runtime. Do not install an agent-dispatching service, provider endpoint or runner credentials for this template.

### Troubleshooting Projects v2 permissions for Chat-mode agents

Repository Issue access and Projects v2 access are separate permission surfaces. A successful issue read, label update, or comment does not prove that an agent can read a Project, its item membership, or its Status and Priority fields. Labels and Project Status are independent: changing `chief`/`worker` never changes Status, and labels never launch a model. The human must invoke each Chief or Worker session separately.

Use a read-only diagnostic, without requesting or pasting a token into chat:

```sh
gh auth status
gh api user --jq .login
gh project list --owner OWNER --limit 100
gh project item-list PROJECT_NUMBER --owner OWNER --limit 1000 --field "Status" --field "Priority" --format json
gh project field-list PROJECT_NUMBER --owner OWNER
```

Interpret the result narrowly:

- **Tool discovery failure:** the runtime exposes no Projects operation. Record Project membership, Status, and Priority as **NOT VERIFIED**; do not guess or make a write.
- **HTTP 401:** authentication is missing or expired. Stop and ask the owner to authenticate through the approved UI/CLI flow; never ask for the credential value in chat.
- **HTTP 403:** the request reached GitHub but this authentication path is not authorized for that Project or operation. Do not infer that the Project or item is absent, and do not retry by broadening permissions blindly.
- **HTTP 200 with no matching item or field:** complete pagination and filtering first. If membership, Status, or Priority still cannot be read back, keep that gate **NOT VERIFIED**.

Personal (user-owned) Projects and organization-owned Projects do not have identical token compatibility. GitHub's [Projects REST items documentation](https://docs.github.com/en/rest/projects/items) notes that adding an item to a user-owned Project does not work with GitHub App user access tokens, GitHub App installation tokens, or fine-grained personal access tokens, while organization-owned Project operations have their own documented fine-grained permission requirements. Check the exact endpoint and authentication method before relying on a permission. For supported authentication paths, `project` (or a read-only `read:project` permission where offered) may be relevant; it does not make every personal-Project operation available. The [Projects API guide](https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/using-the-api-to-manage-projects) is the authority for the current path and operation.

#### Chief fail-closed decision: bootstrap versus implementation

1. Read the issue, all comments, labels, dependencies, and the complete Project item/field result.
2. For **normal implementation**, any discovery failure, 401, 403, or incomplete membership/field data means Project membership/Status/Priority remain **NOT VERIFIED**: retain `chief`, do not claim `Ready`, and do not start implementation.
3. For **metadata bootstrap only**, Chief may route `worker + metadata-bootstrap` without Project membership **only** when an exact owner/Chief-authorized `METADATA BOOTSTRAP HANDOFF` supplies the target Project, approved values, permissions gap, idempotency plan and `IMPLEMENTATION NOT AUTHORIZED`. The separately invoked Worker must verify that handoff and claims before any metadata write; a failure still means `METADATA BLOCKED`, not implementation authorization.
4. A clean governance audit or `metadata_bootstrap_pending` result is advisory and does not authorize metadata operations. Worker independently verifies the handoff, target Project/fields, permissions, claims and current membership before writing.
5. Only after every metadata mutation is read back and the issue returns to Chief may Chief independently certify the full DoR and perform the separate `worker` + Project `Ready` implementation handoff. A label-only transition is never evidence of Project `Ready`.

Worker handback follows the same separation: provide the exact PR/head and test evidence, change `worker` → `chief`, and read back the label. Project `In Review` or `Done` still requires its own authorized Project read-back; do not substitute an Issue label.

## 2. Create a live Project (CLI examples)

```sh
# Check existing projects first — do not create duplicates.
gh project list --owner OWNER --limit 100
# Replace OWNER and PRODUCT_NAME with real values.
gh project create --owner OWNER --title "PRODUCT_NAME — Engineering"
# Use the project number returned above.
gh project link PROJECT_NUMBER --owner OWNER --repo OWNER/REPO
# A newly created Project normally includes a built-in Status field. Edit its options in the UI.
gh project field-list PROJECT_NUMBER --owner OWNER
# Additional custom fields; do not duplicate a built-in Status field.
gh project field-create PROJECT_NUMBER --owner OWNER --name Priority --data-type SINGLE_SELECT --single-select-options 'P0 Critical,P1 High,P2 Medium,P3 Low'
gh project field-create PROJECT_NUMBER --owner OWNER --name Risk --data-type SINGLE_SELECT --single-select-options 'Normal,Elevated,Critical'
```

Configure **Status** in the Project settings to: `Backlog`, `Ready`, `In Progress`, `Blocked`, `In Review`, `Done`, `Canceled`. If GitHub initializes three default values, edit the field options directly. Ensure views and built-in workflows refer to these actual options.

Add saved views in the GitHub Projects UI: All/table, Chief queue, Worker ready, Executing, In Review, Human blocked, Blocked, and optionally Roadmap. Apply saved filters such as `label:chief -label:human -status:Done -status:Canceled`, `label:worker status:Ready`, `label:human`, and `status:Blocked` after checking support in the current interface.

## 3. Labels and issue forms

In the repository's **Issues → Labels** create exact lowercase `chief`, `worker`, `human` and non-routing `metadata-bootstrap` (or use `scripts/bootstrap_github.ps1 -Repo OWNER/REPO -Execute`, after a dry-run). The bundled issue form defaults to `chief` and requires Chief to specify **target Project, approved Priority, milestone (or NOT REQUIRED), native dependencies/sub-issues, and missing API operations**. GitHub requires a label to already exist if a form should apply it. Do not add redundant status labels or use `human` alone.

Configure Project built-in **auto-add** to capture newly created/updated issues from the repository. **Backfill existing issues separately**: auto-add does not retroactively include old items.

```sh
# Example manual backfill; repeat for actual existing issue URLs (check membership first).
gh project item-add PROJECT_NUMBER --owner OWNER --url https://github.com/OWNER/REPO/issues/123
# Project field Status must be updated on the item, not by changing a status label.
# `--url` identifies the issue/PR item; `--owner` identifies the Project owner.
gh project item-edit PROJECT_NUMBER --owner OWNER --url https://github.com/OWNER/REPO/issues/123 --field "Status" --value "Ready"
# Update one field per invocation for a non-draft issue.
gh project item-edit PROJECT_NUMBER --owner OWNER --url https://github.com/OWNER/REPO/issues/123 --field "Priority" --value "P2 Medium"
# Inspect authoritative Project items and values.
gh project item-list PROJECT_NUMBER --owner OWNER --limit 1000 --format json
```

Do not use untracked Project **draft** items as implementation tasks. Convert drafts to repository issues before Worker dispatch. For cross-repository dependencies, use native GitHub blockedBy/blocking relations when available, and link them in issue body as readable context.

## 4. Agent-initiated scheduling, review and human gates

Chief and Worker are **roles** fulfilled by independently invoked AI sessions, not named hosted bots. The owner can start any supported tool manually or schedule its check using that tool's own scheduler. The session must read `AGENTS.md`, query the live Project (all pages), and select eligible Project-linked issues by role and Status.

- **Single-issue:** specify exactly one GitHub issue and role. Normal implementation must verify live labels, Project membership, `Ready`, dependencies and active claim. An issue explicitly labeled `worker + metadata-bootstrap` is a **separate metadata-only bootstrap** task that may start from its repository Issue URL before Project membership exists; implementation is forbidden until Chief subsequently certifies DoR.
- **Queue:** specify a role and issue-count limit. Normal implementation enumerates the Project's paginated `worker + Ready` items **excluding `metadata-bootstrap`**. A separately requested **metadata-bootstrap queue** may search only repository issues with both `worker` and `metadata-bootstrap` and perform metadata-only writes. Never merge those two queues or process a conflicting claim.
- **Safe claim:** inspect all comments for active/ambiguous claims, comment `START` with a unique run ID and UTC expiry, then read back and re-check competing claims **before** changing Project Status or doing work. On a conflict, stop without relabeling or changing Status. After an evidenced handoff or stop, write and verify the matching `<!-- claim-end:ISSUE-N:RUN-ID -->` `CLAIM RELEASED` comment. Missing release or expired claims require Chief reconciliation; labels/comments are not distributed locks.
- **Handoffs:** first Chief → Worker handoff ALWAYS contains the `GITHUB METADATA SETUP` checklist (`Project`, `Status`, `Priority`, milestone, native relations, assignee/required fields; VERIFIED / TO SET / NOT REQUIRED / NOT VERIFIED). If Chief cannot update/verify required metadata, the exception is a **`METADATA BOOTSTRAP HANDOFF`** with `worker + metadata-bootstrap` and **no Project `Ready` assertion**. Worker completes only approved metadata, reads back, and routes to Chief; Chief alone certifies DoR before normal implementation handoff. For ordinary handoffs, update `chief` XOR `worker` and Project Status, and read back all mutations. `human` only coexists with Chief, who clears it after the human-only gate is verified.
- **Governance CI:** keep read-only audit of labels, Project membership and statuses. Do not let GitHub Actions automatically dispatch AI sessions.

There are **no** `AGENT_RUNNER_URL`, `AGENT_RUNNER_TOKEN`, provider/model deployment, or agent-triggering workflows required for this approach. Token credentials used for optional read-only auditing remain separate from the agents' own authorized GitHub access.

### First-handoff metadata bootstrap — for a Chief lacking Projects/Milestones API

This is **not** a substitute for Project `Ready`; it is a bounded owner-authorized **metadata-only** exception. Before the first Worker implementation handoff, Chief records exact target values and missing operations in the issue. The values must be selected by Chief or explicitly approved by owner; missing decisions are a blocker, not Worker discretion.

| Metadata | Chief supplies | Worker, if Chief API is blocked |
|---|---|---|
| GitHub Project | canonical URL, owner, project number | Verify current membership, add if missing, read back |
| Project fields | selected Priority, Backlog during bootstrap, required custom fields | Set only approved values; read back per field |
| Milestone | exact existing title/number, or explicitly NOT REQUIRED | Assign existing milestone; create only if separately approved with an exact title and scope |
| Native dependencies / sub-issues | exact blocking/parent issue URLs and relationship | Apply and read back supported native relations; escalate if unavailable |
| Other issue fields | exact assignee or other approved values | Apply and independently verify |

1. Chief posts `METADATA BOOTSTRAP HANDOFF`, stating **IMPLEMENTATION NOT AUTHORIZED**, with the checklist, sources, permission gaps and a unique handoff ID. Set **`worker` + `metadata-bootstrap`** and read back the issue labels. The Project may not yet contain the issue.
2. The human invokes a Worker in **single-issue** or explicitly requested **metadata-bootstrap queue** mode. The Worker independently verifies the handoff, exact approved values, permissions, idempotency and all claims, posts/reads a unique START, then checks conflicts before making any metadata write. Do not branch or implement code. A clean audit is not authorization.
3. Before adding, query the complete Project item list. If the issue is absent, add it once and read back; if it appears more than once, stop for reconciliation. After any timeout or partial response, re-query before retrying and repeat only missing operations. Read back Project item/fields and milestone/dependency relationships after each write. Where supported, `gh project item-list PROJECT_NUMBER --owner OWNER --limit 1000 --field "Status" --field "Priority" --format json` can assist read-back; check pagination and values. Do not paste tokens in chat or create a milestone speculatively.
4. After per-field metadata read-back, Worker posts/reads a uniquely tagged `METADATA HANDOFF PREPARED` comment describing verified and missing fields, with **no claim that the role transfer succeeded**. Worker then replaces `worker + metadata-bootstrap` with `chief` in one issue-label update and independently verifies the entire new label set.
5. **Only after** the prepared comment and `chief` label read-back both succeed, Worker posts/reads the final `METADATA VERIFIED` (all approved metadata verified) or `METADATA BLOCKED` (unresolved fields); then posts/reads the matching `CLAIM RELEASED`. If any prepared comment, label mutation, final comment or read-back fails, record `RECONCILIATION REQUIRED` with the **actual** observed labels and successful/unknown operations when safe, stop and report the blocker. Do not claim `METADATA VERIFIED` or completed ownership transfer, and never assume the issue is now owned by Chief.
6. If incomplete, Chief retains/resumes ownership only after a verified handback and resolves the blocker. No implementation is authorized by bootstrap.
7. Chief independently verifies all metadata, issues the **separate** implementation `CHIEF RESPONSE/HANDOFF` only after full DoR, and sets `worker + Ready` with Project read-back. If Chief still cannot access Projects, it requests an authorized verifier and does not claim `Ready`.

**No automatic agents:** label changes, Project events, Actions and comments must not invoke Codex/Claude Code/OpenCode/ChatGPT. A human invokes or schedules sessions in those tools. See `AGENTS.md` §4 and `docs/WORKFLOW_EXAMPLES.md` §0 for the canonical policy and copy-ready comment templates.

## 5. Git and PR controls

- Pick a protected default branch (`main` recommended) and short-lived `feat/<issue>-slug`, `fix/<issue>-slug`, etc. Each worker gets a separate branch and worktree.
- Require PRs and validated checks (tests, lint, typechecking, security scans as relevant); prohibit force pushes and branch deletion where supported.
- Resolve review threads, revalidate exact final PR head, and deliberately squash-merge one logical change; delete feature branch after merge.
- **Solo-owner caveat:** requiring a second GitHub approving reviewer can deadlock a sole-maintainer account. Independent AI review notes do not satisfy GitHub's native review authorization. Choose a real separate authorized reviewer, or keep zero required native approvals *plus* required CI and an explicit Chief review record for Normal risk, with human approval at Elevated/Critical risk. Document the residual enforcement gap; a comment is not as strong as a trusted required status check.
- Do not configure unattended auto-merge or give ordinary workers branch-protection bypass rights.
- Test protections safely: create a small temporary PR, demonstrate blocked merge on failing CI and lack of direct-push/force-push rights where your account allows it, and record observed evidence. Do not merge a known bad PR to test controls.

## 6. GitHub Discussions for agents

Optional. Enable Discussions for the relevant repository. Suggested categories: `Research/RFC` (open-ended), `Architecture` (open-ended), `Questions` (Q&A), `Retrospectives` (open-ended). It is an asynchronous discussion forum, not a reliable agent event bus. GitHub's GraphQL API supports `createDiscussion` and `addDiscussionComment`; each writer still needs authorization and a dispatcher/poller to deliver new replies to actual agent sessions.

When exploration yields an actionable conclusion, Chief creates/updates the **Project-linked Issue**, summarizes decision, sources, rationale and exact next steps, and adds dependencies/acceptance. The Worker should not need to read an unbounded Discussion thread to discover its instructions. PR inline comments remain the right channel for code-level feedback.

## 7. Verification before declaring rollout done

- [ ] Root `AGENTS.md` is automatically discovered and read in full by the chosen agent runtime (test with a fresh session).
- [ ] All actionable issues appear in the Project, including backfilled legacy work; draft work converted to issues.
- [ ] All issues have `chief` XOR `worker`; if `human`, also `chief`.
- [ ] Bootstrap cases carry `worker + metadata-bootstrap` only during metadata setup, and end with `chief` plus independent project/field read-back before code authorization.
- [ ] Every queue view matches a direct, paginated Project API query; reconcile differences.
- [ ] Real Worker DoR/START/implementation/review/closeout cycle passes end-to-end.
- [ ] Worker-to-Chief escalation and Chief-to-Worker handoff record correct comments, role labels and Project Status with read-back.
- [ ] Human-only gate is handled by Chief; owner need not edit labels.
- [ ] PR protections and CI gates are demonstrated, not merely described.
- [ ] API secondary rate limits, permissions and outages produce a **Blocked/recoverable** outcome rather than duplicate comments or missing tasks.

## References

- [GitHub CLI Projects](https://cli.github.com/manual/gh_project)
- [GitHub Projects API](https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/using-the-api-to-manage-projects)
- [GitHub Projects auto-add limitation](https://docs.github.com/en/issues/planning-and-tracking-with-projects/automating-your-project/adding-items-automatically)
- [GitHub Issue forms syntax](https://docs.github.com/en/communities/using-templates-to-encourage-useful-issues-and-pull-requests/syntax-for-issue-forms)
- [GitHub issue dependencies](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/creating-issue-dependencies)
- [GitHub branch rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/available-rules-for-rulesets)
- [GitHub Discussions GraphQL](https://docs.github.com/en/graphql/guides/using-the-graphql-api-for-discussions)
