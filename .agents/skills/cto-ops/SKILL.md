---
name: cto-ops
description: Support the ChatGPT CTO operating workflow by inspecting engineering state, reconciling metadata, applying recorded CTO decisions, or preparing a CTO handoff. Loading this skill never grants technical decision authority.
metadata:
  short-description: ChatGPT CTO workflow support
  decision-authority: ChatGPT CTO
---

# CTO Ops

Support the engineering control plane without transferring CTO authority to the invoker.

## Authority boundary

ChatGPT CTO alone makes final technical decisions about architecture, priority, sequencing, scope, standards, technical sufficiency, and worker/skill assignment.

A non-CTO worker may:

- inspect the feature issue, Project fields, linked PR, and repository evidence;
- reconcile status metadata to a decision already recorded by ChatGPT CTO;
- prepare options, evidence, or a decision-ready CTO handoff;
- execute an assignment already recorded in the control issue, `DECISIONS.md`, or an explicit CTO dispatch.

A non-CTO worker must not claim a CTO decision, choose or materially change architecture, rescope the mission, assign itself, or dispatch another worker merely because this skill was loaded. When direction is not already recorded, set `Next: CTO decision` and stop expansion.

## Reconcile the live state

Keep the feature issue's stable status block aligned with durable evidence:

- State
- Agent
- Skill
- PR
- Blocker
- Next

Use one feature control issue and one active implementation PR by default. Keep Blocker to one short current blocker.

## Architecture challenge

When evidence materially challenges the approved direction, prepare this handoff without applying the proposed change:

```text
Current assumption:
New evidence:
Proposed change:
Smallest safe option:
Scope impact:
```

Wait for a recorded CTO decision before materially expanding or redirecting the mission.

## Completion

For implementation work, require evidence appropriate to the assigned execution skill and the repository verify command named in the mission. Escalate to Julia only for product/business decisions, money, credentials, consequential production/destructive actions, or real-world commitments.
