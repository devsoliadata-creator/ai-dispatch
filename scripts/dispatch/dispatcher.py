"""The dispatch decision: may this feature be handed to a worker, and how.

This module is pure. It takes a snapshot of GitHub state and returns what
should happen; the workflow performs the writes. That split is what makes
every rule below testable without a GitHub event, and it keeps the rules in
one file rather than spread across YAML.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .mission import build_mission
from .status import (
    AGENTS,
    LEGACY_LABELS,
    SKILLS,
    STATES,
    canonical,
    parse_status,
    routing_labels,
    skill_file,
)

#: HTML comment marker for the single durable dispatch record. One record
#: comment per control issue, updated in place -- a growing pile of bot
#: comments is exactly the noise Julia should never have to read past.
DISPATCH_MARKER = "<!-- pa-dispatch:v1"
_RECORD_RE = re.compile(
    re.escape(DISPATCH_MARKER) + r"\s*(?P<json>\{.*?\})\s*-->", re.S
)

LANE_AUTOMATIC = "automatic"
LANE_MANUAL = "manual"

#: A record in this status holds an active claim: the worker was invoked and
#: has not been released. Every other status is spent and permits a new
#: dispatch of the same assignment.
ACTIVE_STATUS = "dispatched"

_TERMINAL_STATES = {"Review", "Blocked", "Done"}


def parse_record(comment_body: str) -> dict[str, Any] | None:
    """Read the machine-readable record out of the dispatch comment."""
    match = _RECORD_RE.search(comment_body or "")
    if match is None:
        return None
    try:
        record = json.loads(match.group("json"))
    except json.JSONDecodeError:
        # A corrupted record must not be read as "no dispatch has happened",
        # which would permit a duplicate worker. Report it as an unknown
        # active claim instead and let a human clear it.
        return {"key": "", "status": ACTIVE_STATUS, "corrupt": True}
    return record if isinstance(record, dict) else None


def render_record(record: dict[str, Any]) -> str:
    """Render the dispatch comment: one human line, one machine payload."""
    status = record.get("status", "")
    agent = record.get("agent") or "Unassigned"
    skill = record.get("skill") or "Unassigned"
    headline = {
        "dispatched": f"Dispatched to {agent} ({skill}).",
        "manual": f"{agent} has no automatic dispatch lane; {agent} dispatch is manual.",
        "failed": f"{agent} invocation failed. The feature is Blocked and can be re-dispatched.",
        "abandoned": (
            f"{agent} exited without handing the mission back. The feature is Blocked "
            "and can be re-dispatched."
        ),
        "ci-failed": "Canonical CI failed on the implementation PR head.",
        "released": f"Dispatch to {agent} ({skill}) is complete; the claim is released.",
        "invalid": "Routing metadata is inconsistent; nothing was dispatched.",
    }.get(status, f"Dispatch record: {status}.")
    detail = record.get("detail") or ""
    run_url = record.get("run_url") or ""
    lines = [f"**Dispatch** — {headline}"]
    if detail:
        lines.append("")
        lines.append(detail)
    if run_url:
        lines.append("")
        lines.append(f"[Workflow run]({run_url})")
    payload = json.dumps(record, sort_keys=True, separators=(",", ":"))
    lines += ["", f"{DISPATCH_MARKER} {payload} -->"]
    return "\n".join(lines)


def release(record: dict[str, Any] | None, reason: str, **extra: Any) -> dict[str, Any] | None:
    """Spend an active claim so the same assignment may be dispatched again.

    Rework is the normal case: ChatGPT CTO rejects a PR, sets the same
    feature back to Ready with the same agent and skill, and expects the
    same worker to pick it up. That only works if finishing a mission
    releases its claim.
    """
    if not record or record.get("status") != ACTIVE_STATUS:
        return None
    released = dict(record)
    released.update({"status": "released", "detail": reason, **extra})
    return released


def _outcome(
    action: str,
    reason: str,
    *,
    agent: str = "",
    skill: str = "",
    claim_key: str = "",
    status_updates: dict[str, str] | None = None,
    record: dict[str, Any] | None = None,
    mission: str = "",
    worker: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "action": action,
        "reason": reason,
        "agent": agent,
        "skill": skill,
        "skill_file": skill_file(skill) or "",
        "claim_key": claim_key,
        "status_updates": status_updates or {},
        "record": record,
        "mission": mission,
        "worker": worker or {},
    }


def _metadata_key(*parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
    return f"invalid:{digest[:16]}"


def decide(payload: dict[str, Any]) -> dict[str, Any]:
    """Decide what the dispatch workflow should do for one control issue."""
    issue = payload.get("issue") or {}
    body = issue.get("body") or ""
    labels = list(issue.get("labels") or [])
    record = payload.get("record")
    lanes = payload.get("lanes") or {}
    force = bool(payload.get("force"))
    now = payload.get("now") or ""
    run_url = payload.get("run_url") or ""
    repository = payload.get("repository") or ""
    worker_routing = payload.get("worker_routing") or {}
    number = int(issue.get("number") or 0)

    if (issue.get("state") or "open").lower() != "open":
        return _outcome("skip", "the control issue is closed")

    status = parse_status(body)
    if status is None:
        # Not every issue is a feature control issue. Absence of the status
        # block is the guard that keeps this automation off everything else.
        return _outcome("skip", "no `## Current status` block; not a feature control issue")

    state = canonical(status["State"], STATES)
    agent = canonical(status["Agent"], AGENTS)
    skill = canonical(status["Skill"], SKILLS)
    blocker = " ".join((status.get("Blocker") or "").split())

    # A finished or stalled mission spends its claim, so a later rework of
    # the same assignment can dispatch again.
    if state in _TERMINAL_STATES:
        released = release(record, f"feature moved to {state}")
        if released is not None:
            return _outcome(
                "release",
                f"state is {state}; releasing the active claim",
                agent=released.get("agent", ""),
                skill=released.get("skill", ""),
                claim_key=released.get("key", ""),
                record=released,
            )
        return _outcome("skip", f"state is {state}; nothing to dispatch")

    if state != "Ready":
        return _outcome("skip", f"state is {status['State'] or 'unset'}, not Ready")

    agent_labels, skill_labels = routing_labels(labels)
    legacy = sorted(LEGACY_LABELS.intersection(str(name).casefold() for name in labels))

    problems: list[str] = []
    if agent is None:
        problems.append(f"`Agent` value {status['Agent']!r} is not one of {', '.join(AGENTS)}")
    if skill is None:
        problems.append(f"`Skill` value {status['Skill']!r} is not one of {', '.join(SKILLS)}")
    if problems:
        return _outcome("skip", "; ".join(problems))

    if agent == "Unassigned" or skill == "Unassigned":
        # Choosing a worker or a skill is a CTO decision. Automation stops.
        missing = " and ".join(
            part
            for part, unset in (("Agent", agent == "Unassigned"), ("Skill", skill == "Unassigned"))
            if unset
        )
        return _outcome("skip", f"{missing} is Unassigned; dispatch requires a recorded CTO assignment")

    if blocker.casefold() not in {"none", ""}:
        return _outcome("skip", f"blocker is recorded ({blocker}); dispatch requires Blocker: None")

    # Exactly one routing label of each kind, and both must agree with the
    # status block. Two agent labels is an unresolved assignment, not a hint.
    if len(agent_labels) != 1 or len(skill_labels) != 1:
        detail = (
            f"Expected exactly one `agent:*` and one `skill:*` label. "
            f"Found agent labels {agent_labels or 'none'} and skill labels {skill_labels or 'none'}."
        )
        if legacy:
            detail += (
                f" Legacy queue labels {legacy} are ignored by this automation and "
                "do not route work."
            )
        key = _metadata_key("labels", str(agent_labels), str(skill_labels), str(agent), str(skill))
        return _report_invalid(record, key, detail, agent, skill, now, run_url)

    expected_agent = f"agent:{agent.lower()}"
    expected_skill = f"skill:{skill.lower()}"
    if agent_labels[0] != expected_agent or skill_labels[0] != expected_skill:
        detail = (
            f"The status block says **{agent} / {skill}** but the routing labels are "
            f"`{agent_labels[0]}` and `{skill_labels[0]}`. Automation will not choose "
            "between them."
        )
        key = _metadata_key("mismatch", agent_labels[0], skill_labels[0], agent, skill)
        return _report_invalid(record, key, detail, agent, skill, now, run_url)

    claim_key = f"{agent.lower()}:{skill.lower()}"

    # Any live claim blocks, whatever assignment it holds. Scoping this to a
    # matching <agent>:<skill> key meant a reassignment -- Claude/Build to
    # Codex/Review, or just a different skill -- set back to Ready would
    # dispatch a second worker alongside the first. Reassignment is still
    # supported; it just has to cross an explicit release or force boundary
    # rather than becoming an implicit parallel worker.
    if record and record.get("status") == ACTIVE_STATUS and not force:
        if record.get("key") == claim_key:
            reason = (
                "an active dispatch claim already exists for this assignment; "
                "release it or re-run the workflow manually with force"
            )
        else:
            held_agent = record.get("agent") or "an earlier worker"
            held_skill = record.get("skill") or "unknown skill"
            reason = (
                f"an active dispatch claim for {held_agent}/{held_skill} is still open; "
                "a reassignment dispatches only once that claim is released or the "
                "workflow is re-run manually with force"
            )
        return _outcome("skip", reason, agent=agent, skill=skill, claim_key=claim_key)

    lane = lanes.get(agent, LANE_MANUAL)
    if lane != LANE_AUTOMATIC:
        if record and record.get("key") == claim_key and record.get("status") == "manual":
            return _outcome(
                "skip",
                f"{agent} has no automatic lane and the manual hand-off is already recorded",
                agent=agent,
                skill=skill,
                claim_key=claim_key,
            )
        detail = (
            f"No automatic {agent} lane is configured in this repository, so nothing was "
            f"dispatched. The assignment stands: **{agent} / {skill}**. Start the worker "
            "manually, or configure the lane and re-run the *Feature dispatch* workflow."
        )
        new_record = {
            "key": claim_key,
            "status": "manual",
            "agent": agent,
            "skill": skill,
            "issue": number,
            "at": now,
            "run_url": run_url,
            "detail": detail,
        }
        return _outcome(
            "manual",
            f"{agent} has no automatic dispatch lane",
            agent=agent,
            skill=skill,
            claim_key=claim_key,
            status_updates={"Next": f"Manual {agent} dispatch"},
            record=new_record,
        )

    routing = worker_routing.get(skill) or {}
    mission = build_mission(
        issue_number=number,
        title=issue.get("title") or "",
        body=body,
        agent=agent,
        skill=skill,
        status=status,
        repository=repository,
        routing=routing,
        verify=str(payload.get("verify_command") or ""),
    )
    new_record = {
        "key": claim_key,
        "status": ACTIVE_STATUS,
        "agent": agent,
        "skill": skill,
        "issue": number,
        "at": now,
        "run_url": run_url,
        "detail": f"Skill definition: `{routing.get('skill_file') or skill_file(skill)}`.",
    }
    return _outcome(
        "dispatch",
        f"dispatching {agent} with the {skill} skill",
        agent=agent,
        skill=skill,
        claim_key=claim_key,
        status_updates={"State": "In Progress", "Next": "Worker executing"},
        record=new_record,
        mission=mission,
        worker=dict(routing),
    )


def _report_invalid(
    record: dict[str, Any] | None,
    key: str,
    detail: str,
    agent: str | None,
    skill: str | None,
    now: str,
    run_url: str,
) -> dict[str, Any]:
    if record and record.get("key") == key and record.get("status") == "invalid":
        return _outcome("skip", "inconsistent routing metadata is already reported")
    return _outcome(
        "report",
        "routing metadata is inconsistent",
        agent=agent or "",
        skill=skill or "",
        claim_key=key,
        status_updates={"Next": "CTO fixes routing metadata"},
        record={
            "key": key,
            "status": "invalid",
            "agent": agent or "",
            "skill": skill or "",
            "at": now,
            "run_url": run_url,
            "detail": detail,
        },
    )


def failure_record(record: dict[str, Any], detail: str, **extra: Any) -> dict[str, Any]:
    """Turn an active claim into a truthful, recoverable failure record."""
    failed = dict(record)
    failed.update({"status": "failed", "detail": detail, **extra})
    return failed


# ------------------------------------------------------ post-worker truthfulness

#: What the workflow saw the worker invocation step do. Anything else -- an
#: agent with no callable lane, a step that never ran -- is "not invoked".
WORKER_SUCCEEDED = "success"
WORKER_FAILED = "failure"


def reconciliation(payload: dict[str, Any]) -> dict[str, Any]:
    """Reconcile the recorded state with the fact that the worker has exited.

    The invocation step finishing is not evidence that work is still running;
    it is evidence that it is *not*. A worker signals completion explicitly
    (``scripts.dispatch complete``), which releases the claim. So once the
    step is over, an still-active claim means no hand-back happened, and the
    only truthful state left is Blocked -- never an indefinite
    "In Progress / Worker executing" with nobody executing.
    """
    issue = payload.get("issue") or {}
    record = payload.get("record")
    outcome = str(payload.get("worker_outcome") or "").strip().lower()
    agent = (record or {}).get("agent") or payload.get("agent") or "The worker"

    status = parse_status(issue.get("body") or "")
    if status is None:
        return _outcome("skip", "no `## Current status` block; not a feature control issue")

    if not record or record.get("status") != ACTIVE_STATUS:
        # The worker handed back (or the claim was already spent). Whatever
        # Review/PR state that hand-back wrote is the truth; leave it alone.
        return _outcome("skip", "the dispatch claim is already released; nothing to reconcile")

    state = canonical(status["State"], STATES)
    if state != "In Progress":
        # Something else already moved the feature on while the claim stayed
        # open. Spend the claim so it cannot outlive its mission, but do not
        # rewrite a state this function did not author.
        released = release(record, f"the worker exited with the feature in {state or 'an unknown state'}")
        return _outcome(
            "release",
            f"state is {status['State'] or 'unset'}, not In Progress; releasing the stale claim",
            agent=record.get("agent", ""),
            skill=record.get("skill", ""),
            claim_key=record.get("key", ""),
            record=released,
        )

    if outcome == WORKER_SUCCEEDED:
        blocker = f"{agent} exited without handing the mission back"
        detail = (
            f"The {agent} invocation finished, but the mission was never handed back with "
            "`python3 -m scripts.dispatch complete`, so no implementation was reported ready. "
            "The claim is released and the feature can be re-dispatched."
        )
        status_name = "abandoned"
    elif outcome == WORKER_FAILED:
        blocker = f"{agent} invocation failed"
        detail = (
            f"The {agent} invocation step failed. The workflow logs hold the detail; the "
            "feature is recoverable and can be re-dispatched."
        )
        status_name = "failed"
    else:
        blocker = f"{agent} invocation failed"
        detail = (
            f"No {agent} invocation ran in this repository, so no worker is executing. "
            f"Re-dispatch once a callable {agent} lane is configured."
        )
        status_name = "failed"

    return _outcome(
        "fail",
        detail,
        agent=record.get("agent", ""),
        skill=record.get("skill", ""),
        claim_key=record.get("key", ""),
        status_updates={"State": "Blocked", "Blocker": blocker, "Next": "Retry dispatch"},
        record=failure_record(record, detail, status=status_name),
    )


# --------------------------------------------------------------- PR linking

#: The PR template's own line comes first; the GitHub closing keywords are
#: accepted as a fallback so an existing PR does not have to be edited.
_CONTROL_LINK_RE = re.compile(r"^[ \t]*Control issue:[ \t]*#(\d+)", re.M | re.I)
_CLOSING_KEYWORD_RE = re.compile(
    r"\b(?:fixes|closes|resolves|references|refs)\b[ \t]*:?[ \t]*#(\d+)", re.I
)


def control_issue_from_pr(body: str) -> int | None:
    """The one feature control issue a PR belongs to, or ``None``."""
    for pattern in (_CONTROL_LINK_RE, _CLOSING_KEYWORD_RE):
        match = pattern.search(body or "")
        if match is not None:
            return int(match.group(1))
    return None


def pr_sync(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep the control issue's PR field, state and claim aligned with a PR.

    Automation reports what the PR objectively is. It never marks a feature
    Done -- that is ChatGPT CTO's call -- and it never opens an issue.
    """
    issue = payload.get("issue") or {}
    pull = payload.get("pull") or {}
    record = payload.get("record")

    status = parse_status(issue.get("body") or "")
    if status is None:
        return _outcome("skip", "linked issue has no status block; not a feature control issue")

    number = int(pull.get("number") or 0)
    pr_state = (pull.get("state") or "open").lower()
    merged = bool(pull.get("merged"))
    draft = bool(pull.get("draft"))
    reference = f"#{number}"

    updates: dict[str, str] = {}
    recorded_pr = (status.get("PR") or "").strip()
    if (pr_state == "open" or merged) and recorded_pr != reference:
        updates["PR"] = reference

    new_record = None
    state = canonical(status["State"], STATES)
    if pr_state == "open" and not draft and state == "In Progress":
        # The worker has declared the implementation ready. The mission is
        # over, so the claim is spent and rework can dispatch again.
        updates["State"] = "Review"
        updates["Next"] = "CTO review"
        new_record = release(record, f"implementation PR {reference} ready for review")

    # Two authorities have spoken once a CTO-approved PR is merged by the
    # owner: that, and only that, closes the feature.
    if merged and (status.get("Next") or "").strip().casefold() == "jm merge" and state != "Done":
        updates.update({"State": "Done", "Next": "None"})
        new_record = new_record or release(record, f"{reference} merged after CTO approval")

    if not updates and new_record is None:
        return _outcome("skip", "the control issue already reflects this pull request")

    return _outcome(
        "sync",
        f"synchronising the control issue with pull request {reference}",
        status_updates=updates,
        record=new_record,
    )


# ------------------------------------------------------------- canonical CI

#: The one status context the canonical run publishes on a worker PR head.
#: Distinct from the check GitHub creates for a `pull_request`-triggered run,
#: so the two can never be mistaken for each other.
CI_STATUS_CONTEXT = "canonical-ci"

_PR_FIELD_RE = re.compile(r"#(\d+)")


def linked_pull(body: str) -> int | None:
    """The one implementation pull request a control issue records, if any."""
    status = parse_status(body or "")
    if status is None:
        return None
    match = _PR_FIELD_RE.search(status.get("PR") or "")
    return int(match.group(1)) if match else None


def ci_plan(payload: dict[str, Any]) -> dict[str, Any]:
    """Which exact commit, if any, needs a canonical CI run dispatched.

    A pull request opened or pushed by the workflow's own ``GITHUB_TOKEN``
    does not start a ``pull_request`` run, so a worker PR head would sit
    without the one gate this repository trusts. The answer is the head SHA
    itself -- never "the branch", which can move under a queued run.
    """
    issue = payload.get("issue") or {}
    pull = payload.get("pull") or {}

    number = linked_pull(issue.get("body") or "")
    if number is None:
        return {
            "action": "skip",
            "reason": "no implementation pull request is linked; nothing to validate",
            "sha": "",
            "ref": "",
            "pull": 0,
        }

    head = pull.get("head") or {}
    sha = str(head.get("sha") or "")
    if (pull.get("state") or "open").lower() != "open" or pull.get("merged"):
        return {
            "action": "skip",
            "reason": f"pull request #{number} is not open; nothing to validate",
            "sha": "",
            "ref": "",
            "pull": number,
        }
    if not sha:
        return {
            "action": "skip",
            "reason": f"pull request #{number} reports no head commit",
            "sha": "",
            "ref": "",
            "pull": number,
        }
    return {
        "action": "ci",
        "reason": f"dispatching canonical CI for pull request #{number} at {sha[:7]}",
        "sha": sha,
        "ref": str(head.get("ref") or ""),
        "pull": number,
    }


def ci_result(payload: dict[str, Any]) -> dict[str, Any]:
    """Record the canonical CI verdict for a worker PR head on its feature.

    A green run needs no words: the commit status on the head SHA is the
    durable evidence. A red one must be impossible to miss, so it takes the
    feature out of Review and back to Blocked -- an implementation whose
    canonical gate is failing is not ready for CTO review or for merge.
    """
    issue = payload.get("issue") or {}
    record = payload.get("record")
    conclusion = str(payload.get("conclusion") or "").strip().lower()
    sha = str(payload.get("sha") or "")
    number = int(payload.get("pull") or 0)
    short = sha[:7] or "the PR head"

    status = parse_status(issue.get("body") or "")
    if status is None:
        return _outcome("skip", "no `## Current status` block; not a feature control issue")

    if conclusion == "success":
        return _outcome("skip", f"canonical CI passed on {short}; nothing to record")

    state = canonical(status["State"], STATES)
    if state not in {"In Progress", "Review"}:
        # Blocked already says something truthful, and Done is ChatGPT CTO's
        # word. Neither is automation's to overwrite from a CI run.
        return _outcome(
            "skip",
            f"state is {status['State'] or 'unset'}; leaving it as recorded",
        )

    detail = (
        f"Canonical CI failed on {short}, the head of pull request #{number}. "
        "The implementation is not ready for review or merge until "
        "`python3 scripts/validate.py` passes on the PR head."
    )
    updated = None
    if record:
        updated = dict(record)
        updated.update({"status": "ci-failed", "detail": detail})
    return _outcome(
        "fail",
        detail,
        agent=(record or {}).get("agent", ""),
        skill=(record or {}).get("skill", ""),
        claim_key=(record or {}).get("key", ""),
        status_updates={
            "State": "Blocked",
            "Blocker": f"Canonical CI failed on {short}",
            "Next": "Retry dispatch",
        },
        record=updated,
    )


def completion(payload: dict[str, Any]) -> dict[str, Any]:
    """Hand a finished mission back to ChatGPT CTO.

    This is the completion signal a worker raises deliberately when its
    assigned work is actually ready. PR sync deliberately does not guess it
    from a push: rework happens on an already-open, non-draft PR, where every
    `synchronize` looks identical whether the worker is finished or mid-work.

    It moves the feature to Review and releases the claim. It never marks a
    feature Done -- that decision stays with ChatGPT CTO.
    """
    issue = payload.get("issue") or {}
    record = payload.get("record")
    pull = payload.get("pull")

    status = parse_status(issue.get("body") or "")
    if status is None:
        return _outcome("skip", "no `## Current status` block; not a feature control issue")

    state = canonical(status["State"], STATES)
    if state != "In Progress":
        # Already handed back, or never dispatched. Either way there is
        # nothing to complete, and saying so is better than forcing a state.
        return _outcome(
            "skip",
            f"state is {status['State'] or 'unset'}, not In Progress; nothing to complete",
        )

    updates = {"State": "Review", "Next": "CTO review"}
    if pull:
        reference = f"#{int(pull)}"
        if (status.get("PR") or "").strip() != reference:
            updates["PR"] = reference

    released = release(record, "worker reported the implementation ready for review")
    return _outcome(
        "complete",
        "implementation reported ready; handing the feature to CTO review",
        agent=(record or {}).get("agent", ""),
        skill=(record or {}).get("skill", ""),
        claim_key=(record or {}).get("key", ""),
        status_updates=updates,
        record=released,
    )
