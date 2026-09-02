"""Command line for the dispatch workflows.

    python -m scripts.dispatch decide      < snapshot.json   # pure, for tests
    python -m scripts.dispatch dispatch    --issue 42 [--force] --out decision.json
    python -m scripts.dispatch mark-failed --issue 42 --blocker "..." --detail "..."
    python -m scripts.dispatch complete    --issue 42 --pull 43
    python -m scripts.dispatch pr-sync     --pull 43

``decide`` and ``pr-sync`` reduce to pure functions in ``dispatcher``; the
commands below are the thin shell that reads GitHub, calls one of them, and
writes the answer back. The workflows contain no routing rules of their own.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

from .dispatcher import (
    DISPATCH_MARKER,
    completion,
    control_issue_from_pr,
    decide,
    failure_record,
    parse_record,
    pr_sync,
    render_record,
)
from .github import GitHub, label_names
from .routing import (
    DEFAULTS_ROOT,
    RoutingError,
    claude_args,
    claude_settings,
    read_all_worker_routing,
    validate_verify_command,
)
from .status import apply_status
from .verdict import REVIEW_LABEL, cto_verdict

#: The repository being dispatched: the workflow's checkout of the caller
#: repository (GITHUB_WORKSPACE), or the current directory when run by hand.
#: This package itself lives in the ai-dispatch checkout (DEFAULTS_ROOT) and
#: is shared by every repository; per-repository skill overrides are read
#: from ROOT first.
ROOT = os.environ.get("GITHUB_WORKSPACE") or os.getcwd()

#: The one test/lint entry point this repository's worker may run, declared
#: by the caller workflow. Empty means the worker gets no verify command.
VERIFY_ENV = "DISPATCH_VERIFY_COMMAND"

LANE_ENV = {"Claude": "LANE_CLAUDE", "Codex": "LANE_CODEX", "Local": "LANE_LOCAL"}


def _lanes() -> dict[str, str]:
    return {agent: os.environ.get(env, "manual") or "manual" for agent, env in LANE_ENV.items()}


def _run_url() -> str:
    server = os.environ.get("GITHUB_SERVER_URL", "https://github.com")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    return f"{server}/{repo}/actions/runs/{run_id}" if repo and run_id else ""


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _find_record(api: GitHub, issue_number: int) -> tuple[dict[str, Any] | None, int | None]:
    """The single durable dispatch record comment, if one exists."""
    for comment in reversed(api.list_comments(issue_number)):
        if DISPATCH_MARKER in (comment.get("body") or ""):
            return parse_record(comment["body"]), int(comment["id"])
    return None, None


def _snapshot(api: GitHub, issue_number: int, force: bool) -> tuple[dict[str, Any], int | None]:
    issue = api.get_issue(issue_number)
    record, comment_id = _find_record(api, issue_number)
    snapshot = {
        "issue": {
            "number": issue["number"],
            "title": issue.get("title") or "",
            "body": issue.get("body") or "",
            "state": issue.get("state") or "open",
            "labels": label_names(issue),
        },
        "record": record,
        "lanes": _lanes(),
        "force": force,
        "now": _now(),
        "run_url": _run_url(),
        "repository": api.repository,
        "verify_command": os.environ.get(VERIFY_ENV, ""),
    }
    return snapshot, comment_id


def _apply(
    api: GitHub,
    issue_number: int,
    body: str,
    outcome: dict[str, Any],
    comment_id: int | None,
) -> None:
    """Upsert the one dispatch record comment, then write the status block.

    The claim goes first on purpose: if the second write fails, the durable
    record still exists and the next event stops on it. The reverse order
    would leave a feature that looks dispatched with nothing recording it.
    """
    record = outcome.get("record")
    if record:
        comment = render_record(record)
        if comment_id is not None:
            api.update_comment(comment_id, comment)
        else:
            api.create_comment(issue_number, comment)
    updates = outcome.get("status_updates") or {}
    if updates:
        new_body = apply_status(body, updates)
        if new_body != body:
            api.update_issue(issue_number, body=new_body)


def _emit(outcome: dict[str, Any], out_path: str | None) -> None:
    if out_path:
        with open(out_path, "w", encoding="utf-8") as handle:
            json.dump(outcome, handle)
    summary = f"{outcome.get('action')}: {outcome.get('reason')}"
    print(f"::notice title=Feature dispatch::{summary}")


def cmd_decide(args: argparse.Namespace) -> int:
    payload = json.load(sys.stdin)
    outcome = decide(payload if isinstance(payload, dict) else {})
    if outcome.get("record"):
        outcome["comment"] = render_record(outcome["record"])
    json.dump(outcome, sys.stdout)
    sys.stdout.write("\n")
    return 0


def cmd_dispatch(args: argparse.Namespace) -> int:
    api = GitHub()
    snapshot, comment_id = _snapshot(api, args.issue, args.force)
    # Which model and effort each skill runs on is recorded in the skill
    # files. Read them all before deciding: an unreadable routing is a
    # repository configuration error and must stop the run before any claim
    # is written, not after a worker has been half-started.
    try:
        snapshot["worker_routing"] = read_all_worker_routing(ROOT, DEFAULTS_ROOT)
        snapshot["verify_command"] = validate_verify_command(snapshot["verify_command"])
    except RoutingError as exc:
        print(f"::error title=Feature dispatch::worker routing is invalid -- {exc}")
        return 1
    outcome = decide(snapshot)
    if outcome.get("worker"):
        outcome["claude_args"] = claude_args(outcome["worker"])
        outcome["claude_settings"] = claude_settings(outcome["worker"], snapshot["verify_command"])
    # The claim and the In Progress transition are written before the worker
    # is invoked, so a crash in between leaves a recoverable claim rather
    # than a second worker.
    if outcome["action"] != "skip":
        _apply(api, args.issue, snapshot["issue"]["body"], outcome, comment_id)
    _emit(outcome, args.out)
    return 0


def cmd_mark_failed(args: argparse.Namespace) -> int:
    """Turn an active claim into a truthful, recoverable Blocked state."""
    api = GitHub()
    issue = api.get_issue(args.issue)
    record, comment_id = _find_record(api, args.issue)
    if not record or record.get("status") != "dispatched":
        print("::notice title=Feature dispatch::no active claim to fail")
        return 0
    outcome = {
        "action": "fail",
        "reason": args.detail,
        "status_updates": {
            "State": "Blocked",
            "Blocker": args.blocker,
            "Next": "Retry dispatch",
        },
        "record": failure_record(record, args.detail, at=_now(), run_url=_run_url()),
    }
    _apply(api, args.issue, issue.get("body") or "", outcome, comment_id)
    _emit(outcome, args.out)
    return 0


def cmd_complete(args: argparse.Namespace) -> int:
    """The worker's explicit hand-back: In Progress -> Review."""
    api = GitHub()
    issue = api.get_issue(args.issue)
    record, comment_id = _find_record(api, args.issue)
    outcome = completion(
        {
            "issue": {"number": args.issue, "body": issue.get("body") or ""},
            "record": record,
            "pull": args.pull,
        }
    )
    if outcome["action"] != "skip":
        _apply(api, args.issue, issue.get("body") or "", outcome, comment_id)
        if args.pull:
            # Flag the PR for the CTO: the Mac review script polls this label.
            api.add_labels(int(args.pull), [REVIEW_LABEL])
    _emit(outcome, args.out)
    return 0


def cmd_pr_sync(args: argparse.Namespace) -> int:
    api = GitHub()
    pull = api.get_pull(args.pull)
    issue_number = control_issue_from_pr(pull.get("body") or "")
    if issue_number is None:
        print("::notice title=Feature dispatch::pull request references no control issue")
        return 0
    issue = api.get_issue(issue_number)
    if "pull_request" in issue:
        print("::notice title=Feature dispatch::reference is a pull request, not a control issue")
        return 0
    record, comment_id = _find_record(api, issue_number)
    outcome = pr_sync(
        {
            "issue": {"number": issue_number, "body": issue.get("body") or ""},
            "pull": {
                "number": pull["number"],
                "state": pull.get("state") or "open",
                "draft": bool(pull.get("draft")),
                "merged": bool(pull.get("merged")),
            },
            "record": record,
        }
    )
    if outcome["action"] != "skip":
        _apply(api, issue_number, issue.get("body") or "", outcome, comment_id)
    _emit(outcome, args.out)
    return 0


def cmd_cto_verdict(args: argparse.Namespace) -> int:
    """Relay a `CTO:` comment on a PR to its control issue."""
    api = GitHub()
    pull = api.get_pull(args.pull)
    issue_number = control_issue_from_pr(pull.get("body") or "")
    if issue_number is None:
        print("::notice title=CTO verdict::pull request references no control issue")
        return 0
    issue = api.get_issue(issue_number)
    outcome = cto_verdict(
        {
            "issue": {"number": issue_number, "body": issue.get("body") or "", "labels": label_names(issue)},
            "pull": {"number": pull["number"]},
            "comment": {
                "body": os.environ.get("CTO_COMMENT_BODY", ""),
                "author_association": os.environ.get("CTO_COMMENT_AUTHOR_ASSOCIATION", ""),
            },
            "now": _now(),
        }
    )
    if outcome["action"] != "skip":
        body = outcome.get("body") or issue.get("body") or ""
        fields: dict[str, Any] = {}
        new_body = apply_status(body, outcome.get("status_updates") or {})
        if new_body != (issue.get("body") or ""):
            fields["body"] = new_body
        if outcome.get("labels") is not None:
            fields["labels"] = outcome["labels"]
        if fields:
            api.update_issue(issue_number, **fields)
        for label in outcome.get("pr_labels_add") or []:
            api.add_labels(pull["number"], [label])
        for label in outcome.get("pr_labels_remove") or []:
            api.remove_label(pull["number"], label)
        if outcome.get("reply"):
            api.create_comment(pull["number"], outcome["reply"])
    _emit(outcome, args.out)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m scripts.dispatch")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("decide", help="pure decision, JSON on stdin/stdout").set_defaults(func=cmd_decide)

    dispatch = sub.add_parser("dispatch", help="decide and apply for one control issue")
    dispatch.add_argument("--issue", type=int, required=True)
    dispatch.add_argument("--force", action="store_true")
    dispatch.add_argument("--out")
    dispatch.set_defaults(func=cmd_dispatch)

    failed = sub.add_parser("mark-failed", help="record a failed worker invocation")
    failed.add_argument("--issue", type=int, required=True)
    failed.add_argument("--blocker", required=True)
    failed.add_argument("--detail", required=True)
    failed.add_argument("--out")
    failed.set_defaults(func=cmd_mark_failed)

    complete = sub.add_parser("complete", help="worker hand-back: move the feature to Review")
    complete.add_argument("--issue", type=int, required=True)
    complete.add_argument("--pull", type=int)
    complete.add_argument("--out")
    complete.set_defaults(func=cmd_complete)

    sync = sub.add_parser("pr-sync", help="sync a control issue with its implementation PR")
    sync.add_argument("--pull", type=int, required=True)
    sync.add_argument("--out")
    sync.set_defaults(func=cmd_pr_sync)

    verdict = sub.add_parser("cto-verdict", help="relay a CTO: comment on a PR to its control issue")
    verdict.add_argument("--pull", type=int, required=True)
    verdict.add_argument("--out")
    verdict.set_defaults(func=cmd_cto_verdict)
    return parser


def main(argv: list[str]) -> int:
    args = build_parser().parse_args(argv[1:])
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
