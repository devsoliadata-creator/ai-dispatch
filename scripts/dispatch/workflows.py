"""Just enough workflow-file reading to prove one permission boundary.

The dispatched worker holds an Anthropic credential and write scopes on this
repository. Triggering the canonical CI run for a worker-produced pull
request head needs ``actions: write`` -- a permission that must never be
reachable from the job the worker runs in, or the worker could start
arbitrary workflows. The separation is expressed as two jobs, and *that* is
what has to stay true across edits, so it is checked mechanically rather than
described in a comment.

A whole YAML parser is not a dependency this repository has, and it is not
needed: workflow jobs are block-mapped at a fixed indentation, so splitting
on it is exact for the shape GitHub Actions accepts.
"""

from __future__ import annotations

import re

_JOBS_RE = re.compile(r"^jobs:[ \t]*$", re.M)
_JOB_RE = re.compile(r"^  (?P<name>[A-Za-z_][\w-]*):[ \t]*$")
_TOP_LEVEL_RE = re.compile(r"^\S")


def workflow_jobs(text: str) -> dict[str, str]:
    """Map each job name in a workflow file to its own block of text."""
    start = _JOBS_RE.search(text or "")
    if start is None:
        return {}
    jobs: dict[str, list[str]] = {}
    current: str | None = None
    # Blank and comment lines are held back until something else follows: the
    # comment block above a job header introduces that job, not the previous
    # one, and attributing it wrongly would make a permission read misleading.
    pending: list[str] = []
    for line in text[start.end() :].splitlines():
        if line.strip() and _TOP_LEVEL_RE.match(line):
            break  # a later top-level key ends the jobs mapping
        match = _JOB_RE.match(line)
        if match is not None:
            current = match.group("name")
            jobs[current] = list(pending)
            pending = []
            continue
        if not line.strip() or line.lstrip().startswith("#"):
            pending.append(line)
            continue
        if current is not None:
            jobs[current].extend(pending)
            jobs[current].append(line)
        pending = []
    return {name: "\n".join(lines) for name, lines in jobs.items()}


def jobs_granting(text: str, permission: str) -> list[str]:
    """Names of the jobs in ``text`` that request ``permission``."""
    pattern = re.compile(rf"^[ \t]+{re.escape(permission)}:[ \t]*write[ \t]*$", re.M)
    return sorted(name for name, body in workflow_jobs(text).items() if pattern.search(body))


def jobs_invoking_the_worker(text: str) -> list[str]:
    """Names of the jobs that run the Claude worker action or its credential."""
    return sorted(
        name
        for name, body in workflow_jobs(text).items()
        if "claude-code-action" in body or "ANTHROPIC_API_KEY" in body
    )
