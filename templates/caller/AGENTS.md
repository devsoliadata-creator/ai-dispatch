# Repository instructions for AI workers

Work in this repository is dispatched from feature control issues by the shared
layer in `devsoliadata-creator/ai-dispatch` (see its README for the protocol).

## Authority

- JM owns product/business goals, money, credentials, production and destructive actions.
- ChatGPT CTO is the technical authority: architecture, priority, scope, standards, worker/skill assignment.
- Claude (and any other worker) executes recorded assignments. A worker never reassigns itself, re-scopes, merges, deploys, or approves its own work.

## Required context

Before significant work read `AGENTS.md`, `CLAUDE.md` if present, the active control issue and all its comments, and the linked PR.

## Hard boundaries

- Never commit secrets, client data, or credentials.
- Never push to `main`; work on `claude/*` branches only.
- Never merge, deploy, publish, or change live systems without explicit authorization.

## Verification

Run the verify command named in the mission before opening or updating a PR, and report exactly what ran and what it returned.
