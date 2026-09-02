---
name: qa
description: Verify real user-visible behavior against acceptance criteria in the relevant environment. Use when behavior, integration, UX, or release evidence must be proven beyond unit tests.
metadata:
  short-description: User-visible behavior verification
  execution-skill: QA
  github-label: "skill:qa"
  worker-model: sonnet
  worker-effort: medium
  worker-access: write
  worker-max-turns: 100
---

# QA

Test the real workflow through supported user-facing or integration boundaries. Cover the happy path, the most important negative path, and the stated regression risk.

Record environment, inputs, observed outputs, screenshots/logs when useful, and pass/fail against each acceptance criterion. Distinguish product failure, environment blocker, and unverified behavior. Unit-test success alone is not QA evidence.

Do not implement fixes unless separately assigned. Keep one short current blocker visible when QA cannot proceed.
