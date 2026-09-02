#!/usr/bin/env bash
# CTO review, run from the Mac: finds every open PR labeled `cto:review`
# across the owner's repos, has the engine (Claude Code CLI on the Max plan by
# default, or Codex) review the actual checkout with the CTO persona, and posts
# the verdict as a PR comment under the owner's GitHub login. The repo's
# CTO-verdict workflow relays it to the control issue.
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
LOCK="$CTO_HOME/review.lock"
mkdir -p "$CTO_WORKDIR" "$CTO_HOME/log"

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }

# One run at a time (launchd fires every few minutes).
if ! mkdir "$LOCK" 2>/dev/null; then log "another run is active; exiting"; exit 0; fi
trap 'rmdir "$LOCK"' EXIT

for tool in gh git jq "$CTO_ENGINE"; do
  command -v "$tool" >/dev/null || { log "missing $tool"; exit 1; }
done

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
