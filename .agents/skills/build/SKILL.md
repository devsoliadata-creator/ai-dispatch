---
name: build
description: Implement one bounded, approved feature. Use only when behavior is specified and implementation is authorized; use Debug for an unexplained defect.
metadata:
  short-description: Bounded feature implementation
  execution-skill: Build
  github-label: "skill:build"
  worker-model: opus
  worker-effort: medium
  worker-access: write
  worker-max-turns: 150
---

# Build

Implement the feature within the control issue's approved scope and acceptance criteria.

- Work in the assigned isolated worktree.
- Preserve existing behavior outside scope.
- Deliver the smallest coherent implementation; avoid speculative abstractions and adjacent cleanup.
- Verify through the affected public behavior, then run the repository verify command named in the mission.
- Return changed files, commands/results, evidence, risks, and commit SHA/PR.

Do not silently broaden the mission. If new evidence materially challenges the approved architecture, stop expansion and use the architecture-challenge report in `AI_WORKFLOW.md`.
