---
name: review
description: Independently and adversarially review a mission or PR for technical sufficiency. Use for read-only technical review; fixes require a separate Build or Debug assignment.
metadata:
  short-description: Independent adversarial review
  execution-skill: Review
  github-label: "skill:review"
  worker-model: sonnet
  worker-effort: medium
  worker-access: read-only
  worker-max-turns: 40
---

# Review

Review the approved scope, diff, tests, evidence, and affected architecture. Challenge:

- scope creep and architecture violations;
- stale assumptions and unproven claims;
- over-engineering or needless abstractions;
- missing behavioral and negative tests;
- safety, security, privacy, concurrency, and recovery risks;
- user-visible gaps hidden by unit-test success.

## Where the findings go

Findings are worthless in a workflow log. Post them on the pull request:

1. Read the diff with `gh pr diff <n>` and the control issue with `gh issue view <n> --comments`.
2. Post ONE review with `gh pr review <n> --comment --body "..."` (never `--approve`: approval is the CTO's call). Title it `## Review`, list at most 8 findings ordered by severity — `blocker`, `should`, `nit` — each with `file:line`, what is wrong, the evidence, and the smallest correction.
3. End the review with exactly one of `Verdict: ready` or `Verdict: needs changes`. Say explicitly when no material finding remains.
4. Hand the mission back with `python3 -m scripts.dispatch complete --issue <control issue> --pull <n>`.

Do not modify the branch unless separately assigned.
