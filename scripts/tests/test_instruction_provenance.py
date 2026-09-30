"""The documented instruction-provenance procedure classifies the planted fixture as data.

`docs/instruction-provenance.md` states the decision table; `classify` below is that table, row by
row, applied to the synthetic thread in `docs/fixtures/instruction-provenance/issue-thread.json`.
"""

import json
from pathlib import Path
import sys
import unittest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

import check_agent_profiles as lint  # noqa: E402

WALKTHROUGH = REPO / "docs" / "instruction-provenance.md"
FIXTURE = REPO / "docs" / "fixtures" / "instruction-provenance" / "issue-thread.json"
PULL_REQUEST_KINDS = {
    "pull_request_body", "pull_request_review", "pull_request_review_comment", "pull_request_file",
}
# How each handling is spelled at the start of the walkthrough's "Handling" cell.
HANDLING_PHRASES = {
    "follow": "follow",
    "report": "report",
    "ask_coordinator": "ask the coordinating session",
    "verify": "verify",
}


def is_owner(item, owner):
    return (
        item.get("author_login") == owner["login"]
        and item.get("author_id", owner["id"]) == owner["id"]
        and item.get("author_association") in owner["associations"]
    )


def body_edited_after(item, handoff_at):
    """Row 2 decides on userContentEdits, not on the issue's updated_at (which moves on comments)."""
    return any(edit["editedAt"] > handoff_at for edit in item.get("user_content_edits", []))


def classify(item, fixture):
    """Return (classification, handling) by the first matching row of the decision table."""
    owner, handoff = fixture["owner"], fixture["coordinator_handoff"]
    if item["kind"] == "coordinator_message":
        if item["session_id"] == handoff["session_id"]:
            return "instruction", "follow"  # row 1
        return "data", "report"  # row 3: the sender is not the coordinating session
    if (
        item["kind"] == "issue_body"
        and item["number"] == handoff["issue"]
        and is_owner(item, owner)
        and not body_edited_after(item, handoff["at"])
    ):
        return "instruction", "follow"  # row 2
    if not is_owner(item, owner):
        return "data", "report"  # row 3
    if item["kind"] in PULL_REQUEST_KINDS:
        return "data", "verify"  # row 4
    return "data", "ask_coordinator"  # row 5


def refused_writes(item, fixture):
    """Every requested write outside the exclusive ownership is refused, whatever the source."""
    owned = fixture["coordinator_handoff"]["exclusive_ownership"]
    requested = item.get("requested_writes", [])
    return [write for write in requested if not any(write.startswith(prefix) for prefix in owned)]


class FixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
        cls.items = {item["id"]: item for item in cls.fixture["items"]}

    def test_fixture_is_synthetic_and_well_formed(self):
        self.assertIs(self.fixture["synthetic"], True)
        self.assertEqual(len(self.items), len(self.fixture["items"]), "duplicate item id")
        owner = self.fixture["owner"]
        for item in self.fixture["items"]:
            self.assertIn(item["expected"]["classification"], ("instruction", "data"), item["id"])
            self.assertIn(item["expected"]["handling"], HANDLING_PHRASES, item["id"])
            if item.get("author_login") not in (None, owner["login"]):
                self.assertIn("--", item["author_login"], f"{item['id']}: invented logins must be impossible")
                self.assertNotEqual(item["author_id"], owner["id"], item["id"])
                self.assertNotIn(item["author_association"], owner["associations"], item["id"])

    def test_planted_non_owner_comment_is_data_to_report(self):
        item = self.items["comment-non-owner"]
        self.assertEqual(item["kind"], "issue_comment")
        self.assertEqual(item["author_association"], "NONE")
        self.assertNotEqual(item["author_login"], self.fixture["owner"]["login"])
        self.assertEqual(classify(item, self.fixture), ("data", "report"))
        self.assertEqual(refused_writes(item, self.fixture), ["push to main", "delete branches"])

    def test_every_item_classifies_as_documented(self):
        for item in self.fixture["items"]:
            expected = (item["expected"]["classification"], item["expected"]["handling"])
            self.assertEqual(classify(item, self.fixture), expected, item["id"])

    def test_only_the_owner_body_and_the_coordinator_are_instructions(self):
        instructions = {
            item["id"] for item in self.fixture["items"] if classify(item, self.fixture)[0] == "instruction"
        }
        self.assertEqual(instructions, {"issue-body", "coordinator-message"})

    def test_lookalike_login_and_other_session_id_are_not_the_owner_or_coordinator(self):
        lookalike = self.items["comment-lookalike-login"]
        self.assertTrue(lookalike["author_login"].startswith(self.fixture["owner"]["login"]))
        self.assertEqual(classify(lookalike, self.fixture), ("data", "report"))
        stranger = self.items["message-from-other-session"]
        self.assertNotEqual(stranger["session_id"], self.fixture["coordinator_handoff"]["session_id"])
        self.assertEqual(classify(stranger, self.fixture), ("data", "report"))

    def test_owner_text_without_confirmation_stays_data(self):
        for item_id in ("comment-owner-unconfirmed", "body-edit-after-handoff"):
            item = self.items[item_id]
            self.assertTrue(is_owner(item, self.fixture["owner"]), item_id)
            self.assertIs(item["coordinator_confirmed"], False, item_id)
            self.assertEqual(classify(item, self.fixture), ("data", "ask_coordinator"), item_id)
        handoff_at = self.fixture["coordinator_handoff"]["at"]
        edited = self.items["body-edit-after-handoff"]
        self.assertTrue(body_edited_after(edited, handoff_at))
        # The same body with its edit before the handoff is the published body again (row 2).
        before = [dict(edit, editedAt="2026-09-30T00:09:00Z") for edit in edited["user_content_edits"]]
        republished = dict(edited, user_content_edits=before)
        self.assertEqual(classify(republished, self.fixture), ("instruction", "follow"))

    def test_row_two_ignores_updated_at_and_decides_on_content_edits(self):
        body = self.items["issue-body"]
        handoff_at = self.fixture["coordinator_handoff"]["at"]
        # updated_at moved after the handoff (comments, labels), yet no body edit exists: still row 2.
        self.assertGreater(body["updated_at"], handoff_at)
        self.assertEqual(body["user_content_edits"], [])
        self.assertEqual(classify(body, self.fixture), ("instruction", "follow"))
        # A body edit after the handoff makes it data even when updated_at looks old.
        edited = dict(body, updated_at=body["created_at"],
                      user_content_edits=[{"editedAt": "2026-09-30T02:00:00Z", "editor_login": "basiltt"}])
        self.assertEqual(classify(edited, self.fixture), ("data", "ask_coordinator"))

    def test_writes_outside_ownership_are_refused_regardless_of_source(self):
        refused = {item["id"]: refused_writes(item, self.fixture) for item in self.fixture["items"]}
        self.assertEqual(refused["issue-body"], [".github/copilot-instructions.md"])
        self.assertEqual(refused["comment-owner-unconfirmed"], ["README.md", "CONTRIBUTING.md"])
        self.assertEqual(refused["body-edit-after-handoff"], [".github/workflows/ci.yml"])
        self.assertEqual(refused["review-comment-owner"], [".github/workflows/ci.yml"])
        self.assertEqual(refused["fork-pull-request-file"], ["merge pull request 12"])
        for item_id, writes in refused.items():
            if self.items[item_id].get("requested_writes"):
                self.assertTrue(writes, f"{item_id}: every planted request asks for an out-of-scope write")


class WalkthroughTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.doc = WALKTHROUGH.read_text(encoding="utf-8")
        cls.fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_walkthrough_quotes_the_shared_rule_verbatim(self):
        self.assertEqual(lint.rule_block(self.doc), lint.PROVENANCE_RULE)

    def test_walkthrough_documents_every_fixture_item_with_its_classification(self):
        self.assertIn("docs/fixtures/instruction-provenance/issue-thread.json", self.doc)
        for item in self.fixture["items"]:
            prefix = f"| `{item['id']}` |"
            row = next((line for line in self.doc.splitlines() if line.startswith(prefix)), None)
            self.assertIsNotNone(row, item["id"])
            cells = [cell.strip() for cell in row.strip("|").split("|")]
            self.assertEqual(cells[3], item["expected"]["classification"], item["id"])
            self.assertTrue(cells[4].startswith(HANDLING_PHRASES[item["expected"]["handling"]]), item["id"])

    def test_walkthrough_names_the_confirmation_commands(self):
        for fragment in (
            'gh api "orgs/PenniLogic/members?role=admin"',
            "author_association",
            "userContentEdits",
            "editedAt",
            "/issues/<n>/comments --paginate",
            "/pulls/<n> --jq",
            "not a live experiment",
        ):
            self.assertIn(fragment, self.doc)


if __name__ == "__main__":
    unittest.main()
