---
name: debug
description: Diagnose and fix one reproducible defect using boundary-first root-cause analysis. Use when existing behavior fails or its cause is unknown.
metadata:
  short-description: Root-cause defect resolution
  execution-skill: Debug
  github-label: "skill:debug"
  worker-model: opus
  worker-effort: high
  worker-access: write
  worker-max-turns: 150
---

# Debug

Follow this evidence chain:

1. Reproduce the failure.
2. Isolate the smallest failing case.
3. Add a canary or targeted observation.
4. Identify the first failing boundary.
5. Establish the root cause.
6. Apply the smallest structural fix.
7. Add a regression test that fails for the original reason and passes with the fix.

Run the focused proof, then the repository verify command named in the mission. Return root cause, changed files, commands/results, evidence, risks, and commit SHA/PR. Do not substitute retries, suppression, or symptom patches for a demonstrated cause without CTO approval.
