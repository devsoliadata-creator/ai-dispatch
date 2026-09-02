"""Build the compact mission a dispatched worker receives.

The worker gets the control issue's own words -- outcome, scope, acceptance
criteria, durable decisions -- and a pointer to the authoritative skill
file. It does not get the repository's history, the issue's comment thread,
or a copy of the skill instructions pasted into workflow YAML.
"""

from __future__ import annotations

import re

from .status import skill_file

#: Per-section budget. Long evidence dumps belong in the issue, not in every
#: dispatch prompt; a truncated section says so instead of ending mid-word.
SECTION_LIMIT = 4000

TRUNCATION_NOTE = "\n\n[truncated -- read the full section in the control issue]"

NO_VERIFY = "the repository's own tests/lint, if any (no verify command is configured)"

EXECUTION_RULES = """- Read AGENTS.md and CLAUDE.md (whichever exist) before changing anything.
- Load the assigned skill file above; it is authoritative for how to work.
- Preserve the recorded scope. Do not silently alter architecture or widen
  the mission; use the architecture-challenge format in AI_WORKFLOW.md and
  wait for a ChatGPT CTO decision.
- Work in THIS checkout on a new `claude/<slug>` branch (`git checkout -b`).
  The runner is already isolated: do not create worktrees, do not `cd`
  elsewhere, run every command from the repository root -- the tool
  allow-list matches command prefixes and a leading `cd ... &&` is denied.
- Dependencies are already installed by the workflow. Never install packages;
  if the verify command fails on a missing module, report it as a blocker.
- Tools you have: file edits, any `git` command except force-push / push to
  the default branch / remote or global-config changes, `gh pr`/`gh issue`
  read-and-comment plus `gh pr create|edit`, the verify command, pytest /
  unittest, and the usual read tools (find, grep, sed -n, awk, diff, cat).
  Redirect output only to files inside the repository, never to /tmp. You do
  NOT have: pip/npm/curl/wget, python3 -c, bash -c, sed -i (use the Edit
  tool), gh merge/secret/workflow, or edits under .github/workflows. A denied
  command will stay denied: do not retry it in another form -- work around it
  with an allowed tool or report the blocker.
- Run `{verify}` before opening or updating the PR.
- Do not loop. Work the phases in order once. If the same fix fails twice, or
  the verify command is still red after two repair attempts, stop: hand back
  with `python3 -m scripts.dispatch complete` is NOT allowed in that case --
  instead comment the exact evidence on the control issue and exit, so the
  feature is blocked truthfully rather than retried blindly.
- Take no consequential external action (merge, deploy, publish, credential
  change, purchase, booking, cancellation) without the authorization that
  action requires.
- You are an execution worker. This dispatch records an assignment ChatGPT
  CTO already made; it does not grant you CTO authority, and loading
  $cto-ops does not either. Do not reassign the agent or skill, re-scope the
  mission, or approve your own work."""

RETURN_CONTRACT = """- outcome
- changed files
- tests/checks run and their results
- evidence
- risks
- PR or commit SHA
- architecture challenge, if you hit one"""


def extract_section(body: str, heading: str) -> str:
    """Return the body of a ``## <heading>`` section, or an empty string."""
    pattern = re.compile(
        rf"^##[ \t]+{re.escape(heading)}[ \t]*$", re.M | re.I
    )
    match = pattern.search(body or "")
    if match is None:
        return ""
    start = match.end()
    following = re.compile(r"^##[ \t]+", re.M).search(body, start)
    end = following.start() if following else len(body)
    return body[start:end].strip()


def _bounded(text: str) -> str:
    if len(text) <= SECTION_LIMIT:
        return text
    return text[:SECTION_LIMIT].rstrip() + TRUNCATION_NOTE


def build_mission(
    *,
    issue_number: int,
    title: str,
    body: str,
    agent: str,
    skill: str,
    status: dict[str, str],
    repository: str = "",
    routing: dict[str, str] | None = None,
    verify: str = "",
) -> str:
    """Render the dispatch prompt for one worker.

    Everything here is derived from the control issue. No credential, secret
    name, or environment value is interpolated: a dispatch prompt is posted
    to a workflow log and read by a worker, so it carries only what the
    mission needs.
    """
    sections = {
        "REWORK (CTO review findings -- address these first)": extract_section(body, "Rework"),
        "OUTCOME": extract_section(body, "Outcome"),
        "SCOPE": extract_section(body, "Scope"),
        "ACCEPTANCE": extract_section(body, "Acceptance criteria"),
        "DURABLE DECISIONS": extract_section(body, "Decisions and context"),
    }
    linked_pr = (status.get("PR") or "None").strip() or "None"
    blocker = (status.get("Blocker") or "None").strip() or "None"
    path = (routing or {}).get("skill_file") or skill_file(skill) or "(unmapped skill -- ask ChatGPT CTO)"

    lines = [
        "FEATURE",
        title.strip() or f"Issue #{issue_number}",
        "",
        "CONTROL ISSUE",
        f"#{issue_number}" + (f" in {repository}" if repository else ""),
        "",
        "AGENT",
        agent,
        "",
        "SKILL",
        f"{skill} -- {path}",
        "",
    ]
    if routing:
        lines += [
            "WORKER",
            f"Claude {routing.get('model', '')} at {routing.get('effort', '')} effort, "
            f"{routing.get('access', '')} access, as recorded in the skill file above",
            "",
        ]
    for name, text in sections.items():
        if name.startswith("REWORK") and not text:
            continue
        lines += [name, _bounded(text) if text else "(not recorded in the control issue)", ""]
    lines += [
        "CURRENT BLOCKER",
        blocker,
        "",
        "LINKED PR",
        linked_pr,
        "",
        "EXECUTION RULES",
        EXECUTION_RULES.replace("{verify}", verify or NO_VERIFY),
        "",
        "RETURN",
        RETURN_CONTRACT,
        "",
        "HANDING BACK",
        "When implementation is ready, open or update the single "
        f"implementation PR and reference `Control issue: #{issue_number}` in "
        "its body. Then hand the mission back explicitly:",
        "",
        f"    python3 -m scripts.dispatch complete --issue {issue_number} --pull <pr number>",
        "",
        "That sets State: Review and Next: CTO review and releases the "
        "dispatch claim. The workflow environment already supplies the GitHub "
        "credentials it needs; if the command is not available to you, edit "
        "the control issue's status block to those same two values by hand. "
        "Nothing else marks the work finished -- a push to an "
        "already-open pull request is not a completion signal. Do not set "
        "State: Done; that is ChatGPT CTO's decision. Then stop.",
    ]
    return "\n".join(lines).strip() + "\n"
