#!/usr/bin/env bash
# Double-click from Finder. Repairs the Mac-side CTO reviewer without typing:
#   1. forces the engine to Claude Code (never Codex)
#   2. re-runs install.sh so the launchd job's PATH includes `claude`
#   3. pushes any commits waiting in ~/dev/ai-dispatch and ~/dev/personal-assistant
#   4. runs one review tick now and shows the result
here="$(cd "$(dirname "$0")" && pwd)"
export PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
CTO_HOME="$HOME/.cto"; mkdir -p "$CTO_HOME/log"
step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

step "1/4 engine -> claude"
if [ -f "$CTO_HOME/config" ]; then
  grep -v '^CTO_ENGINE=' "$CTO_HOME/config" > "$CTO_HOME/config.new" && mv "$CTO_HOME/config.new" "$CTO_HOME/config"
fi
echo 'CTO_ENGINE=claude' >> "$CTO_HOME/config"
if ! command -v claude >/dev/null; then
  echo "Claude Code CLI is not installed. Installing..."
  npm install -g @anthropic-ai/claude-code || { echo "install failed - open Terminal and run: npm install -g @anthropic-ai/claude-code"; read -r -p "press Enter to close"; exit 1; }
fi
claude --version || true

step "2/4 launchd job (PATH + 5-minute schedule)"
bash "$here/install.sh"

step "3/4 push waiting commits"
for d in "$HOME/dev/ai-dispatch" "$HOME/dev/personal-assistant"; do
  [ -d "$d/.git" ] || continue
  git -C "$d" push --quiet 2>&1 | tail -1
  printf '%s: %s\n' "$(basename "$d")" "$(git -C "$d" status -sb | head -1)"
done

step "4/4 one review tick now"
bash "$here/cto-review.sh" 2>&1 | tail -20

echo; echo "Done. Latest reviewer output:"; ls -t "$CTO_HOME/log" 2>/dev/null | head -3
read -r -p "press Enter to close"
