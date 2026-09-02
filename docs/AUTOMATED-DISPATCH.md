# Automated engineering dispatch

This layer executes a dispatch assignment ChatGPT CTO has already recorded.
It chooses nothing. When the assignment is incomplete, inconsistent, or the
selected worker has no callable lane, it reports that and stops rather than
inventing a decision or faking a dispatch.

## What Julia sees

The feature control issue, and only the feature control issue:

```markdown
## Current status

**State:** In Progress
**Agent:** Claude
**Skill:** Debug
**PR:** #43
**Blocker:** None
**Next:** Worker executing
```

Plus at most one `**Dispatch** — …` comment per feature, rewritten in place.
Nothing else about worktrees, run IDs, claim keys, or workflow internals ever
reaches the issue.

## Execution lanes that actually exist

| Worker | Lane today | How it is invoked |
|---|---|---|
| Claude | Automatic **when a credential is configured** | `anthropics/claude-code-action@v1`, gated on `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN` being present |
| Codex | **Manual** | No programmatic Codex invocation exists for this repository |
| Local | **Manual** | No self-hosted runner or local-agent bridge exists |

A lane is `automatic` only when something can genuinely be called. With no
Claude credential configured, *every* lane is manual and the automation says
so on the issue — it never reports a worker as executing when none is.

Codex and Local keep the full routing contract. Set the repository variable
`CODEX_DISPATCH_LANE` or `LOCAL_DISPATCH_LANE` to `automatic` **only after**
adding a real invocation step to `.github/workflows/dispatch.yml (shared)`.
Flipping the variable without the step is caught: the run marks the feature
`Blocked` with `Blocker: <Agent> invocation failed` instead of leaving it
looking dispatched.

## Which Claude runs which skill

Each skill is a different Claude worker: the model and effort level are
recorded in the skill's own file, next to its instructions, so the role is
defined in exactly one place.

```yaml
# .agents/skills/build/SKILL.md
metadata:
  worker-model: opus
  worker-effort: medium
  worker-access: write
```

| Skill | Worker | Access | Why |
|---|---|---|---|
| Build | Claude **opus**, medium effort | write | implementation with judgment calls |
| Debug | Claude **opus**, high effort | write | root-cause work rewards deeper reasoning |
| Review | Claude **sonnet**, medium effort | read-only | adversarial review returns findings, never edits |
| QA | Claude **sonnet**, medium effort | write | may add regression tests and evidence |
| Research | Claude **sonnet**, medium effort | read-only | evidence gathering |
| Data | Claude **sonnet**, medium effort | write | curation against a fixed contract |

The decide step reads the assigned skill's file and hands the worker
`--model <model> --effort <effort>` (`claude_args` on the Claude step) plus
a permissions settings file (`settings`) built from `worker-access`; the
mission also states all three under `WORKER` so the run is self-describing.

`worker-model` accepts the aliases `opus`, `sonnet`, `haiku` (always the
current model of that tier) or a full `claude-…` id for a deliberate pin;
`worker-effort` is one of `low`, `medium`, `high`, `xhigh`, `max`;
`worker-access` is `write` or `read-only`.

**Tool access.** The action allows file edits by default but not `Bash`, so
a worker with no explicit permissions can edit files in the runner and then
cannot commit, push, open its PR, or run `scripts.dispatch complete` — the
work evaporates with the runner (this is what happened on the first live
Build dispatch). Each profile is therefore an explicit allow-list in
`scripts/dispatch/routing.py`:

- *read-only*: Read/Glob/Grep, `git diff/log/show/status`, the test suite
  and `scripts/validate.py`, `gh pr view/diff/review/comment`,
  `gh issue view/comment`, and the hand-back command.
- *write*: everything above plus Edit/Write, `git add/commit/checkout/
  stash/push`, `mkdir/cp/mv/touch`, and `gh pr create/edit/ready`.

No profile grants a general-purpose interpreter, package manager, or
command runner. Python is reachable only through four named entry points
(`python3 -m pytest`, `python3 -m unittest`, `python3 scripts/validate.py`,
`python3 -m scripts.dispatch complete`); `pip`, `python3 -c`, `node`,
`bash`, `sh`, `env`, `xargs`, `sed`, `awk`, `find`, `rg`, `git rebase` and
`git -c` are denied by name because each carries a "run this command" path.
Both profiles also deny `gh pr merge`, `gh workflow`, `gh run rerun`,
`gh secret`/`gh variable`, `gh api`, force-push, push to `main`,
`scripts/deploy.sh`, `ssh`/`scp`/`rsync`/`docker`/`curl`/`wget`, and any
edit under `.git/` (a hook planted there would run on the next allowed
`git commit`). A regression test pins all of this: an allow entry that is
not one of the approved binary+subcommand shapes fails the suite.

**What the lists are, and are not.** They keep a cooperative worker inside
its role and make an accidental over-reach fail loudly. They are **not a
sandbox**: a `write` worker that creates a test file and runs the suite is
executing arbitrary code on the runner, and Claude Code's permission
matcher only sees the outer command. The boundary that actually holds is
what the runner can reach:

- the job token has `contents`, `issues`, `pull-requests` write and nothing
  else — no `actions`, no `packages`, no environment;
- no production secret is available to the job; `deploy-vps.yml` is
  `workflow_dispatch`-only and its SSH key lives in the `production`
  environment, which this job never enters;
- pushes made with the job token trigger no workflows, so even a push to
  `main` cannot start a deploy;
- the worker's own credentials (`CLAUDE_CODE_OAUTH_TOKEN`, `GITHUB_TOKEN`)
  are in the runner environment and are the most valuable thing it can
  reach. They are scoped to this repository and this workflow run.

Known gap: this is a private repository on the GitHub Free plan, which does
not offer branch protection or rulesets, so `git push origin main` is
denied by the list but not enforced by GitHub. The cheapest hard fix is a
ruleset blocking direct pushes to `main` (GitHub Pro), which would also
protect against a human mistake. Until then, `PR: #n` in the control issue
plus CTO merge is the process control.

## Dispatch condition

All of these, or nothing happens:

- `State: Ready`
- `Agent` is one of Claude / Codex / Local (not `Unassigned`)
- `Skill` is one of Build / Debug / Review / QA / Research / Data (not `Unassigned`)
- `Blocker: None`
- exactly one `agent:*` label and exactly one `skill:*` label
- both labels agree with the status block
- no active dispatch claim for the same assignment

Legacy queue labels (`dispatch:claude`, `fix:claude`, `claude:working`, …)
carry no routing meaning. They cannot dispatch anything, and an issue that
has only legacy labels is reported as missing its routing labels.

## Idempotency

One `Ready` assignment produces one dispatch. Three mechanisms, in order:

1. **Workflow concurrency** — `feature-dispatch-<issue>` with
   `cancel-in-progress: false`. Simultaneous label and body-edit events queue
   instead of racing.
2. **State transition** — the claim and `State: In Progress` are written
   *before* the worker is invoked, so a later event no longer sees `Ready`.
3. **Durable claim** — one machine-readable record comment per feature:

   ```
   <!-- pa-dispatch:v1 {"key":"claude:debug","status":"dispatched",...} -->
   ```

   A record in status `dispatched` holds the claim. A replayed webhook, a
   re-run of the workflow, or a status-block edit all find the claim and stop
   even if they somehow read a stale `Ready` body. A record that cannot be
   parsed is treated as *held*, never as absent.

A claim is spent — and the same assignment may dispatch again — when it is
`released` (the feature reached Review/Blocked/Done, normal rework), `failed`
(the invocation failed), `abandoned` (the worker exited without handing the
mission back) or `ci-failed`. A deliberate retry of an assignment whose
claim is still active goes through **Actions → Feature dispatch → Run
workflow** with `force: true`.

A live claim blocks a new dispatch **whatever assignment it holds**. A
reassignment — Claude/Build to Codex/Review, or just a different skill — is
not an escape hatch: while the previous claim is `dispatched`, nothing new
goes out, because that would be a second worker running alongside the first.

Reassignment still works; it just has to cross an explicit boundary. Either
the claim is released the normal way (the feature reaches Review, Blocked or
Done), or the dispatch is re-run manually with `force: true` because someone
has decided the previous worker is gone.

## After the worker exits

The invocation step finishing is evidence that no worker is running any more,
not that one still is. So once it is over, `dispatch.yml (shared)` runs:

```bash
python3 -m scripts.dispatch reconcile --issue <n> --worker-outcome <success|failure|>
```

which compares what is recorded with what is now true:

| What the claim says | What is written |
|---|---|
| already released (the worker handed back) | nothing — `Review` and the linked PR stand |
| still `dispatched`, invocation succeeded | `Blocked` / `<agent> exited without handing the mission back` |
| still `dispatched`, invocation failed | `Blocked` / `<agent> invocation failed` |
| still `dispatched`, no invocation ran | `Blocked` / `<agent> invocation failed`, saying no lane ran |
| still `dispatched`, feature already moved on | the stale claim is released; the state is left alone |

Every blocking outcome is one short line, with `Next: Retry dispatch`, and
spends the claim so a deliberate re-dispatch works. Stack traces and logs stay
in the workflow run. The step exits non-zero when it has to block a feature: a
green workflow run beside a stalled feature would be the same untruth in a
different place.

A feature therefore cannot sit at `In Progress / Worker executing` with nobody
executing.

## Completion

A worker hands its mission back explicitly:

```bash
python3 -m scripts.dispatch complete --issue <n> --pull <pr>
```

That sets `State: Review` / `Next: CTO review`, records the PR, and releases
the claim so rework can dispatch again. The generated mission tells every
worker to run it, and to edit the status block by hand to the same two values
if the command is not available to it. It never sets `Done`.

This is deliberate rather than inferred. Rework happens on an already-open,
non-draft PR, where a push while the worker is mid-change looks exactly like
a push that finishes the work — so `pr-sync.yml (shared)` does not listen to
`synchronize` at all, and no push is read as a completion signal. Without the
explicit hand-back a reworked feature would sit at `In Progress / Worker
executing` forever.

## PR linking

`pr-sync.yml (shared)` reads `Control issue: #<n>` from the PR body (the
repository PR template already carries that line; GitHub closing keywords are
accepted as a fallback) and keeps `**PR:** #<n>` current. When the PR is open
and out of draft while the feature is `In Progress`, the feature moves to
`Review` / `Next: CTO review` and the claim is released.

The automation never marks a feature `Done`, never opens an issue from a PR,
and never creates a second PR.

## Canonical CI on a worker pull request head

A pull request opened or pushed by a workflow's own `GITHUB_TOKEN` starts no
`pull_request` run. Left alone, a worker-produced head would carry no gate at
all — so "canonical CI is green" would be unverifiable exactly where it
matters most. `workflow_dispatch` is GitHub's supported exception, and
`ci.yml` accepts it:

1. After the worker exits, `ci-plan` reads the feature's one linked PR and
   reports its **exact head SHA** — never the branch, which can move under a
   queued run.
2. A separate `canonical-ci` job runs `gh workflow run ci.yml --ref <default
   branch> -f sha=<head> -f pull=<n>`. It is a separate job because triggering
   a workflow needs `actions: write`, and the job holding the Anthropic
   credential must never be able to start arbitrary workflows. `--ref` is the
   default branch, so the workflow file that runs is this repository's, never
   the head's.
3. `ci.yml` checks out that exact commit, proves it did, and runs
   `python scripts/validate.py`. That job holds no write scope and no secret:
   it executes worker-authored tests.
4. A separate `report` job — which never checks out the head — publishes a
   `canonical-ci` commit status on the head SHA, so the verdict is visible on
   the PR and a red one is what a reviewer or a branch-protection rule sees.
   On failure it also runs `ci-result`, which moves the feature to `Blocked`
   with `Canonical CI failed on <sha>` while keeping the PR linked. A passing
   run says nothing beyond the green status; a feature in `Review` stays there
   for ChatGPT CTO.

`ci-result` never overwrites a state it did not author: `Done` is the CTO's
word, and an existing `Blocked` already says something truthful.

Permission map:

| Job | Permissions | Runs worker code? |
|---|---|---|
| `feature-dispatch / dispatch` (worker) | `contents`, `issues`, `pull-requests`, `id-token` write | yes |
| `feature-dispatch / canonical-ci` | `actions: write` only | no — no checkout |
| `ci / validate` | `contents: read` | yes |
| `ci / report` | `contents: read`, `statuses: write`, `issues: write` | no — base branch only |

## Files

| File | Role |
|---|---|
| `.github/workflows/dispatch.yml (shared)` | Trigger, concurrency, worker invocation, post-worker reconciliation, canonical-CI dispatch |
| `.github/workflows/ci.yml` | The canonical gate, on a PR, a push, or an exact worker head SHA |
| `.github/workflows/pr-sync.yml (shared)` | PR ⇄ control issue synchronisation |
| `.github/workflows/queue-bootstrap.yml` | Creates the `agent:*` / `skill:*` labels |
| `scripts/dispatch/status.py` | The `## Current status` block: parse and rewrite in place |
| `scripts/dispatch/mission.py` | The compact mission a worker receives |
| `scripts/dispatch/routing.py` | Reads each skill's `worker-model` / `worker-effort` / `worker-access`; builds the worker's tool permissions |
| `scripts/dispatch/dispatcher.py` | Every routing, validation, claim, reconciliation, PR-sync and CI rule |
| `scripts/dispatch/workflows.py` | Reads job/permission structure out of a workflow file, so the worker/CI split is checked, not described |
| `scripts/dispatch/github.py` | The six REST calls this layer makes |
| `scripts/dispatch/__main__.py` | The commands the workflows and workers run |
| `tests/test_dispatch.py` | Who may be dispatched — the proof, run by `python3 scripts/validate.py` |
| `tests/test_dispatch_lifecycle.py` | What is true after the worker exits: hand-back, missing hand-back, canonical CI |

There is no dispatcher service, no database, no queue and no scheduler. The
rules are pure functions; the workflows gather state and apply the answer.

## Third-party action pinning

The Claude invocation step receives an Anthropic credential plus
`contents: write`, `issues: write`, `pull-requests: write` and
`id-token: write`. A mutable tag would hand all of that to whoever can move
the tag, so the action is pinned to a full commit SHA, with the release it
corresponds to in a trailing comment.

To update it, resolve the intended release and take the dereferenced commit:

```bash
git ls-remote https://github.com/anthropics/claude-code-action refs/tags/<tag>
# use the line ending in ^{} -- that is the commit the tag points at
```

Review the diff from the current pin, then change the SHA and its comment
together in one reviewed commit. `scripts/validate.py` fails if any
non-`actions/*` step in the dispatch workflow goes back to a tag.

## Authority

The generated mission tells every worker, in the dispatch itself, that the
assignment is a recorded ChatGPT CTO decision, that it confers no CTO
authority, that loading `$cto-ops` confers none either, and that the worker
must not reassign the agent or skill or re-scope the mission. Automation
likewise never picks a worker, a skill, or a scope: those fields must already
be set, by a human decision, before anything dispatches.

## One-time GitHub Project setup (manual)

`GITHUB_TOKEN` cannot write ProjectV2 fields, and this repository has no
project-scoped PAT. Provisioning the fields automatically would mean either
storing a broad new credential or building a mirror of the Project — both
worse than a five-minute manual setup. Create these once on the project, in
**Project → Settings → Fields**:

| Field | Type | Options |
|---|---|---|
| Status | Single select | Ready, In Progress, Review, Blocked, Done |
| Priority | Single select | P0, P1, P2 |
| Agent | Single select | Claude, Codex, Local |
| Skill | Single select | Build, Debug, Review, QA, Research, Data |
| Feature | Text | — |
| Blocker | Text | — |

The issue status block, not the Project, is what this automation reads and
writes. The Project stays a portfolio view over the same values; keeping the
issue authoritative is why dispatch works with no project credential at all.
