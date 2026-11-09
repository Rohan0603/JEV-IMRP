"""Offline invariant tests, no network access or GitHub writes."""
import unittest
from validate_governance import audit, reconcile_metadata_bootstrap, check_metadata_handback_order

REPO = "owner/project"


def issue(n, labels, state="open"):
    return {"node_id": f"ISSUE_{n}", "number": n, "labels": [{"name": v} for v in labels],
            "state": state, "html_url": f"https://github.com/{REPO}/issues/{n}"}


def item(n, status):
    return {"content": {"id": f"ISSUE_{n}", "repository": {"nameWithOwner": REPO}},
            "fieldValueByName": {"name": status}}


class TestAudit(unittest.TestCase):
    def test_valid_handoffs(self):
        a = audit(REPO,
                  [issue(1, ["chief"]), issue(2, ["worker"]),
                   issue(3, ["chief", "human"]), issue(4, ["chief"], "closed")],
                  [item(1, "Backlog"), item(2, "Ready"),
                   item(3, "Blocked"), item(4, "Done")])
        self.assertEqual(a["violation_count"], 0)

    def test_bad_role_and_status(self):
        a = audit(REPO,
                  [issue(1, ["worker", "chief"]), issue(2, ["human", "worker"]),
                   issue(3, ["chief"])],
                  [item(1, "Ready"), item(2, "In Progress")])
        codes = {v["code"] for v in a["violations"]}
        self.assertTrue({"ROUTE_XOR", "HUMAN_WITHOUT_CHIEF", "PROJECT_MEMBERSHIP",
                         "HUMAN_NOT_BLOCKED"}.issubset(codes))

    def test_closeout_invariants(self):
        a = audit(REPO, [issue(1, ["chief"], "open"), issue(2, ["chief"], "closed")],
                  [item(1, "Done"), item(2, "In Review")])
        codes = {v["code"] for v in a["violations"]}
        self.assertTrue({"UNFINISHED_CLOSEOUT", "PREMATURE_CLOSE"}.issubset(codes))


    def test_unprojected_metadata_bootstrap_is_pending_not_ready(self):
        a = audit(REPO,
                  [issue(1, ["worker", "metadata-bootstrap"]),
                   issue(2, ["chief"])],
                  [item(2, "Backlog")])
        self.assertEqual(a["violation_count"], 0)
        self.assertEqual(a["metadata_bootstrap_pending_count"], 1)
        self.assertEqual(a["metadata_bootstrap_pending"][0]["issue_number"], 1)
        self.assertEqual(a["metadata_bootstrap_pending"][0]["project_items"], 0)
        self.assertIsNone(a["metadata_bootstrap_pending"][0]["project_status"])

    def test_bootstrap_with_project_backlog_is_still_pending(self):
        a = audit(REPO, [issue(1, ["worker", "metadata-bootstrap"])],
                  [item(1, "Backlog")])
        self.assertEqual(a["violation_count"], 0)
        self.assertEqual(a["metadata_bootstrap_pending_count"], 1)
        self.assertEqual(a["metadata_bootstrap_pending"][0]["project_status"], "Backlog")

    def test_bootstrap_with_ready_or_invalid_role_is_rejected(self):
        a = audit(REPO,
                  [issue(1, ["worker", "metadata-bootstrap"]),
                   issue(2, ["chief", "metadata-bootstrap"]),
                   issue(3, ["worker", "metadata-bootstrap"], "closed")],
                  [item(1, "Ready"), item(2, "Backlog")])
        codes = {v["code"] for v in a["violations"]}
        self.assertTrue({"BOOTSTRAP_STATUS", "BOOTSTRAP_ROUTE", "BOOTSTRAP_CLOSED"}
                        .issubset(codes))

    def test_duplicate_project_assignments_are_rejected(self):
        a = audit(REPO, [issue(1, ["worker", "metadata-bootstrap"])],
                  [item(1, "Backlog"), item(1, "Backlog")])
        self.assertIn("PROJECT_MEMBERSHIP", {v["code"] for v in a["violations"]})

    def test_audit_is_advisory_about_bootstrap_authorization(self):
        a = audit(REPO, [issue(1, ["worker", "metadata-bootstrap"])], [])
        self.assertEqual(a["violation_count"], 0)
        self.assertIn("Bootstrap handoff authorization", a["not_checked"])
        self.assertIn("Competing Worker claims", a["not_checked"])

    def test_reconciliation_flags_claims_partial_writes_retries_duplicates_and_handback(self):
        findings = reconcile_metadata_bootstrap(
            active_claims=2,
            project_item_count=2,
            pending_writes=["Priority"],
            retry_after_ambiguous_error=True,
            handback_labels_read_back=False,
            handback_comment_read_back=False,
        )
        self.assertEqual(
            {"CLAIM_CONFLICT", "PROJECT_DUPLICATE", "PARTIAL_WRITE",
             "RETRY_REQUIRES_READBACK", "HANDBACK_NOT_VERIFIED"},
            set(findings),
        )


    def test_handback_requires_ordered_successful_readbacks(self):
        valid = ["PREPARED_COMMENT_READBACK", "CHIEF_LABEL_READBACK",
                 "FINAL_COMMENT_READBACK", "CLAIM_RELEASED_READBACK"]
        self.assertEqual(check_metadata_handback_order(valid), [])

    def test_handback_rejects_premature_final_or_missing_label(self):
        premature = ["PREPARED_COMMENT_READBACK", "FINAL_COMMENT_READBACK",
                     "CLAIM_RELEASED_READBACK"]
        findings = check_metadata_handback_order(premature)
        self.assertIn("HANDBACK_OUT_OF_ORDER", findings)
        self.assertIn("HANDBACK_INCOMPLETE", findings)
        missing = ["PREPARED_COMMENT_READBACK", "CHIEF_LABEL_READBACK"]
        self.assertIn("HANDBACK_INCOMPLETE", check_metadata_handback_order(missing))


if __name__ == "__main__":
    unittest.main()
