---
name: research
description: Answer one bounded technical or product question with traceable evidence and a decision-ready recommendation. Use for investigation only; implementation requires a separate mission.
metadata:
  short-description: Bounded evidence-based research
  execution-skill: Research
  github-label: "skill:research"
  worker-model: sonnet
  worker-effort: medium
  worker-access: read-only
  worker-max-turns: 60
---

# Research

State the question, inspect the smallest relevant source set, distinguish fact from inference, and return:

- answer and evidence;
- uncertainty or conflicting evidence;
- practical options and tradeoffs;
- recommended smallest safe option;
- scope impact.

Prefer primary sources for technical claims. Do not modify runtime code or silently turn the research into implementation.
