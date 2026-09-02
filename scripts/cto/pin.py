#!/usr/bin/env python3
"""Pin (or re-pin) a repository's caller workflows to one ai-dispatch commit.

    pin.py <repo-dir> <40-char sha> [--setup CMD] [--verify CMD] [--ci ci.yml|""]

Rewrites every `devsoliadata-creator/ai-dispatch/.github/workflows/*.yml@<ref>`
to `@<sha>` and sets `dispatch_ref: <sha>` in the `with:` block of each job
that calls one (creating the block when absent), in
.github/workflows/ai-dispatch.yml and ci.yml. The optional flags set the
per-repo inputs on the dispatch caller at the same time. Idempotent.
"""
from __future__ import annotations

import pathlib
import re
import sys

SHARED = "devsoliadata-creator/ai-dispatch/.github/workflows/"
USES_RE = re.compile(rf"^(\s*)uses:\s*{re.escape(SHARED)}([\w-]+)\.yml@(\S+)\s*$")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def pin_text(text: str, sha: str, inputs: dict[str, str]) -> str:
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        m = USES_RE.match(line)
        if not m:
            out.append(line); i += 1; continue
        ind = len(m.group(1))
        workflow = m.group(2)
        out.append(f"{m.group(1)}uses: {SHARED}{workflow}.yml@{sha}\n")
        if workflow == "labels":  # takes no inputs at all
            i += 1; continue
        # the job's remaining sibling keys: lines until a non-blank line with indent <= ind-2 (parent) or == ind that starts a new key we pass through
        j = i + 1
        wanted = {"dispatch_ref": sha, **(inputs if workflow == "dispatch" else {})}
        rendered = [f"{' ' * (ind + 2)}{k}: {v}\n" if k == "dispatch_ref" else f"{' ' * (ind + 2)}{k}: \"{v}\"\n" for k, v in wanted.items()]
        found_with = False
        while j < len(lines):
            cur = lines[j]
            if cur.strip() and _indent(cur) < ind:
                break  # left the job
            if cur.strip() and _indent(cur) == ind:
                if cur.strip() == "with:":
                    found_with = True
                    out.append(cur); j += 1
                    # children
                    while j < len(lines) and (not lines[j].strip() or _indent(lines[j]) > ind):
                        child = lines[j]
                        key = re.match(r"\s*([\w-]+):", child)
                        if key and key.group(1) in wanted:
                            j += 1; continue  # replaced below
                        if not child.strip():
                            break  # keep blank line after the block
                        out.append(child); j += 1
                    out.extend(rendered)
                    continue
                out.append(cur); j += 1; continue
            out.append(cur); j += 1
        if not found_with:
            # insert directly after the uses line (already appended): find its index
            idx = len(out) - (j - i - 1)
            out[idx:idx] = [f"{' ' * ind}with:\n", *rendered]
        i = j
    return "".join(out)


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(__doc__); return 2
    repo, sha = pathlib.Path(argv[1]), argv[2]
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        print(f"not a 40-char sha: {sha}"); return 2
    flags = {"--setup": "setup_command", "--verify": "verify_command", "--ci": "ci_workflow"}
    inputs: dict[str, str] = {}
    args = argv[3:]
    while args:
        if args[0] not in flags or len(args) < 2:
            print(f"bad flag {args[0]}"); return 2
        inputs[flags[args[0]]] = args[1]; args = args[2:]
    changed = []
    for name in ("ai-dispatch.yml", "ci.yml"):
        path = repo / ".github" / "workflows" / name
        if not path.is_file():
            continue
        before = path.read_text(encoding="utf-8")
        after = pin_text(before, sha, inputs if name == "ai-dispatch.yml" else {})
        if after != before:
            path.write_text(after, encoding="utf-8"); changed.append(str(path))
    print("\n".join(changed) if changed else "already pinned")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
