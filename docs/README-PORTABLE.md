# AI dispatch — portable package

Everything needed to run the issue → GPT (CTO) → Claude (builder) → merge → deploy pipeline on any Mac and any GitHub repo you own. Nothing here contains a secret.

## What is in the zip

| Path | What it is |
|---|---|
| `.github/workflows/*.yml` | The shared reusable workflows (dispatch, pr-sync, cto-verdict, ci, ci-report, labels). Live copy: github.com/devsoliadata-creator/ai-dispatch |
| `scripts/dispatch/` | The tested Python that routes issues, builds missions, relays verdicts |
| `scripts/cto/` | Mac tooling: `cto` CLI, reviewer (`cto-review.sh`), `install.sh`, `onboard-repo.sh`, `pin.py`, **`Fix CTO reviewer.command`** (double-click repair) |
| `templates/caller/` | The thin files a repo needs: `ai-dispatch.yml`, `ci.yml`, `AGENTS.md`, issue + PR templates |
| `.agents/skills/`, `agents/` | Worker skills (build/debug/review/qa/research/data) and default sub-agents |
| `docs/CHATGPT-CTO-PROMPT.md` | GPT CTO persona — Custom GPT instructions and the Mac reviewer's prompt |
| `docs/CHATGPT-AGENT-TASK.md` | The ChatGPT scheduled agent task (approve-and-run, label, merge) |
| `docs/CTO-KNOWLEDGE.example.md` | personal_assistant's tech-stack invariants — the per-repo knowledge file pattern |
| `docs/AUTOMATED-DISPATCH.md` | How the state machine works (Ready → In Progress → Review → Done / Blocked) |
| `skills/ai-dispatch-ops/SKILL.md` | The Claude skill capturing the operating workflow |

## Set up a new Mac (once)

1. Install prerequisites: Homebrew, then `brew install gh jq`, `gh auth login`, `npm install -g @anthropic-ai/claude-code`, run `claude` once and sign in with the Max account.
2. `git clone https://github.com/devsoliadata-creator/ai-dispatch ~/dev/ai-dispatch`
3. `claude setup-token` → save it: `printf '%s' '<token>' > ~/.cto/claude_token && chmod 600 ~/.cto/claude_token`
4. Double-click `~/dev/ai-dispatch/scripts/cto/Fix CTO reviewer.command`. That installs the 5-minute reviewer job and verifies everything.

## Connect a repo (once per repo)

`cto onboard owner/repo` → merge the PR it opens → install the Claude GitHub App on the repo (github.com/apps/claude). Never onboard a fork (the token secret would be exposed to upstream). Copy `docs/CTO-KNOWLEDGE.example.md` to the repo's `docs/CTO-KNOWLEDGE.md` and edit the invariants.

## Daily use

- `cto new "what you want"` — GPT-style mission → control issue → Claude worker dispatched.
- `cto list` — everything in flight; `cto status N` — one issue.
- Verdicts: the Mac reviewer posts `CTO: APPROVE | REWORK skill=X | BLOCK …` on PRs labelled `cto:review`; you can post the same line yourself to override.
- Merge: the ChatGPT agent task (or you) merges `cto:approved` PRs; the issue goes Done and the deploy workflow starts where configured.
- `cto bump` re-pins every repo to the current ai-dispatch commit after you change the shared layer.

## Where the secrets live

`~/.cto/claude_token` (Mac, chmod 600) → pushed to each repo as `CLAUDE_CODE_OAUTH_TOKEN` by onboarding. VPS access lives only in the repo's `production` environment secrets (`VPS_HOST`, `VPS_USER`, `VPS_SSH_KEY`).
