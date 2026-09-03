"""The CTO's verdict, relayed as a comment.

The CTO (ChatGPT, the Mac reviewer running Claude with the CTO persona, or
JM herself) speaks to the pipeline through one comment shape. On a PULL
REQUEST the first line decides the review:

    CTO: APPROVE
    CTO: REWORK [skill=Build|Debug|QA]
    <what must change, in the CTO's words>
    CTO: BLOCK <one-line reason>

On a CONTROL ISSUE it decides whether a feature may run at all -- the
approval gate for a new (Proposed) feature and the triage of a Blocked one:

    CTO: GO [skill=Build|Debug|QA|Research|Data] [agent=Claude]
    <guidance for the worker, in the CTO's words>
    CTO: BLOCK <one-line reason>          (needs a JM decision; stays Blocked)

This module is pure: it turns the comment plus the current control issue
into the status/label/body updates the workflow applies. Only a comment
from someone with write access counts; anyone else's ``CTO:`` line is
ignored, not obeyed.
"""

from __future__ import annotations

import re
from typing import Any

from .mission import extract_section
from .status import (
    AGENTS,
    AGENT_LABELS,
    SKILLS,
    SKILL_LABELS,
    STATES,
    TRIAGE_LABEL,
    canonical,
    parse_status,
)

VERDICT_RE = re.compile(r"^[ \t]*CTO:[ \t]*(?P<verdict>APPROVE|REWORK|BLOCK|GO)\b(?P<rest>[^\n]*)", re.I)
_SKILL_ARG_RE = re.compile(r"skill\s*=\s*(?P<skill>[A-Za-z]+)", re.I)
_AGENT_ARG_RE = re.compile(r"agent\s*=\s*(?P<agent>[A-Za-z]+)", re.I)

REWORK_HEADING = "Rework"
GUIDANCE_HEADING = "CTO guidance"
APPROVED_LABEL = "cto:approved"
#: On the PR while a CTO verdict is awaited; the Mac review script polls for it.
REVIEW_LABEL = "cto:review"
TRUSTED_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}


def parse_verdict(comment: str) -> dict[str, str] | None:
    """The decision in a comment, or ``None`` when it carries none."""
    match = VERDICT_RE.search(comment or "")
    if match is None:
        return None
    verdict = match.group("verdict").upper()
    rest = (match.group("rest") or "").strip()
    notes = (comment[match.end():] or "").strip()
    skill = agent = ""
    arg = _SKILL_ARG_RE.search(rest)
    if arg:
        skill = canonical(arg.group("skill"), SKILLS) or ""
        rest = _SKILL_ARG_RE.sub("", rest).strip()
    arg = _AGENT_ARG_RE.search(rest)
    if arg:
        agent = canonical(arg.group("agent"), AGENTS) or ""
        rest = _AGENT_ARG_RE.sub("", rest).strip()
    return {"verdict": verdict, "skill": skill, "agent": agent, "reason": rest, "notes": notes}


def upsert_section(body: str, heading: str, text: str) -> str:
    """Replace the ``## <heading>`` section's text, or append the section."""
    pattern = re.compile(rf"^##[ \t]+{re.escape(heading)}[ \t]*$", re.M | re.I)
    match = pattern.search(body or "")
    block = f"## {heading}\n\n{text.strip()}\n"
    if match is None:
        return (body or "").rstrip() + "\n\n" + block
    following = re.compile(r"^##[ \t]+", re.M).search(body, match.end())
    end = following.start() if following else len(body)
    return body[: match.start()] + block + ("\n" if following else "") + body[end:]


def cto_verdict(payload: dict[str, Any]) -> dict[str, Any]:
    """Apply a CTO verdict comment to the control issue.

    payload: ``issue`` (number, body, labels), ``pull`` (number),
    ``comment`` (body, author_association), ``now``.
    """
    issue = payload.get("issue") or {}
    pull = payload.get("pull") or {}
    comment = payload.get("comment") or {}
    body = issue.get("body") or ""
    labels = [str(name) for name in issue.get("labels") or []]
    number = int(pull.get("number") or 0)

    def skip(reason: str) -> dict[str, Any]:
        return {"action": "skip", "reason": reason}

    association = str(comment.get("author_association") or "").upper()
    if association not in TRUSTED_ASSOCIATIONS:
        return skip(f"comment author association {association or 'unknown'} is not trusted to relay a CTO verdict")

    decision = parse_verdict(comment.get("body") or "")
    if decision is None:
        return skip("comment carries no `CTO:` verdict")
    if decision["verdict"] == "GO":
        return skip("`CTO: GO` belongs on the control issue, not on a pull request")

    status = parse_status(body)
    if status is None:
        return skip("linked issue has no status block; not a feature control issue")
    state = canonical(status["State"], STATES)
    reference = f"#{number}" if number else (status.get("PR") or "").strip()

    if decision["verdict"] == "APPROVE":
        if state == "Done":
            return skip("feature is already Done")
        return {
            "action": "approve",
            "reason": f"CTO approved {reference}; waiting for the owner to merge",
            "status_updates": {"State": "Review", "Blocker": "None", "Next": "JM merge"},
            "pr_labels_add": [APPROVED_LABEL],
            "pr_labels_remove": [REVIEW_LABEL],
            "reply": f"✅ CTO approved. Next: JM merges {reference}. The control issue moves to Done on merge.",
        }

    if decision["verdict"] == "BLOCK":
        reason = decision["reason"] or "CTO blocked; see the PR comment"
        return {
            "action": "block",
            "reason": f"CTO blocked {reference}",
            "status_updates": {"State": "Blocked", "Blocker": reason[:120], "Next": "JM decision"},
            "pr_labels_remove": [REVIEW_LABEL],
            "reply": f"⛔ CTO blocked this feature: {reason}",
        }

    # REWORK: the notes become the `## Rework` section of the control issue
    # so the next worker receives them inside its mission, then the same
    # feature goes back to Ready with the skill the CTO named (Build unless
    # told otherwise). Labels follow the status block, never the reverse.
    skill = decision["skill"] or "Build"
    if skill in ("Unassigned", "Review", "Research", "Data"):
        skill = "Build"
    notes = decision["notes"] or decision["reason"] or "Address the CTO review comments on the PR."
    previous = extract_section(body, REWORK_HEADING)
    round_no = 1 + len(re.findall(r"^### Round \d+", previous, re.M)) if previous else 1
    text = f"### Round {round_no} — on {reference}\n\n{notes}"
    if previous:
        text = previous.rstrip() + "\n\n" + text
    new_body = upsert_section(body, REWORK_HEADING, text)
    wanted = SKILL_LABELS[skill]
    new_labels = [name for name in labels if not name.casefold().startswith("skill:")] + [wanted]
    return {
        "action": "rework",
        "reason": f"CTO requested rework of {reference} via {skill}",
        "status_updates": {"State": "Ready", "Skill": skill, "Blocker": "None", "Next": "Worker executing"},
        "body": new_body,
        "labels": new_labels,
        "pr_labels_remove": [APPROVED_LABEL, REVIEW_LABEL],
        "reply": f"🔁 Rework round {round_no} recorded on the control issue ({skill}). The worker will pick it up.",
    }


def issue_verdict(payload: dict[str, Any]) -> dict[str, Any]:
    """Apply a CTO verdict comment posted on the control issue itself.

    ``GO`` (``APPROVE`` and ``REWORK`` are accepted as synonyms here) sets the
    feature Ready with the named or already-recorded agent and skill, records
    the CTO's guidance in a ``## CTO guidance`` section the worker receives in
    its mission, and clears the triage label. ``BLOCK`` keeps the feature
    Blocked with the reason and hands it to JM. Every path removes
    ``cto:triage`` so the Mac reviewer does not answer twice.

    payload: ``issue`` (number, body, labels), ``comment`` (body,
    author_association), ``now``.
    """
    issue = payload.get("issue") or {}
    comment = payload.get("comment") or {}
    body = issue.get("body") or ""
    labels = [str(name) for name in issue.get("labels") or []]
    now = str(payload.get("now") or "").strip()

    def skip(reason: str) -> dict[str, Any]:
        return {"action": "skip", "reason": reason}

    association = str(comment.get("author_association") or "").upper()
    if association not in TRUSTED_ASSOCIATIONS:
        return skip(f"comment author association {association or 'unknown'} is not trusted to relay a CTO verdict")

    decision = parse_verdict(comment.get("body") or "")
    if decision is None:
        return skip("comment carries no `CTO:` verdict")

    status = parse_status(body)
    if status is None:
        return skip("issue has no status block; not a feature control issue")
    state = canonical(status["State"], STATES)
    if state in ("In Progress",):
        return skip("a worker is executing; wait for its hand-back or the reconcile step")

    without_triage = [name for name in labels if name.casefold() != TRIAGE_LABEL]

    if decision["verdict"] == "BLOCK":
        reason = decision["reason"] or "CTO blocked; see the issue comment"
        return {
            "action": "block",
            "reason": "CTO blocked the feature pending a JM decision",
            "status_updates": {"State": "Blocked", "Blocker": reason[:120], "Next": "JM decision"},
            "labels": without_triage,
            "reply": f"⛔ CTO: needs your decision, JM -- {reason}",
        }

    if state == "Done":
        return skip("feature is already Done")

    skill = decision["skill"] or canonical(status.get("Skill") or "", SKILLS) or "Build"
    if skill in ("Unassigned", "Review"):
        skill = "Build"
    agent = decision["agent"] or canonical(status.get("Agent") or "", AGENTS) or "Claude"
    if agent == "Unassigned":
        agent = "Claude"

    notes = decision["notes"] or decision["reason"]
    new_body = body
    if notes:
        previous = extract_section(body, GUIDANCE_HEADING)
        stamp = f" ({now[:10]})" if now else ""
        text = f"### CTO{stamp}\n\n{notes}"
        if previous:
            text = previous.rstrip() + "\n\n" + text
        new_body = upsert_section(body, GUIDANCE_HEADING, text)

    new_labels = [
        name for name in without_triage
        if not name.casefold().startswith(("skill:", "agent:"))
    ] + [AGENT_LABELS[agent], SKILL_LABELS[skill]]
    return {
        "action": "go",
        "reason": f"CTO cleared the feature for {agent} / {skill}",
        "status_updates": {
            "State": "Ready",
            "Agent": agent,
            "Skill": skill,
            "Blocker": "None",
            "Next": "Worker executing",
        },
        "body": new_body,
        "labels": new_labels,
        "reply": f"▶️ CTO go: {agent} / {skill}. Dispatching.",
    }
