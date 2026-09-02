#!/usr/bin/env bash
# Describe a change in one sentence; ChatGPT CTO (via Codex, Free plan) writes
# the feature control issue and this script files it with the right labels.
#
#   cto-new.sh <owner/repo> "what you want"
#
# Requires: gh (logged in), codex (`codex login` done once).
set -euo pipefail

CTO_HOME="${CTO_HOME:-$HOME/.cto}"
[ -f "$CTO_HOME/config" ] && . "$CTO_HOME/config"
CTO_PROMPT="${CTO_PROMPT:-$(dirname "$0")/../../docs/CHATGPT-CTO-PROMPT.md}"
CTO_WORKDIR="${CTO_WORKDIR:-$CTO_HOME/repos}"
CTO_MODEL="${CTO_MODEL:-}"

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
model_arg=(); [ -n "$CTO_MODEL" ] && model_arg=(-m "$CTO_MODEL")
codex exec --sandbox read-only -C "$dir" --skip-git-repo-check ${model_arg[@]+"${model_arg[@]}"} -o "$out" "$prompt" >"$out.run" 2>&1

labels="$(grep -E '^LABELS:' "$out" | tail -1 | sed 's/^LABELS:[[:space:]]*//; s/[[:space:]]//g')"
[ -n "$labels" ] || { echo "Codex did not return a LABELS line; see $out"; exit 1; }
body="$(awk '/^# Feature/{p=1} /^LABELS:/{exit} p{print}' "$out")"
title="$(cut -c1-70 <<<"$request")"

url="$(gh issue create -R "$repo" --title "[Feature] $title" --body "$body" --label "${labels%%,*}" --label "${labels##*,}")"
echo "Created $url  (labels: $labels)"
