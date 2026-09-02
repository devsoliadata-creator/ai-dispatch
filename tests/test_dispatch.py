"""The automated engineering dispatch layer.

These tests are the proof that the routing rules hold, so they exercise the
decision engine directly rather than a GitHub event: the workflows only
gather state, call :func:`decide`, and apply the answer.
"""

from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys

import pytest

from scripts.dispatch import (
    DISPATCH_MARKER,
    apply_status,
    build_mission,
    completion,
    control_issue_from_pr,
    decide,
    parse_record,
    parse_status,
    pr_sync,
    release,
    render_record,
)
from scripts.dispatch.dispatcher import failure_record

ROOT = pathlib.Path(__file__).resolve().parents[1]

LANES = {"Claude": "automatic", "Codex": "manual", "Local": "manual"}


def issue_body(
    state="Ready",
    agent="Claude",
    skill="Debug",
    pr="None",
    blocker="None",
    nxt="Dispatch",
):
    return f"""# Feature

## Current status

**State:** {state}
**Agent:** {agent}
**Skill:** {skill}
**PR:** {pr}
**Blocker:** {blocker}
**Next:** {nxt}

## Outcome

Availability lookups stop returning another venue's inventory.

## Why

Wrong inventory is worse than no inventory.

## Scope

In scope:

- The OpenTable availability adapter.

Out of scope:

- Live booking.

## Acceptance criteria

- [ ] A mismatched venue fails with a typed error.

## Evidence

**Automated:**

- pytest

## Decisions and context

CTO decision: reuse PR #39; do not open a second implementation PR.

## Deployment

**Required:** No
"""


def snapshot(body=None, labels=("agent:claude", "skill:debug"), record=None, **kwargs):
    payload = {
        "issue": {
            "number": 42,
            "title": "OpenTable availability correctness",
            "state": "open",
            "body": body if body is not None else issue_body(),
            "labels": list(labels),
        },
        "record": record,
        "lanes": dict(LANES),
        "force": False,
        "now": "2026-09-01T06:00:00Z",
        "run_url": "https://github.com/o/r/actions/runs/1",
        "repository": "o/r",
    }
    payload.update(kwargs)
    return payload


# --------------------------------------------------------------- status block


def test_status_block_round_trips_without_touching_prose():
    body = issue_body()
    status = parse_status(body)
    assert status == {
        "State": "Ready",
        "Agent": "Claude",
        "Skill": "Debug",
        "PR": "None",
        "Blocker": "None",
        "Next": "Dispatch",
    }
    updated = apply_status(body, {"State": "In Progress", "Next": "Worker executing"})
    assert parse_status(updated)["State"] == "In Progress"
    assert parse_status(updated)["Next"] == "Worker executing"
    # Everything outside the block survives byte-for-byte.
    assert "reuse PR #39" in updated
    assert updated.split("## Outcome", 1)[1] == body.split("## Outcome", 1)[1]


def test_issue_without_a_status_block_is_not_a_control_issue():
    assert parse_status("# Something else\n\nNo block here.\n") is None
    result = decide(snapshot(body="# Bug report\n\nIt broke.\n"))
    assert result["action"] == "skip"
    assert "not a feature control issue" in result["reason"]


def test_repository_issue_template_carries_a_parsable_status_block():
    template = (ROOT / "templates" / "caller" / ".github" / "ISSUE_TEMPLATE" / "feature.md").read_text(encoding="utf-8")
    status = parse_status(template)
    assert status is not None
    assert status["State"] == "Ready"
    assert status["Agent"] == "Unassigned"
    assert status["Skill"] == "Unassigned"


# ----------------------------------------------------------- required routing


def test_ready_claude_debug_dispatches_exactly_once():
    """1. Ready + Claude + Debug -> exactly one Claude dispatch attempt."""
    first = decide(snapshot())
    assert first["action"] == "dispatch"
    assert first["agent"] == "Claude"
    assert first["skill"] == "Debug"
    assert first["skill_file"] == ".agents/skills/debug/SKILL.md"
    assert first["status_updates"] == {"State": "In Progress", "Next": "Worker executing"}
    assert first["record"]["status"] == "dispatched"

    # The workflow has now written State: In Progress and the claim. A second
    # event for the same assignment finds neither an eligible state nor a
    # free claim.
    applied = apply_status(issue_body(), first["status_updates"])
    second = decide(snapshot(body=applied, record=first["record"]))
    assert second["action"] == "skip"


def test_codex_review_routes_truthfully_when_no_lane_exists():
    """2. Ready + Codex + Review -> truthful manual result, not a fake dispatch."""
    result = decide(
        snapshot(body=issue_body(agent="Codex", skill="Review"), labels=("agent:codex", "skill:review"))
    )
    assert result["action"] == "manual"
    assert result["agent"] == "Codex"
    assert result["skill"] == "Review"
    # State stays Ready; only Next tells the truth about what has to happen.
    assert result["status_updates"] == {"Next": "Manual Codex dispatch"}
    assert result["record"]["status"] == "manual"
    assert "No automatic Codex lane" in result["record"]["detail"]


def test_local_data_routes_truthfully_when_no_lane_exists():
    """3. Ready + Local + Data -> truthful manual result."""
    result = decide(
        snapshot(body=issue_body(agent="Local", skill="Data"), labels=("agent:local", "skill:data"))
    )
    assert result["action"] == "manual"
    assert result["status_updates"] == {"Next": "Manual Local dispatch"}


def test_manual_lane_is_reported_once_not_on_every_event():
    result = decide(
        snapshot(body=issue_body(agent="Codex", skill="Review"), labels=("agent:codex", "skill:review"))
    )
    repeat = decide(
        snapshot(
            body=issue_body(agent="Codex", skill="Review", nxt="Manual Codex dispatch"),
            labels=("agent:codex", "skill:review"),
            record=result["record"],
        )
    )
    assert repeat["action"] == "skip"


def test_configured_lane_dispatches_that_agent():
    lanes = {"Claude": "automatic", "Codex": "automatic", "Local": "manual"}
    result = decide(
        snapshot(
            body=issue_body(agent="Codex", skill="Review"),
            labels=("agent:codex", "skill:review"),
            lanes=lanes,
        )
    )
    assert result["action"] == "dispatch"
    assert result["agent"] == "Codex"


def test_unassigned_agent_does_not_dispatch():
    """4. Ready + Unassigned agent -> no dispatch."""
    result = decide(snapshot(body=issue_body(agent="Unassigned"), labels=("skill:debug",)))
    assert result["action"] == "skip"
    assert "Unassigned" in result["reason"]
    assert result["record"] is None


def test_missing_skill_does_not_dispatch():
    """5. Ready + missing skill -> no dispatch."""
    result = decide(snapshot(body=issue_body(skill="Unassigned"), labels=("agent:claude",)))
    assert result["action"] == "skip"
    assert "Skill is Unassigned" in result["reason"]


def test_blocked_feature_does_not_dispatch():
    """6. Blocked feature -> no dispatch."""
    result = decide(snapshot(body=issue_body(state="Blocked", blocker="Provider contract drift")))
    assert result["action"] == "skip"


def test_ready_with_a_recorded_blocker_does_not_dispatch():
    result = decide(snapshot(body=issue_body(blocker="Waiting on provider capture")))
    assert result["action"] == "skip"
    assert "Blocker: None" in result["reason"]


def test_duplicate_github_event_does_not_dispatch_twice():
    """7. Duplicate GitHub event -> no duplicate dispatch."""
    first = decide(snapshot())
    # A replayed event carries the *original* body: state has not been
    # observed as In Progress yet, so only the durable claim can stop it.
    replay = decide(snapshot(record=first["record"]))
    assert replay["action"] == "skip"
    assert "active dispatch claim" in replay["reason"]


def test_workflow_retry_does_not_create_a_parallel_worker():
    """8. Workflow retry -> no parallel duplicate dispatch."""
    first = decide(snapshot())
    retry = decide(snapshot(body=apply_status(issue_body(), first["status_updates"]), record=first["record"]))
    assert retry["action"] == "skip"
    forced = decide(snapshot(record=first["record"], force=True))
    assert forced["action"] == "dispatch", "a deliberate forced re-run must still be possible"


def test_corrupt_claim_is_treated_as_held_not_as_absent():
    record = parse_record(f"{DISPATCH_MARKER} {{not json}} -->")
    assert record["status"] == "dispatched"
    assert decide(snapshot(record=record))["action"] == "skip"


def test_invocation_failure_produces_a_truthful_recoverable_state():
    """9. Worker invocation failure -> truthful recoverable state."""
    dispatched = decide(snapshot())["record"]
    failed = failure_record(dispatched, "Claude invocation failed")
    assert failed["status"] == "failed"
    # The workflow writes Blocked/Retry alongside this record; the claim is
    # spent, so a deliberate retry can dispatch again.
    blocked = apply_status(
        issue_body(),
        {"State": "Blocked", "Blocker": "Claude invocation failed", "Next": "Retry dispatch"},
    )
    assert decide(snapshot(body=blocked, record=failed))["action"] == "skip"
    retried = decide(snapshot(record=failed))
    assert retried["action"] == "dispatch"


def test_a_live_claim_blocks_a_reassignment_too():
    """A different agent or skill must not become a second parallel worker."""
    dispatched = decide(snapshot())["record"]
    assert dispatched["key"] == "claude:debug"

    reassigned = decide(
        snapshot(
            body=issue_body(agent="Codex", skill="Review"),
            labels=("agent:codex", "skill:review"),
            record=dispatched,
        )
    )
    assert reassigned["action"] == "skip"
    assert "Claude/Debug is still open" in reassigned["reason"]

    # The reassignment goes through once the claim is released. Codex needs a
    # lane here, or "no longer blocked" would show up as the manual hand-off
    # rather than as the dispatch this is checking for.
    lanes = {"Claude": "automatic", "Codex": "automatic", "Local": "manual"}
    released = release(dispatched, "feature moved to Review")
    after_release = decide(
        snapshot(
            body=issue_body(agent="Codex", skill="Review"),
            labels=("agent:codex", "skill:review"),
            record=released,
            lanes=lanes,
        )
    )
    assert after_release["action"] == "dispatch"
    assert after_release["agent"] == "Codex"

    # ...or when the workflow is deliberately forced.
    forced = decide(
        snapshot(
            body=issue_body(agent="Codex", skill="Review"),
            labels=("agent:codex", "skill:review"),
            record=dispatched,
            force=True,
            lanes=lanes,
        )
    )
    assert forced["action"] == "dispatch"


def test_a_live_claim_for_a_different_skill_blocks_too():
    dispatched = decide(snapshot())["record"]
    same_agent_new_skill = decide(
        snapshot(
            body=issue_body(agent="Claude", skill="Build"),
            labels=("agent:claude", "skill:build"),
            record=dispatched,
        )
    )
    assert same_agent_new_skill["action"] == "skip"


def test_rework_of_the_same_assignment_dispatches_again_after_release():
    dispatched = decide(snapshot())["record"]
    # PR opened -> Review; the PR-sync workflow releases the claim.
    released = release(dispatched, "implementation ready for review")
    assert released["status"] == "released"
    rework = decide(snapshot(record=released))
    assert rework["action"] == "dispatch"


def test_review_state_releases_an_active_claim():
    dispatched = decide(snapshot())["record"]
    result = decide(snapshot(body=issue_body(state="Review", pr="#43"), record=dispatched))
    assert result["action"] == "release"
    assert result["record"]["status"] == "released"


def test_done_state_never_dispatches():
    assert decide(snapshot(body=issue_body(state="Done")))["action"] == "skip"


# ------------------------------------------------------------- routing labels


def test_two_agent_labels_is_an_unresolved_assignment_not_a_hint():
    result = decide(snapshot(labels=("agent:claude", "agent:codex", "skill:debug")))
    assert result["action"] == "report"
    assert result["record"]["status"] == "invalid"
    assert "exactly one" in result["record"]["detail"]


def test_labels_disagreeing_with_the_status_block_never_dispatch():
    result = decide(snapshot(labels=("agent:codex", "skill:debug")))
    assert result["action"] == "report"
    assert "will not choose" in result["record"]["detail"]


def test_inconsistent_metadata_is_reported_once():
    first = decide(snapshot(labels=("agent:claude", "agent:codex", "skill:debug")))
    repeat = decide(snapshot(labels=("agent:claude", "agent:codex", "skill:debug"), record=first["record"]))
    assert repeat["action"] == "skip"


def test_legacy_claude_labels_do_not_trigger_dispatch():
    """12. Legacy Claude labels do not trigger new dispatch independently."""
    result = decide(
        snapshot(body=issue_body(agent="Unassigned", skill="Unassigned"), labels=("dispatch:claude", "fix:claude", "claude:working"))
    )
    assert result["action"] == "skip"
    assert result["record"] is None

    # Even with a complete status block, legacy labels supply no routing.
    partial = decide(snapshot(labels=("dispatch:claude", "claude:working")))
    assert partial["action"] == "report"
    assert "do not route work" in partial["record"]["detail"]


# ------------------------------------------------------------------- mission


def test_mission_carries_the_control_issue_and_the_skill_file():
    mission = decide(snapshot())["mission"]
    assert "CONTROL ISSUE\n#42 in o/r" in mission
    assert "SKILL\nDebug -- .agents/skills/debug/SKILL.md" in mission
    for section in ("OUTCOME", "SCOPE", "ACCEPTANCE", "DURABLE DECISIONS", "CURRENT BLOCKER", "RETURN"):
        assert f"\n{section}\n" in mission
    assert "typed error" in mission          # acceptance criteria
    assert "reuse PR #39" in mission          # durable decisions
    assert "Wrong inventory is worse" not in mission, "the mission is not the whole issue"


def test_mission_does_not_duplicate_the_skill_definition():
    mission = decide(snapshot())["mission"]
    skill_text = (ROOT / ".agents" / "skills" / "debug" / "SKILL.md").read_text(encoding="utf-8")
    body_line = "Identify the first failing boundary."
    assert body_line in skill_text
    assert body_line not in mission, "the skill file stays authoritative"


def test_mission_denies_cto_authority_to_the_worker():
    """11. A worker cannot acquire CTO authority through cto-ops."""
    mission = decide(snapshot())["mission"]
    assert "does not grant you CTO authority" in mission
    assert "$cto-ops" in mission
    assert "Do not reassign the agent or skill" in mission

    skill = (ROOT / ".agents" / "skills" / "cto-ops" / "SKILL.md").read_text(encoding="utf-8")
    assert "Loading this skill never grants technical decision authority." in skill
    assert "must not claim a CTO decision" in skill


def test_mission_carries_no_credentials():
    mission = decide(snapshot())["mission"]
    for forbidden in ("ANTHROPIC_API_KEY", "GITHUB_TOKEN", "VPS_SSH_KEY", "secrets."):
        assert forbidden not in mission


def test_long_sections_are_bounded():
    body = issue_body().replace(
        "- The OpenTable availability adapter.", "- x" * 5000
    )
    mission = build_mission(
        issue_number=1,
        title="t",
        body=body,
        agent="Claude",
        skill="Build",
        status=parse_status(body),
    )
    assert "[truncated" in mission
    assert len(mission) < 20000


# -------------------------------------------------------------------- record


def test_record_round_trips_through_the_comment():
    record = decide(snapshot())["record"]
    comment = render_record(record)
    assert parse_record(comment) == record
    # Julia reads the first line; the machine reads the marker.
    assert comment.startswith("**Dispatch** — Dispatched to Claude (Debug).")
    assert DISPATCH_MARKER in comment


def test_release_of_a_spent_record_is_a_no_op():
    assert release(None, "x") is None
    assert release({"status": "failed"}, "x") is None


# ----------------------------------------------------------------------- cli


def test_cli_decide_speaks_json():
    """The workflow drives this exact command; a shape change must fail here."""
    result = subprocess.run(
        [sys.executable, "-m", "scripts.dispatch", "decide"],
        cwd=ROOT,
        input=json.dumps(snapshot()),
        text=True,
        capture_output=True,
        check=True,
    )
    parsed = json.loads(result.stdout)
    assert parsed["action"] == "dispatch"
    assert parsed["agent"] == "Claude"
    assert DISPATCH_MARKER in parsed["comment"]


def test_cli_exposes_the_commands_the_workflows_call():
    from scripts.dispatch.__main__ import build_parser

    parser = build_parser()
    for command in ("decide", "dispatch", "reconcile", "pr-sync", "ci-plan", "ci-result"):
        assert parser.parse_args([command, *_required_args(command)]).command == command


def _required_args(command):
    return {
        "decide": [],
        "dispatch": ["--issue", "42"],
        "reconcile": ["--issue", "42"],
        "pr-sync": ["--pull", "43"],
        "ci-plan": ["--issue", "42"],
        "ci-result": ["--pull", "43", "--conclusion", "failure"],
    }[command]


# ------------------------------------------------------------------ pr sync


@pytest.mark.parametrize(
    "body,expected",
    [
        ("## Why\n\nControl issue: #42\n", 42),
        ("Closes #7", 7),
        ("Closes #12\n\nControl issue: #42\n", 42),  # the template line wins
        ("no reference at all", None),
    ],
)
def test_control_issue_is_read_from_the_pull_request(body, expected):
    assert control_issue_from_pr(body) == expected


def test_pr_linking_updates_the_feature_pr_field():
    """10. PR linking -> feature PR field updated correctly."""
    body = apply_status(issue_body(), {"State": "In Progress", "Next": "Worker executing"})
    result = pr_sync(
        {
            "issue": {"number": 42, "body": body},
            "pull": {"number": 43, "state": "open", "draft": True, "merged": False},
            "record": decide(snapshot())["record"],
        }
    )
    assert result["action"] == "sync"
    assert result["status_updates"] == {"PR": "#43"}
    assert parse_status(apply_status(body, result["status_updates"]))["PR"] == "#43"


def test_ready_for_review_moves_the_feature_to_review_and_releases_the_claim():
    body = apply_status(issue_body(), {"State": "In Progress", "Next": "Worker executing"})
    dispatched = decide(snapshot())["record"]
    result = pr_sync(
        {
            "issue": {"number": 42, "body": body},
            "pull": {"number": 43, "state": "open", "draft": False, "merged": False},
            "record": dispatched,
        }
    )
    assert result["status_updates"] == {"PR": "#43", "State": "Review", "Next": "CTO review"}
    assert result["record"]["status"] == "released"


def test_pr_sync_never_marks_a_feature_done():
    body = apply_status(issue_body(state="Review", pr="#43"), {})
    result = pr_sync(
        {
            "issue": {"number": 42, "body": body},
            "pull": {"number": 43, "state": "closed", "draft": False, "merged": True},
            "record": None,
        }
    )
    assert "Done" not in json.dumps(result["status_updates"])


def test_pr_sync_is_idempotent():
    body = apply_status(issue_body(state="Review", pr="#43", nxt="CTO review"), {})
    result = pr_sync(
        {
            "issue": {"number": 42, "body": body},
            "pull": {"number": 43, "state": "open", "draft": False, "merged": False},
            "record": None,
        }
    )
    assert result["action"] == "skip"


def test_pr_sync_ignores_an_issue_without_a_status_block():
    result = pr_sync(
        {
            "issue": {"number": 5, "body": "# Backlog item\n\nNo block."},
            "pull": {"number": 43, "state": "open", "draft": False, "merged": False},
            "record": None,
        }
    )
    assert result["action"] == "skip"


# ----------------------------------------------------- commands end to end


class FakeGitHub:
    """The four REST calls the commands make, against in-memory state.

    The commands are the part the workflows actually run, so their read ->
    decide -> write sequence is worth proving without a network.
    """

    def __init__(self, issue, comments=None, pull=None):
        self.repository = "o/r"
        self.issue = dict(issue)
        self.comments = list(comments or [])
        self.pull = dict(pull or {})
        self.calls = []

    def get_issue(self, number):
        self.calls.append(("get_issue", number))
        return self.issue

    def update_issue(self, number, **fields):
        self.calls.append(("update_issue", number))
        self.issue.update(fields)
        return self.issue

    def list_comments(self, number):
        return self.comments

    def add_labels(self, number, labels):
        self.labels_added = getattr(self, "labels_added", []) + [(number, list(labels))]

    def remove_label(self, number, label):
        self.labels_removed = getattr(self, "labels_removed", []) + [(number, label)]

    def create_comment(self, number, body):
        self.calls.append(("create_comment", number))
        self.comments.append({"id": 900 + len(self.comments), "body": body})
        return self.comments[-1]

    def update_comment(self, comment_id, body):
        self.calls.append(("update_comment", comment_id))
        for comment in self.comments:
            if comment["id"] == comment_id:
                comment["body"] = body
        return {"id": comment_id, "body": body}

    def get_pull(self, number):
        return self.pull


@pytest.fixture
def cli(monkeypatch):
    """Run a dispatch command against a FakeGitHub and return it."""
    from scripts.dispatch import __main__ as main_module

    def _run(argv, api, expect=0):
        monkeypatch.setattr(main_module, "GitHub", lambda *a, **k: api)
        monkeypatch.setenv("LANE_CLAUDE", "automatic")
        monkeypatch.setenv("LANE_CODEX", "manual")
        monkeypatch.setenv("LANE_LOCAL", "manual")
        assert main_module.main(["prog", *argv]) == expect
        return api

    return _run


def _issue(body=None, labels=("agent:claude", "skill:debug"), number=42):
    return {
        "number": number,
        "title": "OpenTable availability correctness",
        "state": "open",
        "body": body if body is not None else issue_body(),
        "labels": [{"name": name} for name in labels],
    }


def test_dispatch_command_claims_before_it_returns(cli, tmp_path):
    api = FakeGitHub(_issue())
    out = tmp_path / "decision.json"
    cli(["dispatch", "--issue", "42", "--out", str(out)], api)

    decision = json.loads(out.read_text())
    assert decision["action"] == "dispatch"
    assert decision["mission"]

    status = parse_status(api.issue["body"])
    assert status["State"] == "In Progress"
    assert status["Next"] == "Worker executing"
    assert len(api.comments) == 1
    assert parse_record(api.comments[0]["body"])["status"] == "dispatched"

    # A second run against the state the first one left behind is inert: no
    # issue update, no second comment.
    before = list(api.calls)
    api.calls.clear()
    cli(["dispatch", "--issue", "42"], api)
    assert api.calls == [("get_issue", 42)]
    assert len(api.comments) == 1
    assert before  # the first run really did write


def test_dispatch_command_updates_the_one_record_comment_in_place(cli):
    api = FakeGitHub(_issue())
    cli(["dispatch", "--issue", "42"], api)
    cli(["reconcile", "--issue", "42", "--agent", "Claude",
         "--worker-outcome", "failure"], api, expect=1)

    assert len(api.comments) == 1, "one dispatch comment per feature, rewritten in place"
    assert parse_record(api.comments[0]["body"])["status"] == "failed"
    status = parse_status(api.issue["body"])
    assert status["State"] == "Blocked"
    assert status["Blocker"] == "Claude invocation failed"
    assert status["Next"] == "Retry dispatch"
    assert "Traceback" not in api.issue["body"]


def test_reconcile_without_an_active_claim_changes_nothing(cli):
    api = FakeGitHub(_issue())
    cli(["reconcile", "--issue", "42", "--worker-outcome", "failure"], api)
    assert parse_status(api.issue["body"])["State"] == "Ready"
    assert api.comments == []


def test_unsupported_lane_command_leaves_the_feature_ready(cli):
    api = FakeGitHub(_issue(body=issue_body(agent="Codex", skill="Review"),
                            labels=("agent:codex", "skill:review")))
    cli(["dispatch", "--issue", "42"], api)
    status = parse_status(api.issue["body"])
    assert status["State"] == "Ready", "an unsupported lane must not look dispatched"
    assert status["Next"] == "Manual Codex dispatch"
    assert parse_record(api.comments[0]["body"])["status"] == "manual"


def test_pr_sync_command_links_the_pull_request(cli):
    body = apply_status(issue_body(), {"State": "In Progress", "Next": "Worker executing"})
    api = FakeGitHub(
        _issue(body=body),
        pull={"number": 43, "state": "open", "draft": False, "merged": False,
              "body": "## Why\n\nControl issue: #42\n"},
    )
    cli(["pr-sync", "--pull", "43"], api)
    status = parse_status(api.issue["body"])
    assert status["PR"] == "#43"
    assert status["State"] == "Review"
    assert status["Next"] == "CTO review"


def test_pr_sync_command_ignores_a_pull_request_with_no_control_issue(cli):
    api = FakeGitHub(_issue(), pull={"number": 43, "state": "open", "body": "no link"})
    cli(["pr-sync", "--pull", "43"], api)
    assert api.calls == []


# --------------------------------------------------------------- completion


def test_worker_completion_moves_the_feature_to_review_and_releases():
    """The explicit hand-back a worker raises when its work is actually ready."""
    body = apply_status(issue_body(), {"State": "In Progress", "Next": "Worker executing"})
    dispatched = decide(snapshot())["record"]
    result = completion({"issue": {"number": 42, "body": body}, "record": dispatched, "pull": 43})

    assert result["action"] == "complete"
    assert result["status_updates"] == {"State": "Review", "Next": "CTO review", "PR": "#43"}
    assert result["record"]["status"] == "released"

    # And the released claim lets the CTO re-dispatch rework.
    assert decide(snapshot(record=result["record"]))["action"] == "dispatch"


def test_worker_completion_works_without_a_pull_request():
    body = apply_status(issue_body(), {"State": "In Progress"})
    result = completion({"issue": {"number": 42, "body": body}, "record": None, "pull": None})
    assert result["status_updates"] == {"State": "Review", "Next": "CTO review"}


def test_worker_completion_is_idempotent():
    body = apply_status(issue_body(state="Review", pr="#43", nxt="CTO review"), {})
    result = completion({"issue": {"number": 42, "body": body}, "record": None, "pull": 43})
    assert result["action"] == "skip"
    assert "not In Progress" in result["reason"]


def test_worker_completion_never_marks_a_feature_done():
    body = apply_status(issue_body(), {"State": "In Progress"})
    result = completion({"issue": {"number": 42, "body": body}, "record": None, "pull": 43})
    assert result["status_updates"]["State"] == "Review"
    assert "Done" not in json.dumps(result["status_updates"])


def test_completion_command_hands_the_feature_back(cli):
    api = FakeGitHub(_issue())
    cli(["dispatch", "--issue", "42"], api)
    assert parse_status(api.issue["body"])["State"] == "In Progress"

    cli(["complete", "--issue", "42", "--pull", "43"], api)
    status = parse_status(api.issue["body"])
    assert (status["State"], status["Next"], status["PR"]) == ("Review", "CTO review", "#43")
    assert len(api.comments) == 1, "still one dispatch comment per feature"
    assert parse_record(api.comments[0]["body"])["status"] == "released"


def test_mission_tells_the_worker_how_to_hand_back():
    """Rework on an open, non-draft PR has no other completion signal."""
    mission = decide(snapshot())["mission"]
    assert "python3 -m scripts.dispatch complete --issue 42" in mission
    assert "State: Review" in mission and "Next: CTO review" in mission
    assert "not a completion signal" in mission
    assert "Do not set State: Done" in mission


def test_pr_sync_still_refuses_to_guess_completion_from_a_push():
    """A push to an open, non-draft PR is not a hand-back; only linking is."""
    body = apply_status(issue_body(state="Review", pr="#43", nxt="CTO review"), {})
    assert pr_sync({
        "issue": {"number": 42, "body": body},
        "pull": {"number": 43, "state": "open", "draft": False, "merged": False},
        "record": None,
    })["action"] == "skip"

    workflow = (ROOT / ".github" / "workflows" / "pr-sync.yml").read_text(encoding="utf-8")
    assert "synchronize" not in workflow, "a push must not be read as a completion signal"


# ------------------------------------------------------------ supply chain


def test_the_worker_action_is_pinned_to_an_immutable_commit():
    """The step holding an Anthropic credential must not follow a mutable tag."""
    workflow = (ROOT / ".github" / "workflows" / "dispatch.yml").read_text(encoding="utf-8")
    third_party = [
        ref for ref in re.findall(r"^\s*uses:\s*(\S+)", workflow, flags=re.M)
        if not ref.startswith("actions/")
    ]
    assert third_party, "the Claude invocation step disappeared"
    for reference in third_party:
        _, _, revision = reference.partition("@")
        assert re.fullmatch(r"[0-9a-f]{40}", revision), f"{reference} is not SHA-pinned"


# ------------------------------------------------------ worker model / effort

from scripts.dispatch import (  # noqa: E402
    EFFORTS,
    RoutingError,
    allowed_tools,
    claude_args,
    claude_settings,
    parse_worker_routing,
    read_all_worker_routing,
    read_worker_routing,
)
from scripts.dispatch.status import SKILL_FILES  # noqa: E402


def test_every_skill_file_records_its_worker_model_and_effort():
    routing = read_all_worker_routing(ROOT)
    assert set(routing) == set(SKILL_FILES)
    for skill, entry in routing.items():
        assert entry["model"] in {"opus", "sonnet", "haiku"} or entry["model"].startswith("claude-"), skill
        assert entry["effort"] in EFFORTS, skill
        assert entry["access"] in {"write", "read-only"}, skill


def test_build_and_review_run_on_different_claude_workers():
    """The CTO-recorded examples: Review on Sonnet, Build on Opus."""
    build = read_worker_routing("Build", ROOT)
    review = read_worker_routing("Review", ROOT)
    assert {k: build[k] for k in ("model", "effort", "access")} == {"model": "opus", "effort": "medium", "access": "write"}
    assert {k: review[k] for k in ("model", "effort", "access")} == {"model": "sonnet", "effort": "medium", "access": "read-only"}
    assert build["skill_file"] == ".agents/skills/build/SKILL.md"
    assert claude_args(build) == "--model opus --effort medium"
    assert claude_args(review) == "--model sonnet --effort medium"


def test_skill_file_is_the_only_place_routing_is_configured():
    workflow = (ROOT / ".github" / "workflows" / "dispatch.yml").read_text(encoding="utf-8")
    assert "claude_args: ${{ steps.decide.outputs.claude_args }}" in workflow
    assert "settings: ${{ steps.decide.outputs.claude_settings }}" in workflow
    assert "allowedTools" not in workflow and "Bash(" not in workflow, "no tool list in workflow YAML"
    assert not re.search(r"--model\s+[a-z]", workflow), "no model is hard-coded in workflow YAML"
    for module in ("dispatcher.py", "mission.py", "__main__.py"):
        text = (ROOT / "scripts" / "dispatch" / module).read_text(encoding="utf-8")
        assert "opus" not in text and "sonnet" not in text, module


@pytest.mark.parametrize(
    "frontmatter, problem",
    [
        ("metadata:\n  github-label: \"skill:build\"\n", "missing `worker-model`"),
        ("metadata:\n  worker-model: opus\n", "missing `worker-effort`"),
        ("metadata:\n  worker-model: gpt-5\n  worker-effort: medium\n", "not one of opus"),
        ("metadata:\n  worker-model: opus\n  worker-effort: extreme\n", "not one of low"),
        ("metadata:\n  worker-model: opus\n  worker-effort: medium\n", "missing `worker-access`"),
        ("metadata:\n  worker-model: opus\n  worker-effort: medium\n  worker-access: admin\n", "not one of write"),
    ],
)
def test_missing_or_invalid_routing_is_a_configuration_error(frontmatter, problem):
    text = f"---\nname: build\n{frontmatter}---\n\n# Build\n"
    with pytest.raises(RoutingError) as excinfo:
        parse_worker_routing(text, source="build")
    assert problem in str(excinfo.value)


def test_a_pinned_model_id_is_accepted():
    text = "---\nname: x\nmetadata:\n  worker-model: claude-opus-5\n  worker-effort: max\n  worker-access: write\n---\n"
    assert parse_worker_routing(text) == {"model": "claude-opus-5", "effort": "max", "access": "write"}


def test_review_worker_is_read_only_and_build_worker_can_ship():
    """Review missions are read-only by default; Build must be able to push its PR."""
    review = allowed_tools(read_worker_routing("Review", ROOT))
    build = allowed_tools(read_worker_routing("Build", ROOT))
    for tool in ("Edit", "Write", "Bash(git push:*)", "Bash(git commit:*)", "Bash(gh pr create:*)"):
        assert tool not in review, tool
        assert tool in build, tool
    for tool in ("Read", "Bash(git diff:*)", "Bash(gh pr review:*)", "Bash(python3 -m pytest:*)",
                 "Bash(python3 -m scripts.dispatch complete:*)"):
        assert tool in review, tool
        assert tool in build, tool


def test_no_worker_profile_can_deploy_merge_or_reach_secrets():
    for skill in ("Build", "Debug", "Review", "QA", "Research", "Data"):
        settings = claude_settings(read_worker_routing(skill, ROOT))
        allow = settings["permissions"]["allow"]
        deny = settings["permissions"]["deny"]
        assert "Bash" not in allow and "Bash(*)" not in allow, skill
        assert not any(t.startswith("Bash(bash") or "deploy" in t for t in allow), skill
        for forbidden in ("Bash(gh pr merge:*)", "Bash(gh secret:*)", "Bash(gh workflow:*)",
                          "Bash(scripts/deploy.sh:*)", "Bash(ssh:*)", "Bash(git push --force:*)",
                          "Bash(git push origin main:*)", "Bash(curl:*)"):
            assert forbidden in deny, (skill, forbidden)


_ESCAPE_HATCHES = (
    # a bare interpreter, or one that takes code/files on the command line
    "Bash(python3:*)", "Bash(python:*)", "Bash(python3 -c:*)", "Bash(python -c:*)",
    "Bash(node:*)", "Bash(perl:*)", "Bash(ruby:*)", "Bash(bash:*)", "Bash(sh:*)", "Bash(zsh:*)",
    "Bash(env:*)", "Bash(eval:*)", "Bash(xargs:*)",
    # package managers: mutating, run install hooks, fetch from the network
    "Bash(pip:*)", "Bash(pip3:*)", "Bash(pip install:*)", "Bash(pip3 install:*)",
    "Bash(python3 -m pip:*)", "Bash(uv:*)", "Bash(npm:*)", "Bash(npx:*)", "Bash(bun:*)",
    # tools with a "run this command" flag: sed e, find -exec, rg --pre, rebase --exec
    "Bash(sed:*)", "Bash(awk:*)", "Bash(find:*)", "Bash(rg:*)", "Bash(git rebase:*)", "Bash(git -c:*)",
)


def test_no_profile_has_a_general_interpreter_or_package_manager_escape():
    """CTO blocker on #49: the allow-list must not hide an unrestricted shell."""
    from scripts.dispatch.routing import is_approved_command

    for skill in ("Build", "Debug", "Review", "QA", "Research", "Data"):
        settings = claude_settings(read_worker_routing(skill, ROOT))
        allow = settings["permissions"]["allow"]
        deny = settings["permissions"]["deny"]
        for hatch in _ESCAPE_HATCHES:
            assert hatch not in allow, (skill, hatch)
        # Every Bash allow entry is a fixed binary + subcommand from the
        # approved shapes; python only through the named entry points.
        for entry in allow:
            if not entry.startswith("Bash("):
                continue
            command = entry[len("Bash("):-len(":*)")]
            assert command.endswith("") and entry.endswith(":*)"), entry
            assert is_approved_command(command), (skill, entry)
            if command.startswith("python"):
                assert command in (
                    "python3 -m pytest", "python3 -m unittest", "python3 -m scripts.dispatch complete",
                ), (skill, entry)
        # and the interpreters/package managers are denied explicitly, which
        # in Claude Code wins over any allow rule
        for denied in ("Bash(python3 -c:*)", "Bash(pip:*)", "Bash(pip3:*)", "Bash(node:*)",
                       "Bash(bash:*)", "Bash(sh:*)", "Bash(sed:*)", "Bash(find:*)",
                       "Bash(git rebase:*)", "Write(.git/**)", "Edit(.git/**)"):
            assert denied in deny, (skill, denied)


def test_read_only_profile_cannot_install_or_mutate():
    review = allowed_tools(read_worker_routing("Review", ROOT))
    for tool in review:
        assert "install" not in tool, tool
        assert not tool.startswith(("Edit", "Write", "MultiEdit", "NotebookEdit")), tool
        assert not tool.startswith(("Bash(git add", "Bash(git commit", "Bash(git push",
                                    "Bash(gh pr create", "Bash(mkdir", "Bash(mv", "Bash(cp")), tool


def test_dispatch_decision_carries_the_worker_for_that_skill():
    routing = read_all_worker_routing(ROOT)
    decision = decide(snapshot(worker_routing=routing))
    assert decision["action"] == "dispatch"
    assert decision["worker"] == routing["Debug"]
    assert "WORKER\nClaude opus at high effort, write access" in decision["mission"]

    body = issue_body(skill="Review")
    decision = decide(snapshot(body=body, labels=("agent:claude", "skill:review"), worker_routing=routing))
    assert decision["worker"] == routing["Review"]
    assert "WORKER\nClaude sonnet at medium effort, read-only access" in decision["mission"]


def test_dispatch_command_hands_the_worker_its_model_and_effort(cli, tmp_path):
    api = FakeGitHub(_issue())
    out = tmp_path / "decision.json"
    cli(["dispatch", "--issue", "42", "--out", str(out)], api)
    decision = json.loads(out.read_text())
    assert {k: decision["worker"][k] for k in ("model", "effort", "access")} == {"model": "opus", "effort": "high", "access": "write"}
    assert decision["claude_args"] == "--model opus --effort high"
    assert "Bash(git push:*)" in decision["claude_settings"]["permissions"]["allow"]
    # The routing is not a secret and does not travel in the record comment.
    assert "--model" not in api.comments[0]["body"]


def test_dispatch_command_refuses_to_claim_on_broken_routing(cli, monkeypatch, tmp_path):
    """A skill without routing stops before any claim, not after a half-start."""
    from scripts.dispatch import __main__ as main_module

    broken = tmp_path / "repo"
    (broken / ".agents" / "skills" / "debug").mkdir(parents=True)
    (broken / ".agents" / "skills" / "debug" / "SKILL.md").write_text("---\nname: debug\n---\n")
    monkeypatch.setattr(main_module, "ROOT", str(broken))
    monkeypatch.setattr(main_module, "DEFAULTS_ROOT", str(broken))
    api = FakeGitHub(_issue())
    monkeypatch.setattr(main_module, "GitHub", lambda *a, **k: api)
    monkeypatch.setenv("LANE_CLAUDE", "automatic")
    assert main_module.main(["prog", "dispatch", "--issue", "42"]) == 1
    assert api.comments == []
    assert parse_status(api.issue["body"])["State"] == "Ready"


# ------------------------------------------------------ multi-repo additions


def test_skill_files_fall_back_to_the_shared_defaults(tmp_path):
    """A repository with no .agents/skills of its own runs on the shared files."""
    empty = tmp_path / "repo"
    empty.mkdir()
    routing = read_all_worker_routing(empty, ROOT)
    assert set(routing) == set(SKILL_FILES)
    assert routing["Build"]["skill_file"].endswith(".agents/skills/build/SKILL.md")
    # a repo-local override wins
    (empty / ".agents" / "skills" / "build").mkdir(parents=True)
    (empty / ".agents" / "skills" / "build" / "SKILL.md").write_text(
        "---\nname: build\nmetadata:\n  worker-model: sonnet\n  worker-effort: low\n  worker-access: write\n---\n"
    )
    local = read_worker_routing("Build", empty, ROOT)
    assert local["model"] == "sonnet" and local["skill_file"] == ".agents/skills/build/SKILL.md"


def test_verify_command_is_allowed_verbatim_and_nothing_wider():
    from scripts.dispatch.routing import validate_verify_command, denied_tools

    build = read_worker_routing("Build", ROOT)
    allow = allowed_tools(build, "npm test")
    assert "Bash(npm test:*)" in allow and "Bash(npm:*)" not in allow
    deny = denied_tools("npm test")
    assert "Bash(npm:*)" not in deny and "Bash(node:*)" in deny and "Bash(npx:*)" in deny
    # python repos keep every deny
    assert denied_tools("python3 scripts/validate.py") == list(denied_tools(""))
    assert "Bash(python3 scripts/validate.py:*)" in allowed_tools(build, "python3 scripts/validate.py")
    for bad in ("npm install", "python3 -c print(1)", "bash run.sh", "npm test && curl x", "pip install x"):
        with pytest.raises(RoutingError):
            validate_verify_command(bad)


def test_workers_may_dispatch_sub_agents_under_the_same_profile():
    for skill in ("Build", "Review"):
        assert "Agent" in allowed_tools(read_worker_routing(skill, ROOT)), skill


def test_mission_names_the_verify_command_or_says_there_is_none():
    routing = read_all_worker_routing(ROOT)
    with_verify = decide(snapshot(worker_routing=routing, verify_command="npm test"))["mission"]
    assert "Run `npm test` before opening or updating the PR" in with_verify
    without = decide(snapshot(worker_routing=routing))["mission"]
    assert "no verify command is configured" in without


# ------------------------------------------------------------- CTO verdict

from scripts.dispatch.verdict import cto_verdict, parse_verdict, upsert_section  # noqa: E402


def _verdict_payload(comment, body=None, labels=("agent:claude", "skill:build"), association="OWNER"):
    return {
        "issue": {"number": 42, "body": body if body is not None else issue_body(state="Review", pr="#43", nxt="CTO review", skill="Build"), "labels": list(labels)},
        "pull": {"number": 43},
        "comment": {"body": comment, "author_association": association},
        "now": "2026-09-01T06:00:00Z",
    }


def test_parse_verdict_reads_the_first_cto_line_and_keeps_the_notes():
    assert parse_verdict("hello") is None
    approve = parse_verdict("CTO: APPROVE\nlooks good")
    assert approve["verdict"] == "APPROVE"
    rework = parse_verdict("cto: rework skill=Debug\n- fix the null path\n- add a test")
    assert rework["verdict"] == "REWORK" and rework["skill"] == "Debug"
    assert rework["notes"].startswith("- fix the null path")
    block = parse_verdict("CTO: BLOCK needs JM to pick the pricing model")
    assert block["reason"] == "needs JM to pick the pricing model"


def test_only_a_trusted_author_can_relay_a_verdict():
    assert cto_verdict(_verdict_payload("CTO: APPROVE", association="NONE"))["action"] == "skip"
    assert cto_verdict(_verdict_payload("CTO: APPROVE", association="CONTRIBUTOR"))["action"] == "skip"
    assert cto_verdict(_verdict_payload("CTO: APPROVE"))["action"] == "approve"


def test_approve_waits_for_the_owner_to_merge_and_merge_closes_the_feature():
    outcome = cto_verdict(_verdict_payload("CTO: APPROVE"))
    assert outcome["status_updates"]["Next"] == "JM merge"
    assert outcome["pr_labels_add"] == ["cto:approved"]
    body = apply_status(_verdict_payload("")["issue"]["body"], outcome["status_updates"])
    # merge before approval never closes the feature; merge after approval does
    unapproved = pr_sync({"issue": {"number": 42, "body": issue_body(state="Review", pr="#43", nxt="CTO review")},
                          "pull": {"number": 43, "state": "closed", "draft": False, "merged": True}, "record": None})
    assert unapproved["status_updates"].get("State") != "Done"
    merged = pr_sync({"issue": {"number": 42, "body": body},
                      "pull": {"number": 43, "state": "closed", "draft": False, "merged": True}, "record": None})
    assert merged["status_updates"]["State"] == "Done"


def test_rework_records_the_notes_in_the_issue_and_redispatches_the_same_feature():
    outcome = cto_verdict(_verdict_payload("CTO: REWORK\nThe retry loop swallows errors. Surface them and add a negative test."))
    assert outcome["action"] == "rework"
    assert outcome["status_updates"] == {"State": "Ready", "Skill": "Build", "Blocker": "None", "Next": "Worker executing"}
    assert outcome["labels"] == ["agent:claude", "skill:build"]
    assert "## Rework" in outcome["body"] and "### Round 1 — on #43" in outcome["body"]
    # the next mission carries the rework notes, first
    body = apply_status(outcome["body"], outcome["status_updates"])
    routing = read_all_worker_routing(ROOT)
    decision = decide(snapshot(body=body, labels=("agent:claude", "skill:build"), worker_routing=routing))
    assert decision["action"] == "dispatch"
    assert decision["mission"].index("REWORK") < decision["mission"].index("OUTCOME")
    assert "swallows errors" in decision["mission"]
    # a second round appends, and a skill override swaps the label
    again = cto_verdict(_verdict_payload("CTO: REWORK skill=Debug\nStill failing on empty input.", body=body))
    assert "### Round 2" in again["body"] and "### Round 1" in again["body"]
    assert again["labels"] == ["agent:claude", "skill:debug"] and again["status_updates"]["Skill"] == "Debug"


def test_block_records_a_one_line_blocker():
    outcome = cto_verdict(_verdict_payload("CTO: BLOCK pricing model is a JM decision"))
    assert outcome["status_updates"]["State"] == "Blocked"
    assert outcome["status_updates"]["Blocker"] == "pricing model is a JM decision"


def test_upsert_section_replaces_or_appends():
    body = "# F\n\n## Outcome\n\nx\n\n## Rework\n\nold\n\n## Deployment\n\nno\n"
    new = upsert_section(body, "Rework", "new")
    assert "old" not in new and "## Rework\n\nnew" in new and "## Deployment" in new
    assert upsert_section("# F\n\n## Outcome\n\nx", "Rework", "n").endswith("## Rework\n\nn\n")


def test_a_rework_free_mission_has_no_rework_section():
    routing = read_all_worker_routing(ROOT)
    assert "REWORK" not in decide(snapshot(worker_routing=routing))["mission"]


def test_completion_flags_the_pull_request_for_the_cto(cli):
    api = FakeGitHub(_issue(body=issue_body(state="In Progress", nxt="Worker executing")))
    cli(["complete", "--issue", "42", "--pull", "43"], api)
    assert (43, ["cto:review"]) in api.labels_added


def test_every_verdict_clears_the_review_flag_on_the_pr():
    for comment in ("CTO: APPROVE", "CTO: REWORK\nfix it", "CTO: BLOCK why"):
        outcome = cto_verdict(_verdict_payload(comment))
        assert "cto:review" in outcome["pr_labels_remove"], comment
