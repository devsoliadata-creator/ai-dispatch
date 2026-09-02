"""The stable ``## Current status`` block on a feature control issue.

The issue body is the human-readable source of truth. Automation may only
read it and rewrite the six known fields in place -- never reorder them,
never touch the prose around them, and never invent a field. Anything that
cannot be parsed is treated as "not a feature control issue", which is the
safe answer: no dispatch.
"""

from __future__ import annotations

import re

STATUS_HEADING = "Current status"

FIELDS = ("State", "Agent", "Skill", "PR", "Blocker", "Next")

STATES = ("Ready", "In Progress", "Review", "Blocked", "Done")
AGENTS = ("Unassigned", "Claude", "Codex", "Local")
SKILLS = ("Unassigned", "Build", "Debug", "Review", "QA", "Research", "Data")

#: Routing labels are metadata only. The canonical execution instructions
#: stay in one place -- the repo-local skill file -- so a worker loads the
#: same definition a human would read.
SKILL_FILES = {
    "Build": ".agents/skills/build/SKILL.md",
    "Debug": ".agents/skills/debug/SKILL.md",
    "Review": ".agents/skills/review/SKILL.md",
    "QA": ".agents/skills/qa/SKILL.md",
    "Research": ".agents/skills/research/SKILL.md",
    "Data": ".agents/skills/data/SKILL.md",
}

AGENT_LABELS = {agent: f"agent:{agent.lower()}" for agent in AGENTS if agent != "Unassigned"}
SKILL_LABELS = {skill: f"skill:{skill.lower()}" for skill in SKILLS if skill != "Unassigned"}

#: Retired Claude-queue labels. They are listed so the dispatcher can prove
#: it ignores them; they must never regain routing meaning.
LEGACY_LABELS = frozenset(
    {
        "dispatch:claude",
        "dispatch:codex",
        "fix:claude",
        "claude:working",
        "review:needed",
        "ready:merge",
        "needs:julia",
        "blocked",
    }
)

_HEADING_RE = re.compile(rf"^##[ \t]+{re.escape(STATUS_HEADING)}[ \t]*$", re.M)
_NEXT_HEADING_RE = re.compile(r"^##[ \t]+", re.M)
_FIELD_RE = re.compile(r"^\*\*(?P<key>[A-Za-z]+):\*\*[ \t]*(?P<value>.*?)[ \t]*$")


def _block_bounds(body: str) -> tuple[int, int] | None:
    heading = _HEADING_RE.search(body or "")
    if heading is None:
        return None
    start = heading.end()
    following = _NEXT_HEADING_RE.search(body, start)
    return start, following.start() if following else len(body)


def canonical(value: str, allowed: tuple[str, ...]) -> str | None:
    """Return the canonical spelling of ``value``, or ``None`` if unknown.

    Matching is case- and whitespace-insensitive so ``in progress`` and
    ``In Progress`` are the same state, but an unrecognised word is never
    coerced into the nearest canonical value -- guessing what the CTO meant
    is exactly the decision automation is not allowed to make.
    """
    folded = " ".join((value or "").split()).casefold()
    for candidate in allowed:
        if candidate.casefold() == folded:
            return candidate
    return None


def parse_status(body: str) -> dict[str, str] | None:
    """Read the status block, or ``None`` when the issue has no usable one."""
    bounds = _block_bounds(body or "")
    if bounds is None:
        return None
    start, end = bounds
    found: dict[str, str] = {}
    for line in body[start:end].splitlines():
        match = _FIELD_RE.match(line.strip())
        if match is None:
            continue
        key = match.group("key")
        if key in FIELDS and key not in found:
            found[key] = match.group("value").strip()
    if "State" not in found:
        return None
    return {field: found.get(field, "") for field in FIELDS}


def apply_status(body: str, updates: dict[str, str]) -> str:
    """Rewrite known fields inside the status block, leaving everything else.

    A field absent from the block is not created: an issue whose block has
    been hand-edited into a different shape is reported as-is rather than
    silently reformatted underneath the author.
    """
    bounds = _block_bounds(body or "")
    if bounds is None or not updates:
        return body
    start, end = bounds
    block = body[start:end]
    remaining = dict(updates)

    out_lines = []
    for line in block.splitlines(keepends=True):
        match = _FIELD_RE.match(line.strip())
        if match is not None:
            key = match.group("key")
            if key in remaining:
                newline = "\n" if line.endswith("\n") else ""
                out_lines.append(f"**{key}:** {remaining.pop(key)}{newline}")
                continue
        out_lines.append(line)
    return body[:start] + "".join(out_lines) + body[end:]


def skill_file(skill: str) -> str | None:
    """Path to the authoritative repo-local definition for an execution skill."""
    return SKILL_FILES.get(skill)


def routing_labels(labels: list[str]) -> tuple[list[str], list[str]]:
    """Split issue labels into the agent and skill routing labels present."""
    names = [str(name).strip().casefold() for name in labels or []]
    agents = [label for agent, label in AGENT_LABELS.items() if label in names]
    skills = [label for skill, label in SKILL_LABELS.items() if label in names]
    return sorted(agents), sorted(skills)
