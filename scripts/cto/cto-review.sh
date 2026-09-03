#!/usr/bin/env bash
# CTO, run from the Mac every few minutes. Two duties, both answered by the
# engine (Claude Code CLI on the Max plan by default, or Codex) running the
# CTO persona against a real checkout, posted under the owner's GitHub login
# and relayed by the repo's CTO-verdict workflow:
#   1. issues labeled `cto:triage` -> `CTO: GO ...` (dispatch) or `CTO: BLOCK ...`
#      (a Blocked worker hand-back, or a new Proposed feature when CTO_TRIAGE_NEW=1)
#   2. PRs labeled `cto:review`    -> `CTO: APPROVE` / `REWORK` / `BLOCK`
#
# Requires: gh (logged in), git, and the engine: claude (Claude Code CLI, Max plan; default) or codex.
# Config (env or ~/.cto/config): CTO_OWNER, CTO_REPOS (space-separated
# allow-list, empty = all), CTO_WORKDIR, CTO_PROMPT, CTO_MODEL.
set -euo pipefail

CTO_HOME="${CTO_HOME:-$HOME/.cto}"
[ -f "$CTO_HOME/config" ] && . "$CTO_HOME/config"
CTO_OWNER="${CTO_OWNER:-devsoliadata-creator}"
CTO_REPOS="${CTO_REPOS:-}"
CTO_WORKDIR="${CTO_WORKDIR:-$CTO_HOME/repos}"
CTO_PROMPT="${CTO_PROMPT:-$(dirname "$0")/../../docs/CHATGPT-CTO-PROMPT.md}"
CTO_MODEL="${CTO_MODEL:-}"

# ---- engine: claude (default, Max plan) or codex. Set CTO_ENGINE in ~/.cto/config.
CTO_ENGINE="${CTO_ENGINE:-claude}"
run_engine() {  # run_engine <workdir> <outfile> <prompt> [--write]
  local dir="$1" out="$2" prompt="$3" mode="${4:-}"
  if [ "$CTO_ENGINE" = "codex" ]; then
    local model_arg=(); [ -n "${CTO_MODEL:-}" ] && model_arg=(-m "$CTO_MODEL")
    codex exec --sandbox read-only -C "$dir" --skip-git-repo-check ${model_arg[@]+"${model_arg[@]}"} -o "$out" "$prompt"
  else
    local tools="Read,Grep,Glob,Bash(git diff:*),Bash(git log:*),Bash(git show:*),Bash(git status:*),Bash(gh pr view:*),Bash(gh pr diff:*),Bash(gh issue view:*),Bash(cat:*),Bash(ls:*)"
    local model_arg=(); [ -n "${CTO_MODEL:-}" ] && model_arg=(--model "$CTO_MODEL")
    ( cd "$dir" && claude -p "$prompt" --output-format text --max-turns 60 --allowedTools "$tools" ${model_arg[@]+"${model_arg[@]}"} ) > "$out"
  fi
}
# ---- sync: push commits that Claude (Cowork) left on this Mac. Repos listed in
# CTO_SYNC_REPOS (space-separated paths); only fast-forward pushes of a clean
# checkout on its tracked branch, so nothing here can rewrite history.
CTO_SYNC_REPOS="${CTO_SYNC_REPOS:-$HOME/dev/ai-dispatch $HOME/dev/personal-assistant}"
sync_repos() {
  local d
  for d in $CTO_SYNC_REPOS; do
    [ -d "$d/.git" ] || continue
    if [ -n "$(git -C "$d" status --porcelain)" ]; then log "sync: $d has uncommitted changes; not pushing"; continue; fi
    git -C "$d" fetch --quiet origin 2>/dev/null || { log "sync: fetch failed for $d"; continue; }
    local ahead; ahead="$(git -C "$d" rev-list --count '@{u}..HEAD' 2>/dev/null || echo 0)"
    local behind; behind="$(git -C "$d" rev-list --count 'HEAD..@{u}' 2>/dev/null || echo 0)"
    if [ "$ahead" -gt 0 ] && [ "$behind" -eq 0 ]; then
      git -C "$d" push --quiet && log "sync: pushed $ahead commit(s) from $d" || log "sync: push failed for $d"
    elif [ "$ahead" -gt 0 ]; then
      log "sync: $d is ahead $ahead and behind $behind; needs a rebase (not automatic)"
    fi
  done
}
LOCK="$CTO_HOME/review.lock"
mkdir -p "$CTO_WORKDIR" "$CTO_HOME/log"

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }

# One run at a time (launchd fires every few minutes).
if ! mkdir "$LOCK" 2>/dev/null; then log "another run is active; exiting"; exit 0; fi
trap 'rmdir "$LOCK"' EXIT
sync_repos

for tool in gh git jq "$CTO_ENGINE"; do
  command -v "$tool" >/dev/null || { log "missing $tool"; exit 1; }
done

# ---------------------------------------------------------------- 1. triage
issues="$(gh search issues --owner "$CTO_OWNER" --label cto:triage --state open --limit 20 \
        --json number,repository --jq '.[] | "\(.repository.nameWithOwner) \(.number)"')"
while read -r repo number; do
  [ -n "$repo" ] || continue
  if [ -n "$CTO_REPOS" ] && ! grep -qw -- "${repo#*/}" <<<"$CTO_REPOS"; then continue; fi
  issue="$(gh issue view "$number" -R "$repo" --json title,body,url,comments,labels)"
  # Already answered? (the verdict workflow removes the label; if it has not yet, do not answer twice)
  if jq -e '[.comments[] | select(.body | test("^\\s*CTO:\\s*(GO|BLOCK)"))] | length > 0' <<<"$issue" >/dev/null; then
    log "$repo#$number already has a CTO go/block; skipping"; continue
  fi
  state="$(jq -r .body <<<"$issue" | sed -nE 's/^\*\*State:\*\*[[:space:]]*(.*)[[:space:]]*$/\1/p' | head -1)"
  if [ "$state" = "Proposed" ] && [ "${CTO_TRIAGE_NEW:-0}" != "1" ]; then
    log "$repo#$number is Proposed and CTO_TRIAGE_NEW is off; JM approves with: cto go $number"; continue
  fi
  log "triaging $repo#$number ($state)"
  dir="$CTO_WORKDIR/${repo#*/}"
  if [ ! -d "$dir/.git" ]; then gh repo clone "$repo" "$dir" -- --quiet; fi
  git -C "$dir" fetch --quiet origin && git -C "$dir" checkout --quiet --detach origin/HEAD 2>/dev/null || true
  prompt="$(cat "$CTO_PROMPT")

---

You are triaging control issue $(jq -r .url <<<"$issue") in this checkout of $repo (default branch).
State: $state. Read AGENTS.md / CLAUDE.md and the code the issue touches. Read the whole thread below;
if the worker was Blocked, its last comments hold the evidence -- diagnose the real cause, do not restate it.

ISSUE TITLE: $(jq -r .title <<<"$issue")

ISSUE BODY:
$(jq -r .body <<<"$issue")

COMMENTS (oldest first):
$(jq -r '.comments[] | "--- \(.author.login) at \(.createdAt)\n\(.body)"' <<<"$issue" | tail -c 20000)

Decide. Output your reasoning briefly, then ONE final block and nothing after it:
either
CTO: GO skill=<Build|Debug|QA|Research|Data> agent=Claude
<3-8 lines of concrete guidance for the worker: what to change, what NOT to retry, how to verify>
or
CTO: BLOCK <one line: the decision only JM can make>"
  out="$CTO_HOME/log/$(date +%Y%m%d-%H%M%S)-triage-${repo#*/}-$number.md"
  if ! run_engine "$dir" "$out" "$prompt" 2>"$out.run"; then log "$CTO_ENGINE failed for $repo#$number triage (see $out.run)"; continue; fi
  verdict="$(awk 'BEGIN{p=0} /^[[:space:]]*CTO:[[:space:]]*(GO|BLOCK)/{p=1; buf=""} p{buf=buf $0 "\n"} END{printf "%s", buf}' "$out")"
  if [ -z "$verdict" ]; then log "no CTO: GO/BLOCK block in output for $repo#$number"; continue; fi
  printf '%s' "$verdict" | gh issue comment "$number" -R "$repo" --body-file -
  log "posted triage for $repo#$number: $(head -1 <<<"$verdict")"
done <<<"$issues"

# ---------------------------------------------------------------- 2. review
prs="$(gh search prs --owner "$CTO_OWNER" --label cto:review --state open --limit 20 \
        --json number,repository --jq '.[] | "\(.repository.nameWithOwner) \(.number)"')"
[ -n "$prs" ] || { log "nothing to review"; exit 0; }

while read -r repo number; do
  [ -n "$repo" ] || continue
  if [ -n "$CTO_REPOS" ] && ! grep -qw -- "${repo#*/}" <<<"$CTO_REPOS"; then continue; fi
  log "reviewing $repo#$number"

  pr="$(gh pr view "$number" -R "$repo" --json title,body,headRefName,baseRefName,headRefOid,url,comments)"
  head="$(jq -r .headRefOid <<<"$pr")"
  # Already reviewed at this commit? (a CTO: comment newer than the head commit)
  if jq -e --arg h "$head" '
      .comments | map(select(.body | test("^\\s*CTO:"))) | length > 0' <<<"$pr" >/dev/null; then
    last_verdict="$(jq -r '[.comments[] | select(.body | test("^\\s*CTO:"))] | last | .createdAt' <<<"$pr")"
    head_date="$(gh api "repos/$repo/commits/$head" --jq .commit.committer.date)"
    if [[ "$last_verdict" > "$head_date" ]]; then log "verdict already posted for $head; skipping"; continue; fi
  fi

  dir="$CTO_WORKDIR/${repo#*/}"
  if [ ! -d "$dir/.git" ]; then gh repo clone "$repo" "$dir" -- --quiet; fi
  git -C "$dir" fetch --quiet origin "$(jq -r .baseRefName <<<"$pr")" "$(jq -r .headRefName <<<"$pr")"
  git -C "$dir" checkout --quiet --detach "$head"

  base="origin/$(jq -r .baseRefName <<<"$pr")"
  prompt="$(cat "$CTO_PROMPT")

---

You are reviewing pull request $(jq -r .url <<<"$pr") in this checkout (HEAD is the PR head).
Base branch: $base. Run \`git diff $base...HEAD\` and read the changed files and their tests.

PR title: $(jq -r .title <<<"$pr")

PR body:
$(jq -r .body <<<"$pr")

Produce the review exactly as the packet section of your instructions says: findings, then ONE verdict block last and nothing after it."

out="$CTO_HOME/log/$(date +%Y%m%d-%H%M%S)-${repo#*/}-$number.md"
  if ! run_engine "$dir" "$out" "$prompt" 2>"$out.run"; then
    log "$CTO_ENGINE failed for $repo#$number (see $out.run)"; continue
  fi

  # The verdict is the last `CTO:` line and everything after it.
  verdict="$(awk 'BEGIN{p=0} /^[[:space:]]*CTO:[[:space:]]*(APPROVE|REWORK|BLOCK)/{p=1; buf=""} p{buf=buf $0 "\n"} END{printf "%s", buf}' "$out")"
  if [ -z "$verdict" ]; then log "no verdict block in $CTO_ENGINE output for $repo#$number"; continue; fi
  findings="$(awk '/^[[:space:]]*CTO:[[:space:]]*(APPROVE|REWORK|BLOCK)/{exit} {print}' "$out")"

  # Findings first (informational, does not trigger the relay), verdict second.
  if [ -n "$(tr -d '[:space:]' <<<"$findings")" ]; then
    printf '🤖 **CTO review** (via %s, %s)\n\n%s\n' "$CTO_ENGINE" "${head:0:7}" "$findings" | gh pr comment "$number" -R "$repo" --body-file -
  fi
  printf '%s' "$verdict" | gh pr comment "$number" -R "$repo" --body-file -
  log "posted verdict for $repo#$number: $(head -1 <<<"$verdict")"
done <<<"$prs"
