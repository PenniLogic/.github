"""Tests for scripts/check_agent_profiles.py: real profiles pass, planted violations fail."""

import contextlib
import io
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import check_agent_profiles as lint  # noqa: E402

REPO = SCRIPTS.parent
PROFILE = "---\nname: Synthetic\ndescription: Synthetic profile\ntools: {tools}\n---\n\nBody.\n"


class FixtureMixin:
    """Copy the real profiles and policy into a scratch root that each test may mutate."""

    def make_root(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        root = Path(scratch.name)
        shutil.copytree(REPO / lint.PROFILE_DIR, root / lint.PROFILE_DIR)
        (root / ".github").mkdir()
        shutil.copy(REPO / lint.POLICY_FILE, root / lint.POLICY_FILE)
        return root

    def profile(self, root, stem):
        return root / lint.PROFILE_DIR / f"{stem}.agent.md"

    def replace(self, root, stem, old, new):
        path = self.profile(root, stem)
        text = path.read_text(encoding="utf-8")
        self.assertIn(old, text, f"fixture {stem} lacks {old!r}")
        path.write_text(text.replace(old, new, 1), encoding="utf-8")

    def write(self, root, stem, text):
        self.profile(root, stem).write_text(text, encoding="utf-8")

    def assertViolation(self, root, stem, fragment):
        problems = lint.check(root)
        prefix = f"{lint.PROFILE_DIR}/{stem}.agent.md: "
        matching = [problem for problem in problems if problem.startswith(prefix)]
        self.assertTrue(matching, f"no violation reported for {stem}: {problems}")
        self.assertTrue(any(fragment in problem for problem in matching), f"{fragment!r} not in {matching}")


class RepositoryProfilesTest(FixtureMixin, unittest.TestCase):
    def test_real_profiles_pass(self):
        self.assertEqual(lint.check(REPO), [])
        self.assertEqual(len(lint.profile_paths(REPO)), 13)

    def test_every_profile_has_name_description_and_list_of_primary_aliases(self):
        for path in lint.profile_paths(REPO):
            fields = lint.parse_frontmatter(path.read_text(encoding="utf-8"))
            self.assertTrue(fields["name"].strip(), path.name)
            self.assertTrue(fields["description"].strip(), path.name)
            self.assertIsInstance(fields["tools"], list, path.name)
            for entry in fields["tools"]:
                self.assertEqual(lint.canonical_tool(entry), entry, path.name)
                self.assertIn(entry, lint.PRIMARY_ALIASES, path.name)

    def test_stated_negative_controls_hold_in_repository(self):
        review_profiles = set(lint.load_review_roles(REPO).values())
        self.assertEqual(len(review_profiles), 11)
        for path in lint.profile_paths(REPO):
            stem = path.name[: -len(".agent.md")]
            tools = lint.parse_frontmatter(path.read_text(encoding="utf-8"))["tools"]
            self.assertNotIn("agent", tools, stem)
            self.assertNotIn("*", tools, stem)
            if stem in review_profiles:
                self.assertNotIn("edit", tools, stem)
        producer = lint.parse_frontmatter(self.profile(REPO, lint.PRODUCER).read_text(encoding="utf-8"))
        self.assertEqual(producer["tools"], ["read", "search"])
        developer = lint.parse_frontmatter(self.profile(REPO, lint.DEVELOPER).read_text(encoding="utf-8"))
        self.assertEqual(developer["tools"], ["read", "search", "edit", "execute"])

    def test_fixture_copy_passes_before_planting(self):
        self.assertEqual(lint.check(self.make_root()), [])

    def test_main_exit_codes(self):
        argv = mock.patch.object(sys, "argv", ["check_agent_profiles.py"])
        stdout, stderr = io.StringIO(), io.StringIO()
        with argv, contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            self.assertEqual(lint.main(), 0)
        self.assertIn("13 profiles", stdout.getvalue())
        root = self.make_root()
        self.replace(root, "pennilogic-qa", '"execute"]', '"execute", "edit"]')
        stdout, stderr = io.StringIO(), io.StringIO()
        with argv, mock.patch.object(lint, "ROOT", root):
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(lint.main(), 1)
        self.assertIn("pennilogic-qa.agent.md: reviewer profiles must not list 'edit'", stderr.getvalue())
        self.assertEqual(stdout.getvalue(), "")


class FrontmatterParserTest(unittest.TestCase):
    def test_flow_and_block_lists_scalars_and_comments(self):
        text = (
            "---\n"
            "name: 'Quoted name'\n"
            "# comment\n"
            "description: Plain text, with commas\n"
            "tools: [\"read\", 'search', github/get_me]\n"
            "extras:\n"
            "  - one\n"
            "  - \"two\"\n"
            "disable-model-invocation: true\n"
            "user-invocable: false\n"
            "---\n"
            "Body with --- inside is not frontmatter.\n"
        )
        fields = lint.parse_frontmatter(text)
        self.assertEqual(fields["name"], "Quoted name")
        self.assertEqual(fields["description"], "Plain text, with commas")
        self.assertEqual(fields["tools"], ["read", "search", "github/get_me"])
        self.assertEqual(fields["extras"], ["one", "two"])
        self.assertIs(fields["disable-model-invocation"], True)
        self.assertIs(fields["user-invocable"], False)

    def test_crlf_and_empty_list(self):
        fields = lint.parse_frontmatter("---\r\nname: A\r\ndescription: B\r\ntools: []\r\n---\r\n")
        self.assertEqual(fields, {"name": "A", "description": "B", "tools": []})

    def test_comma_separated_string_is_a_scalar(self):
        self.assertEqual(lint.parse_frontmatter("---\ntools: read, search\n---\n")["tools"], "read, search")

    def test_null_value_without_items(self):
        self.assertIsNone(lint.parse_frontmatter("---\ntools:\n---\n")["tools"])

    def test_parse_errors(self):
        cases = {
            "no frontmatter": "name: A\n---\n",
            "unclosed": "---\nname: A\n",
            "duplicate": "---\nname: A\nname: B\n---\n",
            "nested mapping": "---\nmcp-servers:\n  custom:\n    type: local\n---\n",
            "flow mapping": "---\ntools: {a: b}\n---\n",
            "unterminated quote": "---\ntools: [\"read]\n---\n",
            "unbalanced quote": "---\nname: \"A\n---\n",
            "empty flow item": "---\ntools: [read, ]\n---\n",
            "empty block item": "---\ntools:\n  -\n---\n",
            "block item without space": "---\ntools:\n-read\n---\n",
            "malformed line": "---\njust words\n---\n",
            "bad key": "---\n1name: A\n---\n",
        }
        for label, text in cases.items():
            with self.assertRaises(lint.ProfileError, msg=label):
                lint.parse_frontmatter(text)


class ToolEntryTest(unittest.TestCase):
    def test_primary_aliases_are_canonical(self):
        for alias in lint.PRIMARY_ALIASES:
            self.assertEqual(lint.canonical_tool(alias), alias)

    def test_read_only_github_tools_are_accepted_by_exact_name(self):
        for name in ("github/issue_read", "github/pull_request_read", "github/get_me", "github/list_issues",
                     "github/search_code", "github/download_workflow_run_artifact"):
            self.assertEqual(lint.canonical_tool(name), name)

    def test_rejected_entries(self):
        cases = {
            "*": "wildcards",
            "github/*": "wildcards",
            "playwright/*": "wildcards",
            "github/create_pull_request": "read-only GitHub MCP",
            "github/issue_write": "read-only GitHub MCP",
            "github/push_files": "read-only GitHub MCP",
            "github/merge_pull_request": "read-only GitHub MCP",
            "github/add_issue_comment": "read-only GitHub MCP",
            "github/mark_all_notifications_read": "read-only GitHub MCP",
            "github/notification_read": "read-only GitHub MCP",
            "github/get_": "read-only GitHub MCP",
            "github/Get_Me": "read-only GitHub MCP",
            "github/list_*": "read-only GitHub MCP",
            "playwright/browser_click": "not a documented alias",
            "custom-mcp/tool-1": "not a documented alias",
            "shell-execute": "not a documented alias",
            "Read": "write the primary alias 'read'",
            "EXECUTE": "write the primary alias 'execute'",
            "Bash": "compatible alias of 'execute'",
            "shell": "compatible alias of 'execute'",
            "powershell": "compatible alias of 'execute'",
            "Write": "compatible alias of 'edit'",
            "Grep": "compatible alias of 'search'",
            "Task": "compatible alias of 'agent'",
            "custom-agent": "compatible alias of 'agent'",
            "TodoWrite": "compatible alias of 'todo'",
            "": "non-empty strings",
            " read": "not a documented alias",
        }
        for entry, fragment in cases.items():
            with self.assertRaises(lint.ProfileError, msg=entry) as caught:
                lint.canonical_tool(entry)
            self.assertIn(fragment, str(caught.exception), entry)
        with self.assertRaises(lint.ProfileError):
            lint.canonical_tool(True)


class PlantedViolationTest(FixtureMixin, unittest.TestCase):
    def test_reviewer_with_edit_fails(self):
        for stem in ("pennilogic-core-reviewer", "pennilogic-qa", "pennilogic-security-reviewer"):
            root = self.make_root()
            self.replace(root, stem, '"execute"]', '"execute", "edit"]')
            self.assertViolation(root, stem, "reviewer profiles must not list 'edit'")

    def test_reviewer_with_compatible_edit_alias_fails(self):
        root = self.make_root()
        self.replace(root, "pennilogic-core-reviewer", '"execute"]', '"execute", "Write"]')
        self.assertViolation(root, "pennilogic-core-reviewer", "compatible alias of 'edit'")

    def test_producer_with_execute_or_edit_fails(self):
        root = self.make_root()
        self.replace(root, lint.PRODUCER, '["read", "search"]', '["read", "search", "execute"]')
        self.assertViolation(root, lint.PRODUCER, "producer profiles must not list 'execute'")
        root = self.make_root()
        self.replace(root, lint.PRODUCER, '["read", "search"]', '["read", "search", "edit"]')
        self.assertViolation(root, lint.PRODUCER, "producer profiles must not list 'edit'")
        root = self.make_root()
        self.replace(root, lint.PRODUCER, '["read", "search"]', '["read", "search", "powershell"]')
        self.assertViolation(root, lint.PRODUCER, "compatible alias of 'execute'")

    def test_agent_alias_fails_in_every_role(self):
        for stem, spelling in ((lint.DEVELOPER, "agent"), (lint.PRODUCER, "Task"),
                               ("pennilogic-money-reviewer", "custom-agent"), ("pennilogic-qa", "AGENT")):
            root = self.make_root()
            self.replace(root, stem, '"read", "search"', f'"read", "search", "{spelling}"')
            problems = lint.check(root)
            self.assertTrue(any(stem in problem and "agent" in problem for problem in problems), problems)

    def test_wildcard_omitted_empty_and_string_tools_fail(self):
        stem = "pennilogic-design-reviewer"
        cases = (
            ('["*"]', "wildcards"),
            ('["read", "*"]', "wildcards"),
            ('["read", "github/*"]', "wildcards"),
            ("[]", "reviewer profiles must list 'read'"),
            ("read, search, execute", "tools must be a YAML list"),
        )
        for tools, fragment in cases:
            root = self.make_root()
            self.write(root, stem, PROFILE.format(tools=tools))
            self.assertViolation(root, stem, fragment)
        root = self.make_root()
        self.write(root, stem, "---\nname: Synthetic\ndescription: Synthetic profile\n---\n\nBody.\n")
        self.assertViolation(root, stem, "tools must be declared")
        root = self.make_root()
        self.write(root, stem, "---\nname: Synthetic\ndescription: Synthetic profile\ntools:\n---\n\nBody.\n")
        self.assertViolation(root, stem, "tools must be a YAML list")

    def test_github_write_tool_fails_even_for_developer(self):
        root = self.make_root()
        self.replace(root, lint.DEVELOPER, '"execute"]', '"execute", "github/create_pull_request"]')
        self.assertViolation(root, lint.DEVELOPER, "read-only GitHub MCP")

    def test_github_read_tool_needs_a_recorded_identity_decision(self):
        root = self.make_root()
        self.replace(root, lint.PRODUCER, '["read", "search"]', '["read", "search", "github/issue_read"]')
        self.assertViolation(root, lint.PRODUCER, "'github/issue_read' without a recorded identity decision")
        for role in lint.ROLE_RULES:
            self.assertEqual(lint.ROLE_RULES[role]["github"], frozenset(), role)
        with mock.patch.dict(lint.ROLE_RULES["producer"], {"github": frozenset({"github/issue_read"})}):
            self.assertEqual(lint.check(root), [])

    def test_unexpected_entries_in_profile_directory_fail(self):
        root = self.make_root()
        (root / lint.PROFILE_DIR / "notes.md").write_text(PROFILE.format(tools='["*"]'), encoding="utf-8")
        (root / lint.PROFILE_DIR / "rogue.yml").write_text("tools: ['*']\n", encoding="utf-8")
        (root / lint.PROFILE_DIR / "nested").mkdir()
        problems = lint.check(root)
        for name in ("notes.md", "rogue.yml", "nested"):
            self.assertIn(f"{lint.PROFILE_DIR}/{name}: unexpected entry", "".join(problems))
        self.assertEqual(len(problems), 3)

    def test_unknown_tool_name_fails_instead_of_being_ignored(self):
        root = self.make_root()
        self.replace(root, lint.DEVELOPER, '"execute"]', '"execute", "publish_pull_request"]')
        self.assertViolation(root, lint.DEVELOPER, "not a documented alias")

    def test_duplicate_tool_fails(self):
        root = self.make_root()
        self.replace(root, lint.DEVELOPER, '"execute"]', '"execute", "read"]')
        self.assertViolation(root, lint.DEVELOPER, "listed twice")

    def test_developer_requires_edit_and_execute(self):
        root = self.make_root()
        self.replace(root, lint.DEVELOPER, '["read", "search", "edit", "execute"]', '["read", "search"]')
        self.assertViolation(root, lint.DEVELOPER, "developer profiles must list 'edit'")
        self.assertViolation(root, lint.DEVELOPER, "developer profiles must list 'execute'")

    def test_missing_name_or_description_fails(self):
        root = self.make_root()
        self.write(root, lint.PRODUCER, '---\ndescription: D\ntools: ["read", "search"]\n---\n')
        self.assertViolation(root, lint.PRODUCER, "name must be a non-empty string")
        root = self.make_root()
        self.write(root, lint.PRODUCER, '---\nname: N\ndescription: ""\ntools: ["read", "search"]\n---\n')
        self.assertViolation(root, lint.PRODUCER, "description must be a non-empty string")

    def test_mcp_servers_key_fails(self):
        root = self.make_root()
        flat = '---\nname: N\ndescription: D\ntools: ["read", "search"]\nmcp-servers: none\n---\n'
        self.write(root, lint.PRODUCER, flat)
        self.assertViolation(root, lint.PRODUCER, "mcp-servers must not be declared")
        root = self.make_root()
        nested = (
            '---\nname: N\ndescription: D\ntools: ["read"]\n'
            "mcp-servers:\n  github:\n    type: local\n---\n"
        )
        self.write(root, lint.PRODUCER, nested)
        self.assertViolation(root, lint.PRODUCER, "unsupported nested frontmatter")

    def test_broken_frontmatter_fails(self):
        root = self.make_root()
        self.write(root, lint.PRODUCER, 'name: N\ndescription: D\ntools: ["read", "search"]\n')
        self.assertViolation(root, lint.PRODUCER, "frontmatter must start with '---'")

    def test_unclassified_profile_fails(self):
        root = self.make_root()
        self.write(root, "pennilogic-intern", PROFILE.format(tools='["read"]'))
        self.assertViolation(root, "pennilogic-intern", "unclassified profile")

    def test_review_role_without_profile_fails(self):
        root = self.make_root()
        self.profile(root, "pennilogic-release-reviewer").unlink()
        problems = lint.check(root)
        expected = "review role 'release' names a missing profile 'pennilogic-release-reviewer'"
        self.assertIn(f"{lint.POLICY_FILE}: {expected}", problems)

    def test_empty_profile_directory_fails(self):
        root = self.make_root()
        for path in lint.profile_paths(root):
            path.unlink()
        self.assertIn(f"{lint.PROFILE_DIR}: no *.agent.md profiles found", lint.check(root))

    def test_invalid_policy_is_an_error(self):
        root = self.make_root()
        (root / lint.POLICY_FILE).write_text("{}", encoding="utf-8")
        with self.assertRaises(lint.ProfileError):
            lint.check(root)
        (root / lint.POLICY_FILE).write_text("not json", encoding="utf-8")
        with self.assertRaises(lint.ProfileError):
            lint.check(root)
        (root / lint.POLICY_FILE).unlink()
        with self.assertRaises(lint.ProfileError):
            lint.check(root)


if __name__ == "__main__":
    unittest.main()
