"""Which Claude configuration a skill runs on.

Every execution skill names its own worker model and effort in the
frontmatter of its repo-local ``SKILL.md``::

    metadata:
      worker-model: opus
      worker-effort: medium
      worker-access: write

That file is the single authority for *how* a skill works, so it is also
where the CTO records *what* runs it and *what it may touch*. The dispatcher
reads the three values and hands them to the worker as command-line
arguments and a permissions settings file; nothing about model selection or
tool access lives in workflow YAML, and a skill with no recorded routing is
a configuration error, never a silent fallback to whatever the action's
default happens to be.

``worker-access`` is the tool profile. ``write`` lets the worker edit files,
run the test suite, push a branch, open or update its PR, and hand the
mission back. ``read-only`` (Review, Research) can read, diff, run checks
and post findings, but cannot edit files or push. No profile grants a
general-purpose interpreter, package manager, or command runner: Python is
reachable only through the named entry points a mission needs.

These lists keep a worker inside its role; they are not a sandbox. A
``write`` worker that can create a test file and run the suite can execute
arbitrary code on the runner, so the enforceable boundary is what the
runner itself can reach -- the job's GitHub permissions and the absence of
any production secret -- and that is documented, not the deny-list.
"""

from __future__ import annotations

import pathlib
import re
from typing import Any

from .status import SKILL_FILES

#: Where the shared default skill files live when the target repository has
#: no ``.agents/skills/<skill>/SKILL.md`` of its own: the ai-dispatch checkout
#: (this package's own repository root). A repository overrides a skill by
#: committing its own file at the same relative path.
DEFAULTS_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: Model aliases Claude Code resolves to the current model of that tier.
#: A full model id (``claude-…``) is accepted too, for a deliberate pin.
MODEL_ALIASES = ("opus", "sonnet", "haiku")
_MODEL_ID_RE = re.compile(r"claude-[a-z0-9][a-z0-9.-]*")

#: The effort levels the Claude Code CLI accepts for ``--effort``.
EFFORTS = ("low", "medium", "high", "xhigh", "max")

MODEL_KEY = "worker-model"
EFFORT_KEY = "worker-effort"
ACCESS_KEY = "worker-access"
ACCESS_LEVELS = ("write", "read-only")
#: Optional hard cap on agentic turns. A worker that loops burns the whole
#: subscription window; the cap ends the run and the reconcile step blocks
#: the feature truthfully instead.
MAX_TURNS_KEY = "worker-max-turns"

#: Tools every worker may use without a prompt. Reading, searching, and the
#: bounded git/gh/python commands a mission needs to inspect and verify.
_READ_TOOLS = (
    "Read",
    "Glob",
    "Grep",
    "LS",
    # Sub-agents (``.claude/agents/*.md``) run under this same permission
    # profile, so a worker may fan out review/QA/research without widening
    # what the run can touch.
    "Agent",
    "Bash(git status:*)",
    "Bash(git log:*)",
    "Bash(git diff:*)",
    "Bash(git show:*)",
    "Bash(git branch:*)",
    "Bash(git fetch:*)",
    "Bash(git worktree list:*)",
    "Bash(ls:*)",
    "Bash(cat:*)",
    "Bash(head:*)",
    "Bash(tail:*)",
    "Bash(wc:*)",
    "Bash(grep:*)",
    # Python only through the named entry points a mission needs. Never the
    # bare interpreter: `python3 -c` or `python3 <any file>` is an
    # unrestricted shell, and so is anything that installs packages.
    "Bash(python3 -m pytest:*)",
    "Bash(python3 -m unittest:*)",
    "Bash(python3 -m scripts.dispatch complete:*)",
    "Bash(gh pr view:*)",
    "Bash(gh pr diff:*)",
    "Bash(gh pr checks:*)",
    "Bash(gh pr list:*)",
    "Bash(gh pr comment:*)",
    "Bash(gh pr review:*)",
    "Bash(gh issue view:*)",
    "Bash(gh issue comment:*)",
    "Bash(gh run view:*)",
)

#: Added for ``write`` skills: editing, committing, pushing, and opening or
#: updating the single implementation PR. Still no deploy, publish,
#: workflow-run, secret, or merge command.
_WRITE_TOOLS = (
    "Edit",
    "MultiEdit",
    "Write",
    "NotebookEdit",
    "Bash(git add:*)",
    "Bash(git commit:*)",
    "Bash(git checkout:*)",
    "Bash(git switch:*)",
    "Bash(git stash:*)",
    "Bash(git push:*)",
    "Bash(git worktree add:*)",
    "Bash(mkdir:*)",
    "Bash(cp:*)",
    "Bash(mv:*)",
    "Bash(touch:*)",
    "Bash(gh pr create:*)",
    "Bash(gh pr edit:*)",
    "Bash(gh pr ready:*)",
)

#: Never allowed for any dispatched worker, whatever the profile says.
_DENIED_TOOLS = (
    # consequential GitHub actions
    "Bash(gh pr merge:*)",
    "Bash(gh workflow:*)",
    "Bash(gh run rerun:*)",
    "Bash(gh run cancel:*)",
    "Bash(gh secret:*)",
    "Bash(gh variable:*)",
    "Bash(gh release:*)",
    "Bash(gh api:*)",
    "Bash(git push --force:*)",
    "Bash(git push -f:*)",
    "Bash(git push origin main:*)",
    "Bash(git push origin HEAD:main:*)",
    # deploy, remote access, network
    "Bash(bash scripts/deploy.sh:*)",
    "Bash(scripts/deploy.sh:*)",
    "Bash(ssh:*)",
    "Bash(scp:*)",
    "Bash(rsync:*)",
    "Bash(docker:*)",
    "Bash(curl:*)",
    "Bash(wget:*)",
    # general-purpose interpreters, package managers and command runners:
    # each is an unrestricted shell in disguise
    "Bash(python3 -c:*)",
    "Bash(python -c:*)",
    "Bash(python3 -m pip:*)",
    "Bash(python -m pip:*)",
    "Bash(pip:*)",
    "Bash(pip3:*)",
    "Bash(uv:*)",
    "Bash(node:*)",
    "Bash(npm:*)",
    "Bash(npx:*)",
    "Bash(bun:*)",
    "Bash(perl:*)",
    "Bash(ruby:*)",
    "Bash(bash:*)",
    "Bash(sh:*)",
    "Bash(zsh:*)",
    "Bash(env:*)",
    "Bash(eval:*)",
    "Bash(xargs:*)",
    "Bash(sed:*)",
    "Bash(awk:*)",
    "Bash(find:*)",
    "Bash(rg:*)",
    "Bash(git rebase:*)",
    "Bash(git -c:*)",
    # the repository's own git plumbing: a hook written here would run on
    # the next allowed `git commit`
    "Edit(.git/**)",
    "MultiEdit(.git/**)",
    "Write(.git/**)",
)

#: The only shapes a ``Bash(...)`` allow entry may take. Every entry must be
#: one of these binaries followed by a fixed subcommand or entry point; the
#: regression tests enforce it so a later edit cannot quietly re-open a
#: general interpreter.
_ALLOWED_BASH_PREFIXES = (
    "git ",
    "gh ",
    "ls",
    "cat",
    "head",
    "tail",
    "wc",
    "grep",
    "mkdir",
    "cp",
    "mv",
    "touch",
    "python3 -m pytest",
    "python3 -m unittest",
    "python3 -m scripts.dispatch complete",
)

#: The shapes a repository's ``verify_command`` may take. One fixed test /
#: lint entry point per repository, declared in its caller workflow, never a
#: bare interpreter or a package-manager install. The command is allowed
#: verbatim (``Bash(<command>:*)``); anything else is a configuration error.
_VERIFY_RE = re.compile(
    r"^(?:"
    r"python3 -m (?:pytest|unittest)"
    r"|python3 [A-Za-z0-9_./-]+\.py"
    r"|pytest"
    r"|(?:npm|pnpm|yarn|bun) (?:test|run [A-Za-z0-9_:-]+)"
    r"|make [A-Za-z0-9_-]+"
    r"|dotnet test"
    r"|go test"
    r"|cargo test"
    r")(?: [A-Za-z0-9_./=:-]+)*$"
)

#: Deny entries lifted only when the repository's verify command needs that
#: binary. The allow entry is still the exact command, so ``npm test`` does
#: not open ``npm install``.
_VERIFY_BINARY_DENY = {
    "npm": "Bash(npm:*)",
    "pnpm": "Bash(pnpm:*)",
    "yarn": "Bash(yarn:*)",
    "bun": "Bash(bun:*)",
    "cargo": "Bash(cargo:*)",
    "go": "Bash(go:*)",
    "dotnet": "Bash(dotnet:*)",
}


def validate_verify_command(command: str | None) -> str:
    """Return the normalised verify command, or raise for an unsafe shape."""
    command = " ".join((command or "").split())
    if not command:
        return ""
    if not _VERIFY_RE.fullmatch(command):
        raise RoutingError(
            f"verify_command {command!r} is not an approved shape "
            "(one fixed test/lint entry point, e.g. `python3 scripts/validate.py`, `npm test`)"
        )
    return command


def is_approved_command(command: str, verify: str = "") -> bool:
    """Whether a ``Bash(<command>:*)`` allow entry is one of the fixed shapes."""
    if verify and command == verify:
        return True
    return any(command == prefix.rstrip() or command.startswith(prefix) for prefix in _ALLOWED_BASH_PREFIXES)


class RoutingError(ValueError):
    """A skill file has no usable worker routing."""


def _frontmatter(text: str) -> str:
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        return ""
    return text.split("\n---\n", 1)[0][4:]


def _metadata_value(frontmatter: str, key: str) -> str:
    # The metadata block is a flat mapping of scalars indented under
    # ``metadata:``; a regex is enough and keeps this standard-library-only.
    match = re.search(rf"^[ \t]+{re.escape(key)}:[ \t]*(.+?)[ \t]*$", frontmatter, re.M)
    return match.group(1).strip().strip("\"'") if match else ""


def parse_worker_routing(text: str, *, source: str = "skill file") -> dict[str, str]:
    """Extract and validate ``worker-model`` / ``worker-effort`` from a SKILL.md."""
    fm = _frontmatter(text)
    model = _metadata_value(fm, MODEL_KEY).lower()
    effort = _metadata_value(fm, EFFORT_KEY).lower()
    problems = []
    if not model:
        problems.append(f"missing `{MODEL_KEY}`")
    elif model not in MODEL_ALIASES and not _MODEL_ID_RE.fullmatch(model):
        problems.append(
            f"`{MODEL_KEY}: {model}` is not one of {', '.join(MODEL_ALIASES)} or a claude-* model id"
        )
    if not effort:
        problems.append(f"missing `{EFFORT_KEY}`")
    elif effort not in EFFORTS:
        problems.append(f"`{EFFORT_KEY}: {effort}` is not one of {', '.join(EFFORTS)}")
    access = _metadata_value(fm, ACCESS_KEY).lower()
    if not access:
        problems.append(f"missing `{ACCESS_KEY}`")
    elif access not in ACCESS_LEVELS:
        problems.append(f"`{ACCESS_KEY}: {access}` is not one of {', '.join(ACCESS_LEVELS)}")
    max_turns = _metadata_value(fm, MAX_TURNS_KEY)
    if max_turns and not (max_turns.isdigit() and 0 < int(max_turns) <= 500):
        problems.append(f"`{MAX_TURNS_KEY}: {max_turns}` is not an integer between 1 and 500")
    if problems:
        raise RoutingError(f"{source}: " + "; ".join(problems))
    routing = {"model": model, "effort": effort, "access": access}
    if max_turns:
        routing["max_turns"] = max_turns
    return routing


def resolve_skill_file(
    skill: str, root: pathlib.Path | str, defaults: pathlib.Path | str | None = None
) -> tuple[pathlib.Path, str]:
    """The skill file that governs ``skill`` for this repository.

    A file committed in the repository wins; otherwise the shared default
    from the ai-dispatch checkout applies. Returns the absolute path and the
    path a worker should be told to load (relative to the repository root).
    """
    relative = SKILL_FILES.get(skill)
    if relative is None:
        raise RoutingError(f"{skill!r} has no skill file")
    root = pathlib.Path(root)
    local = root / relative
    if local.is_file():
        return local, relative
    defaults = pathlib.Path(defaults) if defaults is not None else DEFAULTS_ROOT
    shared = defaults / relative
    if shared.is_file():
        try:
            shown = str(shared.resolve().relative_to(root.resolve()))
        except ValueError:
            shown = str(shared)
        return shared, shown
    raise RoutingError(f"{relative}: cannot read (not in the repository, no shared default)")


def read_worker_routing(
    skill: str, root: pathlib.Path | str, defaults: pathlib.Path | str | None = None
) -> dict[str, str]:
    """The routing recorded for one canonical skill, read from the repo or the defaults."""
    path, shown = resolve_skill_file(skill, root, defaults)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RoutingError(f"{shown}: cannot read ({exc})") from exc
    routing = parse_worker_routing(text, source=shown)
    routing["skill_file"] = shown
    return routing


def read_all_worker_routing(
    root: pathlib.Path | str, defaults: pathlib.Path | str | None = None
) -> dict[str, dict[str, str]]:
    """Routing for every canonical skill, keyed by skill name."""
    return {skill: read_worker_routing(skill, root, defaults) for skill in SKILL_FILES}


def claude_args(routing: dict[str, Any]) -> str:
    """The extra CLI arguments the Claude worker step receives."""
    args = f"--model {routing['model']} --effort {routing['effort']}"
    if routing.get("max_turns"):
        args += f" --max-turns {routing['max_turns']}"
    return args


def allowed_tools(routing: dict[str, Any], verify: str = "") -> list[str]:
    tools = list(_READ_TOOLS)
    verify = validate_verify_command(verify)
    if verify:
        tools.append(f"Bash({verify}:*)")
    if routing.get("access") == "write":
        tools += _WRITE_TOOLS
    return tools


def denied_tools(verify: str = "") -> list[str]:
    verify = validate_verify_command(verify)
    lifted = _VERIFY_BINARY_DENY.get(verify.split(" ", 1)[0]) if verify else None
    return [tool for tool in _DENIED_TOOLS if tool != lifted]


def claude_settings(routing: dict[str, Any], verify: str = "") -> dict[str, Any]:
    """The Claude Code settings the worker runs under.

    Passed to the action as a settings file rather than ``--allowedTools``
    so that patterns containing spaces (``Bash(git diff:*)``) survive
    argument parsing intact.
    """
    return {
        "permissions": {
            "allow": allowed_tools(routing, verify),
            "deny": denied_tools(verify),
        }
    }
