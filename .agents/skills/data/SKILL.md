---
name: data
description: Curate, normalize, and verify a bounded structured dataset with provenance. Use for dataset work only; runtime architecture changes require a separate mission.
metadata:
  short-description: Verified structured data curation
  execution-skill: Data
  github-label: "skill:data"
  worker-model: sonnet
  worker-effort: medium
  worker-access: write
  worker-max-turns: 120
---

# Data

Treat the supplied schema and source dataset as the contract.

- Preserve canonical source facts and record provenance.
- Normalize only by explicit rules.
- Verify uncertain or volatile fields read-only.
- Never guess identifiers, slugs, URLs, operational status, or missing values.
- Validate schema, uniqueness, required fields, and deterministic output.
- Return records changed, verification evidence, unresolved fields, and validation results.

Do not change runtime behavior or architecture under a Data mission.
