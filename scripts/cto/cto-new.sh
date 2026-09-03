#!/usr/bin/env bash
# Describe a change in one sentence; ChatGPT CTO (via Codex, Free plan) writes
# the feature control issue and this script files it with the right labels.
#
#   cto-new.sh <owner/repo> "what you want"
#
# Requires: gh (logged in) and the engine: claude (Claude Code CLI, Max plan; default) or codex.
set -euo pipefail

CTO_HOME="${CTO_HOME:-$HOME/.cto}"
[ -f "$CTO_HOME/config" ] && . "$CTO_HOME/config"
CTO_PROMPT="${CTO_PROMPT:-$(dirname "$0")/../../docs/CHATGPT-CTO-PROMPT.md}"
CTO_WORKDIR="${CTO_WORKDIR:-$CTO_HOME/repos}"
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

repo="${1:?usage: cto-new.sh <owner/repo> \"description\"}"
request="${2:?usage: cto-new.sh <owner/repo> \"description\"}"
mkdir -p "$CTO_WORKDIR" "$CTO_HOME/log"

dir="$CTO_WORKDIR/${repo#*/}"
if [ ! -d "$dir/.git" ]; then gh repo clone "$repo" "$dir" -- --quiet; fi
git -C "$dir" fetch --quiet origin && git -C "$dir" checkout --quiet --detach origin/HEAD 2>/dev/null || true

prompt="$(cat "$CTO_PROMPT")

---

You are running non-interactively inside a checkout of $repo. Read AGENTS.md / CLAUDE.md and enough of the code to scope this request precisely.

JM's request: $request

Output ONLY: the issue body in the template (starting at the line \`# Feature\`), then a final line exactly of the form
LABELS: agent:claude, skill:<build|debug|review|qa|research|data>
Nothing else. If a JM decision is required first, set State: Blocked with the Blocker line and still output LABELS."

out="$CTO_HOME/log/$(date +%Y%m%d-%H%M%S)-new-${repo#*/}.md"
run_engine "$dir" "$out" "$prompt" 2>"$out.run"

labels="$(grep -E '^LABELS:' "$out" | tail -1 | sed 's/^LABELS:[[:space:]]*//; s/[[:space:]]//g')"
[ -n "$labels" ] || { echo "$CTO_ENGINE did not return a LABELS line; see $out and $out.run"; exit 1; }
body="$(awk '/^# Feature/{p=1} /^LABELS:/{exit} p{print}' "$out")"
# The gate: a new feature is Proposed until JM (`cto go N`) or the CTO (`CTO: GO`
# on the issue) clears it. Nothing dispatches from Proposed.
body="$(printf '%s\n' "$body" | sed -E 's/^\*\*State:\*\*.*/**State:** Proposed/; s/^\*\*Next:\*\*.*/**Next:** CTO approval/')"
title="$(cut -c1-70 <<<"$request")"

triage=(); [ "${CTO_TRIAGE_NEW:-0}" = "1" ] && triage=(--label cto:triage)
url="$(gh issue create -R "$repo" --title "[Feature] $title" --body "$body" --label "${labels%%,*}" --label "${labels##*,}" ${triage[@]+"${triage[@]}"})"
n="${url##*/}"
echo "Created $url  (labels: $labels; State: Proposed)"
if [ "${CTO_TRIAGE_NEW:-0}" = "1" ]; then echo "The CTO reviewer will approve or block it within ~5 min (cto:triage)."
else echo "Approve it:  cto go $n            (or let the CTO decide: gh issue edit $n -R $repo --add-label cto:triage)"; fi
