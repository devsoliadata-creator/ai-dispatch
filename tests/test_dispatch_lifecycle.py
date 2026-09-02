"""The dispatch lifecycle after the worker step has exited.

``test_dispatch.py`` proves who may be dispatched. This file proves what is
true once the invocation is over: a mission that was handed back stays handed
back, a mission that was not becomes Blocked rather than an indefinite
"In Progress / Worker executing", and the pull request head a worker produced
gets the one gate this repository trusts run against that exact commit.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from scripts.dispatch import (
    CI_STATUS_CONTEXT,
    apply_status,
    ci_plan,
    ci_result,
    completion,
    decide,
    parse_record,
    parse_status,
    reconciliation,
)
from scripts.dispatch.workflows import (
    jobs_granting,
    jobs_invoking_the_worker,
    workflow_jobs,
)
from tests.test_dispatch import FakeGitHub, _issue, issue_body, snapshot

ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture
def cli(monkeypatch):
    """Run a dispatch command against a FakeGitHub and assert its exit code."""
    from scripts.dispatch import __main__ as main_module

    def _run(argv, api, expect=0):
        monkeypatch.setattr(main_module, "GitHub", lambda *a, **k: api)
        monkeypatch.setenv("LANE_CLAUDE", "automatic")
        monkeypatch.setenv("LANE_CODEX", "manual")
        monkeypatch.setenv("LANE_LOCAL", "manual")
        assert main_module.main(["prog", *argv]) == expect
        return api

    return _run


def _in_progress(record=None, pr="None"):
    """A feature mid-mission, holding whatever claim the caller supplies."""
    return {
        "issue": {
            "number": 42,
            "body": apply_status(
                issue_body(pr=pr), {"State": "In Progress", "Next": "Worker executing"}
            ),
        },
        "record": decide(snapshot())["record"] if record is None else record,
    }


# ------------------------------------------------- post-worker reconciliation


def test_a_worker_that_exits_without_handing_back_cannot_stay_in_progress():
    """The step finishing is evidence nobody is executing, not that somebody is."""
    result = reconciliation({**_in_progress(), "worker_outcome": "success"})

    assert result["action"] == "fail"
    assert result["status_updates"]["State"] == "Blocked"
    assert "without handing the mission back" in result["status_updates"]["Blocker"]
    assert result["status_updates"]["Next"] == "Retry dispatch"
    assert "scripts.dispatch complete" in result["reason"]


def test_missing_hand_back_releases_the_claim_so_rework_can_dispatch_again():
    result = reconciliation({**_in_progress(), "worker_outcome": "success"})

    record = result["record"]
    assert record["status"] == "abandoned"
    assert record["key"] == "claude:debug", "the recorded assignment is preserved"
    # Set back to Ready, the same assignment dispatches -- with no second
    # worker, because the claim this one held is spent.
    assert decide(snapshot(record=record))["action"] == "dispatch"


def test_a_valid_hand_back_is_left_exactly_as_the_worker_left_it():
    """Review and the linked PR survive reconciliation untouched."""
    handed_back = completion({**_in_progress(), "pull": 43})
    body = apply_status(_in_progress()["issue"]["body"], handed_back["status_updates"])

    result = reconciliation(
        {
            "issue": {"number": 42, "body": body},
            "record": handed_back["record"],
            "worker_outcome": "success",
        }
    )

    assert result["action"] == "skip"
    assert result["status_updates"] == {}
    status = parse_status(body)
    assert (status["State"], status["Next"], status["PR"]) == ("Review", "CTO review", "#43")


def test_worker_invocation_failure_is_reconciled_as_a_recoverable_block():
    result = reconciliation({**_in_progress(), "worker_outcome": "failure"})

    assert result["status_updates"]["Blocker"] == "Claude invocation failed"
    assert result["record"]["status"] == "failed"
    assert "logs hold the detail" in result["reason"]
    assert "Traceback" not in json.dumps(result)


def test_a_worker_that_never_ran_is_reconciled_truthfully():
    result = reconciliation({**_in_progress(), "worker_outcome": ""})

    assert result["status_updates"]["State"] == "Blocked"
    assert "No Claude invocation ran" in result["reason"]


def test_reconciliation_releases_a_stale_claim_without_rewriting_the_state():
    """A claim must never outlive its mission, whoever moved the feature on."""
    body = apply_status(issue_body(state="Review", pr="#43", nxt="CTO review"), {})

    result = reconciliation(
        {
            "issue": {"number": 42, "body": body},
            "record": decide(snapshot())["record"],
            "worker_outcome": "success",
        }
    )

    assert result["action"] == "release"
    assert result["status_updates"] == {}, "Review is not this function's to overwrite"
    assert result["record"]["status"] == "released"


def test_reconciliation_ignores_anything_that_is_not_a_control_issue():
    result = reconciliation(
        {"issue": {"number": 42, "body": "just prose"}, "record": None,
         "worker_outcome": "success"}
    )
    assert result["action"] == "skip"


def test_reconcile_command_blocks_the_feature_and_fails_the_run(cli):
    """A green workflow run beside a stalled feature is the same untruth."""
    api = FakeGitHub(_issue())
    cli(["dispatch", "--issue", "42"], api)
    assert parse_status(api.issue["body"])["State"] == "In Progress"

    cli(["reconcile", "--issue", "42", "--agent", "Claude", "--worker-outcome", "success"],
        api, expect=1)

    status = parse_status(api.issue["body"])
    assert (status["State"], status["Next"]) == ("Blocked", "Retry dispatch")
    assert len(api.comments) == 1, "still one dispatch comment per feature"
    assert parse_record(api.comments[0]["body"])["status"] == "abandoned"


def test_reconcile_command_preserves_a_hand_back(cli):
    api = FakeGitHub(_issue())
    cli(["dispatch", "--issue", "42"], api)
    cli(["complete", "--issue", "42", "--pull", "43"], api)
    before = api.issue["body"]

    cli(["reconcile", "--issue", "42", "--agent", "Claude", "--worker-outcome", "success"], api)

    assert api.issue["body"] == before, "a hand-back is not rewritten by reconciliation"
    status = parse_status(api.issue["body"])
    assert (status["State"], status["PR"]) == ("Review", "#43")


# --------------------------------------------------------------- canonical CI


def _head(sha="a" * 40, ref="feat/x", **overrides):
    pull = {
        "number": 43,
        "state": "open",
        "merged": False,
        "draft": False,
        "head": {"sha": sha, "ref": ref},
        "body": "Control issue: #42\n",
    }
    pull.update(overrides)
    return pull


def _reviewing(pr="#43"):
    return apply_status(issue_body(state="Review", pr=pr, nxt="CTO review"), {})


def test_ci_plan_names_the_exact_worker_pull_request_head():
    """A branch can move under a queued run; the commit cannot."""
    plan = ci_plan({"issue": {"number": 42, "body": _reviewing()}, "pull": _head()})

    assert plan["action"] == "ci"
    assert plan["sha"] == "a" * 40
    assert plan["ref"] == "feat/x"
    assert plan["pull"] == 43


@pytest.mark.parametrize(
    "body,pull",
    [
        (issue_body(), _head()),                                  # no PR linked yet
        (_reviewing(), _head(state="closed")),                    # closed
        (_reviewing(), _head(merged=True)),                       # already merged
        (_reviewing(), {"number": 43, "state": "open"}),           # no head reported
        ("just prose", _head()),                                  # not a control issue
    ],
)
def test_ci_plan_dispatches_nothing_without_an_open_head(body, pull):
    plan = ci_plan({"issue": {"number": 42, "body": body}, "pull": pull})
    assert plan["action"] == "skip"
    assert plan["sha"] == ""


def test_ci_plan_command_publishes_the_workflow_outputs(cli, tmp_path):
    api = FakeGitHub(_issue(body=_reviewing()), pull=_head())
    output = tmp_path / "github_output"

    cli(["ci-plan", "--issue", "42", "--github-output", str(output)], api)

    written = dict(line.split("=", 1) for line in output.read_text().splitlines())
    assert written == {
        "ci_action": "ci",
        "ci_sha": "a" * 40,
        "ci_ref": "feat/x",
        "ci_pull": "43",
    }


def test_failed_canonical_ci_blocks_the_feature_and_keeps_the_pull_request():
    released = completion({**_in_progress(), "pull": 43})["record"]

    result = ci_result(
        {
            "issue": {"number": 42, "body": _reviewing()},
            "record": released,
            "conclusion": "failure",
            "sha": "a" * 40,
            "pull": 43,
        }
    )

    assert result["action"] == "fail"
    assert result["status_updates"]["State"] == "Blocked"
    assert result["status_updates"]["Blocker"] == "Canonical CI failed on aaaaaaa"
    assert "PR" not in result["status_updates"], "the one active PR stays linked"
    assert result["record"]["status"] == "ci-failed"


def test_passing_canonical_ci_leaves_the_feature_in_review():
    result = ci_result(
        {"issue": {"number": 42, "body": _reviewing()}, "record": None,
         "conclusion": "success", "sha": "a" * 40, "pull": 43}
    )
    assert result["action"] == "skip"
    assert result["status_updates"] == {}


@pytest.mark.parametrize("state", ["Done", "Blocked"])
def test_ci_result_never_overwrites_a_state_it_did_not_author(state):
    body = apply_status(issue_body(state=state, pr="#43"), {})
    result = ci_result(
        {"issue": {"number": 42, "body": body}, "record": None,
         "conclusion": "failure", "sha": "a" * 40, "pull": 43}
    )
    assert result["action"] == "skip"
    assert result["status_updates"] == {}


def test_ci_result_command_records_the_verdict_on_the_control_issue(cli):
    api = FakeGitHub(_issue(body=_reviewing()), pull=_head())

    cli(["ci-result", "--pull", "43", "--sha", "a" * 40, "--conclusion", "failure"], api)

    status = parse_status(api.issue["body"])
    assert status["State"] == "Blocked"
    assert status["PR"] == "#43", "one feature issue, one active pull request"


# ------------------------------------------------ worker / CI job separation


def _workflow(name):
    """Shared reusable workflows live here; `ci.yml` is what a caller repo commits."""
    root = ROOT / "templates" / "caller" if name == "ci.yml" else ROOT
    return (root / ".github" / "workflows" / name).read_text(encoding="utf-8")


def test_the_claude_worker_job_cannot_start_workflows():
    """`actions: write` starts arbitrary workflows; the worker never holds it."""
    dispatch = _workflow("dispatch.yml")
    worker_jobs = jobs_invoking_the_worker(dispatch)

    assert worker_jobs, "the Claude invocation step disappeared"
    for permission in ("actions", "packages"):
        assert not set(jobs_granting(dispatch, permission)) & set(worker_jobs), (
            f"a worker job requests `{permission}: write`"
        )


def test_the_canonical_ci_job_holds_nothing_but_actions_write():
    """The privileged lane runs no repository code and carries no credential."""
    job = workflow_jobs(_workflow("dispatch.yml"))["canonical-ci"]

    assert "actions: write" in job
    assert "contents: write" not in job and "issues: write" not in job
    assert "ANTHROPIC" not in job and "OAUTH_TOKEN" not in job
    assert "actions/checkout" not in job, "it must never check out worker-authored code"


def test_canonical_ci_is_dispatched_on_the_default_branch_at_the_head_sha():
    job = workflow_jobs(_workflow("dispatch.yml"))["canonical-ci"]

    assert 'gh workflow run "$CI_WORKFLOW" --ref "$BASE"' in job
    assert '-f sha="$SHA"' in job
    assert "default_branch" in job, "the head's own workflow file must never be the one that runs"


def test_ci_validates_the_exact_commit_it_was_asked_to():
    ci = _workflow("ci.yml")
    jobs = workflow_jobs(ci)

    assert "workflow_dispatch:" in ci
    assert "ref: ${{ inputs.sha }}" in jobs["validate"], "the run checks out the head SHA"
    assert 'if [ "$actual" != "$SHA" ]' in jobs["validate"], "and proves it checked it out"


def test_the_job_that_runs_worker_code_holds_no_write_scope():
    """`validate` executes worker-authored tests, so it must hold nothing."""
    ci = _workflow("ci.yml")
    jobs = workflow_jobs(ci)

    for permission in ("contents", "issues", "statuses", "actions", "packages"):
        assert "validate" not in jobs_granting(ci, permission)
    assert "ref: ${{ inputs.sha }}" not in jobs["report"], "the reporting job stays on the base"
    assert "statuses: write" in jobs["report"]
    assert "ci-report.yml@" in jobs["report"], "reporting is delegated to the shared ci-report workflow"
    # The verdict lands on the head SHA under its own context, so it can never
    # be confused with the check a `pull_request` run would have published.
    shared = workflow_jobs(_workflow("ci-report.yml"))["report"]
    assert f'context="{CI_STATUS_CONTEXT}"' in shared
    assert 'statuses/$SHA' in shared
    assert shared.count("actions/checkout") == 1 and "path: .ai-dispatch" in shared, "it checks out only the shared layer"
    assert "ref: ${{ inputs.sha }}" not in shared
