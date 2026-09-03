#!/usr/bin/env bash
# One-time Mac setup: checks tools, writes ~/.cto/config, installs a launchd
# job that runs cto-review.sh every 5 minutes while you are logged in.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
CTO_HOME="$HOME/.cto"
mkdir -p "$CTO_HOME/log"

for tool in gh claude git jq; do
  if ! command -v "$tool" >/dev/null; then
    echo "missing: $tool"
    case "$tool" in
      gh)     echo "  brew install gh && gh auth login" ;;
      claude) echo "  npm install -g @anthropic-ai/claude-code && claude   (log in with your Max account once)" ;;
      jq)     echo "  brew install jq" ;;
    esac
    exit 1
  fi
done
gh auth status >/dev/null 2>&1 || { echo "run: gh auth login"; exit 1; }

[ -f "$CTO_HOME/config" ] || cat > "$CTO_HOME/config" <<EOF
# Owner whose repos are polled for PRs labeled cto:review
CTO_OWNER=devsoliadata-creator
# Optional space-separated allow-list of repo names; empty = every repo
CTO_REPOS=""
CTO_PROMPT="$here/../../docs/CHATGPT-CTO-PROMPT.md"
# Engine for cto new / the reviewer: claude (Claude Code CLI on your Max plan) or codex
CTO_ENGINE=claude
# Optional model override for the engine (e.g. opus / sonnet, or a codex model)
CTO_MODEL=""
# Repo used by `cto new "..."` / `cto status N` when none is given
CTO_DEFAULT_REPO=devsoliadata-creator/personal_assistant
# 1 = the CTO reviewer also approves/blocks NEW (Proposed) issues on its own.
# 0 = JM approves each new issue herself with `cto go N`; the reviewer only triages Blocked ones.
CTO_TRIAGE_NEW=0
EOF
grep -q '^CTO_TRIAGE_NEW=' "$CTO_HOME/config" || printf '\nCTO_TRIAGE_NEW=0\n' >> "$CTO_HOME/config"
chmod +x "$here"/*.sh "$here/cto"
# `cto` on the PATH for zsh
mkdir -p "$HOME/.local/bin" && ln -sf "$here/cto" "$HOME/.local/bin/cto"
grep -q 'HOME/.local/bin' "$HOME/.zshrc" 2>/dev/null || printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$HOME/.zshrc"

claude_bin="$(dirname "$(command -v claude)")"
codex_bin="$(dirname "$(command -v codex 2>/dev/null || echo /usr/bin/false)")"
gh_bin="$(dirname "$(command -v gh)")"
plist="$HOME/Library/LaunchAgents/com.soliadata.cto-review.plist"
cat > "$plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.soliadata.cto-review</string>
  <key>ProgramArguments</key><array><string>/bin/bash</string><string>$here/cto-review.sh</string></array>
  <key>StartInterval</key><integer>300</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$CTO_HOME/log/review.log</string>
  <key>StandardErrorPath</key><string>$CTO_HOME/log/review.log</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>$claude_bin:$codex_bin:$gh_bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
</dict></plist>
EOF
launchctl unload "$plist" 2>/dev/null || true
launchctl load "$plist"
echo "Installed. Runs every 5 min; log: $CTO_HOME/log/review.log"
echo "Commands (open a new Terminal tab first):  cto new \"...\"   cto status N   cto list   cto watch N"
