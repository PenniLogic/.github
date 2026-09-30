"""Check shared agent profile allowlists against documented tool aliases and role policy."""

import argparse
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
PROFILE_DIR = "agents"
POLICY_FILE = ".github/agent-policy.json"
REFERENCE = "https://docs.github.com/en/copilot/reference/custom-agents-configuration#tool-aliases"

# Primary aliases from the reference table. Compatible aliases are case insensitive and
# select the same tool, so they are canonicalized before role rules apply; profiles must
# write the primary alias so that the allowlist reads the same to people and the platform.
PRIMARY_ALIASES = ("execute", "read", "edit", "search", "agent", "web", "todo")
COMPATIBLE_ALIASES = {
    "shell": "execute", "bash": "execute", "powershell": "execute",
    "notebookread": "read",
    "multiedit": "edit", "write": "edit", "notebookedit": "edit",
    "grep": "search", "glob": "search",
    "custom-agent": "agent", "task": "agent",
    "websearch": "web", "webfetch": "web",
    "todowrite": "todo",
}
# The out-of-the-box GitHub MCP server may be referenced only by exact read-only tool
# names, and only when a role's rules permit that name: the cloud agent's server token is
# read-only and the app's MCP identity must not gain repository tools through a profile.
GITHUB_READ_TOOL = re.compile(
    r"github/(?:(?:get|list|search|download)_[a-z0-9_]+|(?:issue|pull_request)_read)"
)
FRONTMATTER_KEY = re.compile(r"[A-Za-z][A-Za-z0-9_-]*")

DEVELOPER = "pennilogic-developer"
PRODUCER = "pennilogic-producer"
# Review roles come from agent-policy.json; every other profile must be one of these two.
# "tools" pins each role's allowlist exactly: any other alias fails for that role.
# "github" lists the github/<tool> names a role may declare; empty by the decision recorded
# on issue #1 (2026-09-30). Extending either set is a reviewed lint change, not a profile edit.
ROLE_RULES = {
    "developer": {"tools": frozenset({"read", "search", "edit", "execute"}), "github": frozenset()},
    "producer": {"tools": frozenset({"read", "search"}), "github": frozenset()},
    "reviewer": {"tools": frozenset({"read", "search", "execute"}), "github": frozenset()},
}
# Profile-level MCP servers could shadow the GitHub server or add undeclared tools.
FORBIDDEN_KEYS = ("mcp-servers",)
# The profile directory holds only profiles and its README; anything else is unreviewed input.
ALLOWED_EXTRA_FILES = ("README.md",)


class ProfileError(ValueError):
    """A profile cannot be checked or violates the stated policy."""


def unquote(value, line_number):
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    if value[:1] in ('"', "'") or value[-1:] in ('"', "'"):
        raise ProfileError(f"unbalanced quotes at line {line_number}")
    return value


def parse_flow_list(value, line_number):
    inner = value[1:-1].strip()
    if not inner:
        return []
    items, current, quote = [], [], None
    for char in inner:
        if quote:
            current.append(char)
            if char == quote:
                quote = None
        elif char in "\"'":
            quote = char
            current.append(char)
        elif char == ",":
            items.append("".join(current))
            current = []
        else:
            current.append(char)
    if quote:
        raise ProfileError(f"unterminated quote at line {line_number}")
    items.append("".join(current))
    result = []
    for item in items:
        item = item.strip()
        if not item:
            raise ProfileError(f"empty list item at line {line_number}")
        result.append(unquote(item, line_number))
    return result


def parse_scalar(value, line_number):
    if value in ("true", "false"):
        return value == "true"
    return unquote(value, line_number)


def parse_frontmatter(text):
    """Parse the flat YAML frontmatter used by agent profiles without a YAML dependency."""
    lines = [line.rstrip("\r") for line in text.split("\n")]
    if not lines or lines[0] != "---":
        raise ProfileError("frontmatter must start with '---' on line 1")
    try:
        end = lines.index("---", 1)
    except ValueError:
        raise ProfileError("frontmatter is not closed by '---'") from None
    fields = {}
    index = 1
    while index < end:
        line = lines[index]
        index += 1
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0] in " \t":
            raise ProfileError(f"unsupported nested frontmatter at line {index}")
        key, separator, value = line.partition(":")
        key = key.strip()
        if not separator or not FRONTMATTER_KEY.fullmatch(key):
            raise ProfileError(f"malformed frontmatter at line {index}")
        if key in fields:
            raise ProfileError(f"duplicate frontmatter key {key!r}")
        value = value.strip()
        if value == "":
            items = None
            while index < end and (lines[index].strip() == "-" or lines[index].strip().startswith("- ")):
                item = lines[index].strip()[1:].strip()
                index += 1
                if not item:
                    raise ProfileError(f"empty list item at line {index}")
                items = (items or []) + [unquote(item, index)]
            fields[key] = items
        elif value.startswith("[") and value.endswith("]"):
            fields[key] = parse_flow_list(value, index)
        elif value.startswith(("[", "{")):
            raise ProfileError(f"unsupported frontmatter value at line {index}")
        else:
            fields[key] = parse_scalar(value, index)
    return fields


def canonical_tool(entry):
    """Return the canonical name of a documented tool entry or raise ProfileError."""
    if not isinstance(entry, str) or not entry.strip():
        raise ProfileError("tools entries must be non-empty strings")
    if entry in PRIMARY_ALIASES:
        return entry
    lowered = entry.lower()
    if lowered in PRIMARY_ALIASES:
        raise ProfileError(f"tool {entry!r}: write the primary alias {lowered!r}")
    if lowered in COMPATIBLE_ALIASES:
        primary = COMPATIBLE_ALIASES[lowered]
        raise ProfileError(f"tool {entry!r}: compatible alias of {primary!r}; write the primary alias")
    if entry == "*" or entry.endswith("/*"):
        raise ProfileError(f"tool {entry!r}: wildcards enable undeclared tools")
    if entry.startswith("github/"):
        if GITHUB_READ_TOOL.fullmatch(entry):
            return entry
        raise ProfileError(
            f"tool {entry!r}: only read-only GitHub MCP tool names "
            "(github/get_*, list_*, search_*, download_*, issue_read, pull_request_read) may be listed"
        )
    raise ProfileError(f"tool {entry!r}: not a documented alias or github/<tool>; see {REFERENCE}")


def check_profile(path, role):
    problems = []
    fields = parse_frontmatter(path.read_text(encoding="utf-8"))
    for key in ("name", "description"):
        value = fields.get(key)
        if not isinstance(value, str) or not value.strip():
            problems.append(f"{key} must be a non-empty string")
    for key in FORBIDDEN_KEYS:
        if key in fields:
            problems.append(f"{key} must not be declared in a shared profile")
    if "tools" not in fields:
        problems.append("tools must be declared; an omitted list enables every tool")
        return problems
    tools = fields["tools"]
    if not isinstance(tools, list):
        problems.append("tools must be a YAML list of tool names")
        return problems
    present = []
    for entry in tools:
        try:
            name = canonical_tool(entry)
        except ProfileError as error:
            problems.append(str(error))
            continue
        if name in present:
            problems.append(f"tool {entry!r} is listed twice")
        present.append(name)
    rules = ROLE_RULES[role]
    aliases = {tool for tool in present if not tool.startswith("github/")}
    for tool in sorted(aliases - rules["tools"]):
        problems.append(f"{role} profiles must not list {tool!r}")
    for tool in sorted(rules["tools"] - aliases):
        problems.append(f"{role} profiles must list {tool!r}")
    for tool in present:
        if tool.startswith("github/") and tool not in rules["github"]:
            problems.append(f"{role} profiles must not list {tool!r} without a recorded identity decision")
    return problems


def load_review_roles(root):
    try:
        policy = json.loads((root / POLICY_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ProfileError(f"{POLICY_FILE}: cannot read review roles ({type(error).__name__})") from None
    roles = policy.get("review_roles") if isinstance(policy, dict) else None
    if not isinstance(roles, dict) or not roles:
        raise ProfileError(f"{POLICY_FILE}: review_roles must map role names to profile names")
    if not all(isinstance(profile, str) and profile for profile in roles.values()):
        raise ProfileError(f"{POLICY_FILE}: review_roles must map role names to profile names")
    return roles


def profile_paths(root):
    return sorted((root / PROFILE_DIR).glob("*.agent.md"))


def classify(stem, review_profiles):
    if stem == DEVELOPER:
        return "developer"
    if stem == PRODUCER:
        return "producer"
    if stem in review_profiles:
        return "reviewer"
    return None


def check(root):
    problems = []
    review_roles = load_review_roles(root)
    paths = profile_paths(root)
    if not paths:
        problems.append(f"{PROFILE_DIR}: no *.agent.md profiles found")
    for entry in sorted((root / PROFILE_DIR).glob("*")):
        if entry not in paths and entry.name not in ALLOWED_EXTRA_FILES:
            problems.append(
                f"{PROFILE_DIR}/{entry.name}: unexpected entry; "
                "only *.agent.md profiles and README.md belong here"
            )
    stems = {path.name[: -len(".agent.md")] for path in paths}
    for role, profile in sorted(review_roles.items()):
        if profile not in stems:
            problems.append(f"{POLICY_FILE}: review role {role!r} names a missing profile {profile!r}")
    for path in paths:
        label = f"{PROFILE_DIR}/{path.name}"
        role = classify(path.name[: -len(".agent.md")], set(review_roles.values()))
        if role is None:
            problems.append(
                f"{label}: unclassified profile; only {DEVELOPER}, {PRODUCER} and the "
                f"review_roles in {POLICY_FILE} are allowed"
            )
            continue
        try:
            problems.extend(f"{label}: {problem}" for problem in check_profile(path, role))
        except (OSError, UnicodeDecodeError, ProfileError) as error:
            problems.append(f"{label}: {error}")
    return problems


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        problems = check(ROOT)
    except ProfileError as error:
        print(f"Agent profile check failed: {error}", file=sys.stderr)
        return 1
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    count = len(profile_paths(ROOT))
    print(f"Agent profile checks passed ({count} profiles); allowlists match documented aliases and policy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
