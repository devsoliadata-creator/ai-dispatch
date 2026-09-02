# ai-dispatch

One shared GitHub → Claude dispatch layer for every repository. A feature
control issue written by ChatGPT (as CTO) is executed by Claude Code inside
GitHub Actions; the worker opens a PR, can fan out to sub-agents, and the
reviewer posts findings on the PR. Nothing here chooses work: it executes
recorded assignments (see `docs/AUTOMATED-DISPATCH.md` for every rule).

## Layout

| Path | Role |
|---|---|
| `.github/workflows/dispatch.yml` | reusable: decide, claim, invoke Claude, record outcome |
| `.github/workflows/pr-sync.yml` | reusable: keep the control issue's PR/State in sync; post the CTO review packet |
| `.github/workflows/cto-verdict.yml` | reusable: relay a pasted `CTO:` verdict to the control issue |
| `.github/workflows/labels.yml` | reusable: create the `agent:*` / `skill:*` labels |
| `.github/workflows/ci-report.yml` | reusable: publish a dispatched CI run's verdict (commit status + control issue) |
| `scripts/dispatch/` | all routing, validation, idempotency rules (stdlib only) |
| `.agents/skills/<skill>/SKILL.md` | default skill definitions incl. worker model / effort / access |
| `agents/*.md` | default sub-agents copied into a repo's `.claude/agents/` when it has none |
| `templates/caller/` | what each target repository copies |
| `docs/CHATGPT-CTO-PROMPT.md` | the ChatGPT instructions that produce dispatchable issues |
| `tests/` | the proof (`python -m pytest tests`) |

## Add a repository (5 minutes)

1. Copy `templates/caller/.github/workflows/ai-dispatch.yml` into the repo and
   set `verify_command` to its one test entry point (or leave `""`).
   Copy `templates/caller/.github/workflows/ci.yml` too (or add its
   `workflow_dispatch` inputs, SHA check and `report` job to your existing CI):
   a PR opened by the worker gets no `pull_request` run, so dispatch triggers
   this CI on the exact head SHA and it reports back as the `canonical-ci`
   status. Set `ci_workflow: ""` in the caller to skip that gate.
2. Copy `templates/caller/.github/ISSUE_TEMPLATE/feature.md`,
   `templates/caller/.github/pull_request_template.md`, and
   `templates/caller/AGENTS.md` (edit AGENTS.md if the repo has extra rules).
3. Repo → Settings → Secrets → Actions: add `CLAUDE_CODE_OAUTH_TOKEN`
   (from `claude setup-token` on any machine logged into your Max plan).
4. Actions → **AI dispatch** → Run workflow once (any issue number, e.g. `1`):
   this creates the labels.
5. Install the Claude GitHub App on the repo: https://github.com/apps/claude

Optional per-repo overrides: commit `.agents/skills/<skill>/SKILL.md` to change
a skill's model/effort/instructions, or `.claude/agents/*.md` to replace the
default sub-agents. Repo-local files win over the shared defaults.

## Private or public?

The reusable workflows check this repo out with the caller's `GITHUB_TOKEN`,
which only works if this repo is **public** (it contains no secrets or client
data). To keep it private, create a fine-grained PAT with *Contents: read* on
this repo and add it to each caller as the secret `DISPATCH_REPO_TOKEN`.

## Fully automatic GPT review from your Mac (recommended)

ChatGPT Free has no API, but **Codex is included on the Free plan** and its
CLI signs in with your ChatGPT account. `scripts/cto/` uses it:

| Script | What it does |
|---|---|
| `scripts/cto/install.sh` | one-time: checks `gh`/`codex`/`jq`, writes `~/.cto/config`, installs a launchd job (every 5 min) |
| `scripts/cto/cto-review.sh` | finds every open PR labeled `cto:review` across your repos, has Codex review the real checkout with the CTO prompt, posts findings + the `CTO:` verdict as PR comments under your login → the relay does the rest |
| `scripts/cto/cto-new.sh owner/repo "what you want"` | Codex writes the control issue from the CTO prompt and files it with the right labels |

Setup on the Mac:

```
npm install -g @openai/codex && codex login        # choose "Sign in with ChatGPT"
brew install gh jq && gh auth login
git clone https://github.com/devsoliadata-creator/ai-dispatch ~/dev/ai-dispatch
bash ~/dev/ai-dispatch/scripts/cto/install.sh
```

Success looks like: a PR labeled `cto:review` gets a "🤖 ChatGPT CTO review"
comment and a `CTO: …` comment within ~5 minutes of your Mac being awake; the
control issue then moves on its own. The manual paste loop below still works
whenever the Mac is off.

## Everyday flow

1. Ask ChatGPT (with `docs/CHATGPT-CTO-PROMPT.md` loaded) for the issue. Paste
   it into GitHub, add the two labels it names.
2. Within a minute the *AI dispatch* run claims the issue (`State: In Progress`),
   Claude works on a `claude/*` branch and opens a PR.
3. The PR gets a **CTO review packet** comment (title, body, files, diff).
   Copy it into ChatGPT. Paste ChatGPT's verdict block back as a PR comment:
   `CTO: APPROVE` / `CTO: REWORK ...` / `CTO: BLOCK ...`.
4. APPROVE → you merge; the issue closes to Done on merge. REWORK → the notes
   land in the issue's `## Rework` section and Claude is re-dispatched with
   them first in its mission. BLOCK → the issue waits for you.
5. Optional independent Claude review before ChatGPT sees it: set
   `skill:review` + `State: Ready`; findings are posted on the PR.

Worker runs use your Claude subscription and the repo's GitHub Actions minutes
(2,000/month free for private repos).
