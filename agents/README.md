# Shared agent profiles

The `*.agent.md` files in this directory are the PenniLogic custom agent profiles: one
Developer, one Producer and the eleven review roles named in `.github/agent-policy.json`
(`review_roles`, including QA). Each profile's YAML `tools` list is its allowlist; a specific
list enables only the listed tools. Each profile declares the smallest documented set that
covers its public duties, and no profile text promises an action its allowlist cannot perform.

## Tool alias reference

Source: GitHub Docs, *Custom agents configuration*, sections "Tools" and "Tool aliases":
<https://docs.github.com/en/copilot/reference/custom-agents-configuration#tool-aliases>
(checked 2026-09-30).

| Primary alias | Compatible aliases (case insensitive) | Purpose |
| --- | --- | --- |
| `execute` | `shell`, `Bash`, `powershell` | Run a shell command |
| `read` | `Read`, `NotebookRead` | Read file contents |
| `edit` | `Edit`, `MultiEdit`, `Write`, `NotebookEdit` | Edit files |
| `search` | `Grep`, `Glob` | Search files or text |
| `agent` | `custom-agent`, `Task` | Invoke another custom agent |
| `web` | `WebSearch`, `WebFetch` | Fetch URLs / web search (not in cloud agent) |
| `todo` | `TodoWrite` | Structured task lists (not in cloud agent) |

MCP server tools are referenced as `<server>/<tool>`; the out-of-the-box `github` server exposes
read-only tools with a token scoped to the source repository. Omitting `tools` or writing
`tools: ["*"]` enables every tool; `tools: []` disables all of them. Unrecognized names are
ignored silently, so a misspelled or undocumented entry changes nothing at runtime while the
profile text appears to promise it. No documented alias exists for app-native pull-request
creation or session messaging.

## Stated policy

- Every profile has a non-empty `name` and `description` and an explicit `tools` list written
  with primary aliases only (compatible aliases and case variants are rejected so that the
  allowlist reads the same to people and to the platform).
- No profile lists `agent`. Roles are separated by session, never by delegation.
- No review-role profile lists `edit`; reviewers and QA stay read-only.
- The Producer lists neither `edit` nor `execute`.
- The Developer lists `read`, `search`, `edit` and `execute`. There is one GitHub user and
  multiple independent AI sessions (`PenniLogic/docs/governance/DELIVERY.md`); pull-request
  and issue operations run through `execute` with the owner's `gh` credentials in the
  session's own process, and no app-native publication or messaging tool is used.
- Nobody uses `["*"]`, omits `tools`, or uses a server wildcard such as `github/*`.
- `github/<tool>` may name only read-only GitHub MCP tools (`get_*`, `list_*`, `search_*`,
  `download_*`, `issue_read`, `pull_request_read`), and only when the role's rules in
  `scripts/check_agent_profiles.py` permit that exact name. Every role's permitted set is
  empty: the app's MCP identity is the corporate account and is not coupled to repository
  reads or writes. Extending a set is an identity decision recorded on an issue and reviewed
  as a lint change, not a profile edit.
- Profiles do not declare `mcp-servers`; MCP servers are repository or organization settings.
- Every `*.agent.md` file is the Developer, the Producer or a `review_roles` profile; an
  unclassified profile fails the check, and so does any other file in this directory apart
  from this note.

Current allowlists:

| Profile | `tools` |
| --- | --- |
| `pennilogic-developer` | `read`, `search`, `edit`, `execute` |
| `pennilogic-producer` | `read`, `search` |
| `pennilogic-core-reviewer` | `read`, `search`, `execute` |
| `pennilogic-qa` | `read`, `search`, `execute` |
| `pennilogic-security-reviewer` | `read`, `search`, `execute` |
| `pennilogic-privacy-reviewer` | `read`, `search`, `execute` |
| `pennilogic-money-reviewer` | `read`, `search`, `execute` |
| `pennilogic-contract-reviewer` | `read`, `search`, `execute` |
| `pennilogic-compliance-reviewer` | `read`, `search`, `execute` |
| `pennilogic-accessibility-reviewer` | `read`, `search`, `execute` |
| `pennilogic-design-reviewer` | `read`, `search`, `execute` |
| `pennilogic-reliability-reviewer` | `read`, `search`, `execute` |
| `pennilogic-release-reviewer` | `read`, `search`, `execute` |

`scripts/check_agent_profiles.py` pins these lists exactly per role: any other alias, including
`web` and `todo`, fails for that role.

Decision record (issue #1, 2026-09-30): the Producer keeps `["read", "search"]`; its duty reads
the issue text supplied by the coordinator and the preserved backlog entry in the docs checkout
(`planning/backlog.json`) rather than fetching the issue itself (option a). Option b,
`github/issue_read`, was not taken because of the identity coupling above.

## Check

```text
python scripts/check_agent_profiles.py
python -m unittest discover -s scripts/tests
```

`scripts/check_agent_profiles.py` (standard library only) enforces the mechanical rules above
and exits 1 with one line per violation. `scripts/tests/test_agent_profiles.py` asserts that the
committed profiles pass and that planted violations fail. The generated CI workflow runs
`python scripts/check_repository.py` only; these two commands run locally until the Infra owner
adds them to the `.github` profile in `PenniLogic/infra/governance/repository-profiles.json`.
Agreement between duty text and allowlist is reviewed, not linted.
