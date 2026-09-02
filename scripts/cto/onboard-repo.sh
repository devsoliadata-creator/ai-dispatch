#!/usr/bin/env bash
# Connect one repository to the shared dispatch layer, end to end:
#   caller workflow (+ CI template when the repo has none), AGENTS.md, issue
#   and PR templates, the CLAUDE_CODE_OAUTH_TOKEN secret, the routing labels,
#   and a PR (or a direct push to the default branch with --direct).
#
#   onboard-repo.sh owner/repo [--verify "npm test"] [--direct]
#
# Verify command is auto-detected when omitted (python validate.py / pytest /
# npm test / make test); pass --verify "" to disable.
# The token comes from $CLAUDE_CODE_OAUTH_TOKEN or ~/.cto/claude_token (chmod 600).
set -euo pipefail
CTO_HOME="${CTO_HOME:-$HOME/.cto}"
[ -f "$CTO_HOME/config" ] && . "$CTO_HOME/config"
CTO_WORKDIR="${CTO_WORKDIR:-$CTO_HOME/repos}"
here="$(cd "$(dirname "$0")" && pwd)"
tpl="$here/../../templates/caller"

repo="${1:?usage: onboard-repo.sh owner/repo [--verify CMD] [--direct]}"; shift
verify="__auto__"; direct=0
while [ $# -gt 0 ]; do
  case "$1" in
    --verify) verify="$2"; shift 2 ;;
    --direct) direct=1; shift ;;
    *) echo "unknown arg $1"; exit 1 ;;
  esac
done

token="${CLAUDE_CODE_OAUTH_TOKEN:-}"
[ -n "$token" ] || { [ -f "$CTO_HOME/claude_token" ] && token="$(tr -d '[:space:]' < "$CTO_HOME/claude_token")"; }
[ -n "$token" ] || { echo "no token: run  claude setup-token  then  printf '%s' '<token>' > ~/.cto/claude_token && chmod 600 ~/.cto/claude_token"; exit 1; }

mkdir -p "$CTO_WORKDIR"
dir="$CTO_WORKDIR/${repo#*/}"
if [ ! -d "$dir/.git" ]; then gh repo clone "$repo" "$dir" -- --quiet; fi
cd "$dir"
default="$(gh repo view "$repo" --json defaultBranchRef --jq .defaultBranchRef.name)"
git fetch --quiet origin "$default" && git checkout --quiet -B "chore/ai-dispatch-onboard" "origin/$default"

# --- verify command
if [ "$verify" = "__auto__" ]; then
  if   [ -f scripts/validate.py ]; then verify="python3 scripts/validate.py"
  elif [ -f pytest.ini ] || { [ -f pyproject.toml ] && grep -q pytest pyproject.toml; }; then verify="python3 -m pytest"
  elif [ -f package.json ] && grep -q '"test"' package.json; then verify="npm test"
  elif [ -f Makefile ] && grep -qE '^test:' Makefile; then verify="make test"
  else verify=""; fi
fi
echo "verify command: '${verify:-<none>}'"

# --- caller workflow
mkdir -p .github/workflows .github/ISSUE_TEMPLATE
sed "s|      verify_command: \"\"|      verify_command: \"$verify\"|" "$tpl/.github/workflows/ai-dispatch.yml" > .github/workflows/ai-dispatch.yml
ci_gate="ci.yml"
if [ ! -f .github/workflows/ci.yml ]; then
  if [ -n "$verify" ]; then
    # the template's validate job runs python + validate.py; swap in this repo's verify command
    sed "s|      - run: python3 scripts/validate.py|      - run: $verify|" "$tpl/.github/workflows/ci.yml" > .github/workflows/ci.yml
    case "$verify" in npm*|make*)
      awk '/actions\/setup-python@v5/{skip=3} skip>0{skip--; next} {print}' .github/workflows/ci.yml > .github/workflows/ci.yml.new && mv .github/workflows/ci.yml.new .github/workflows/ci.yml ;;
    esac
    echo "added ci.yml (canonical CI gate on)"
  else ci_gate=""; fi
elif ! grep -q "workflow_dispatch" .github/workflows/ci.yml; then
  ci_gate=""
  echo "existing ci.yml has no workflow_dispatch inputs -> canonical CI gate off (add the template's inputs + report job later to enable)"
fi
# tell the caller whether to dispatch CI on worker PR heads
if [ "$ci_gate" = "" ]; then
  awk '/^      verify_command: /{print "      ci_workflow: \"\""} {print}' .github/workflows/ai-dispatch.yml > .github/workflows/ai-dispatch.yml.new && mv .github/workflows/ai-dispatch.yml.new .github/workflows/ai-dispatch.yml
fi

[ -f AGENTS.md ] || cp "$tpl/AGENTS.md" AGENTS.md
[ -f .github/ISSUE_TEMPLATE/feature.md ] || cp "$tpl/.github/ISSUE_TEMPLATE/feature.md" .github/ISSUE_TEMPLATE/feature.md
[ -f .github/pull_request_template.md ] || cp "$tpl/.github/pull_request_template.md" .github/pull_request_template.md

# --- secret + labels (independent of the merge)
gh secret set CLAUDE_CODE_OAUTH_TOKEN --repo "$repo" --body "$token"
while IFS='|' read -r name color desc; do
  gh label create "$name" --repo "$repo" --color "$color" --description "$desc" --force >/dev/null 2>&1 || true
done <<'EOF'
agent:claude|1f6feb|Execution worker: Claude
agent:codex|8250df|Execution worker: Codex
agent:local|57606a|Execution worker: Local
skill:build|0e8a16|Execution skill: bounded feature build
skill:debug|d93f0b|Execution skill: root-cause debugging
skill:review|a371f7|Execution skill: adversarial review
skill:qa|fbca04|Execution skill: user-visible QA
skill:research|0969da|Execution skill: bounded research
skill:data|1b7c83|Execution skill: verified data curation
cto:review|e4e669|Awaiting the CTO verdict
cto:approved|0e8a16|CTO approved; owner merges
EOF
echo "secret + labels set on $repo"

# --- commit and ship
git add -A
if git diff --cached --quiet; then echo "nothing to commit (already onboarded)"; exit 0; fi
git -c user.name="${GIT_AUTHOR_NAME:-JM}" -c user.email="${GIT_AUTHOR_EMAIL:-dev.soliadata@gmail.com}" commit -q -m "Connect to the shared AI dispatch layer (devsoliadata-creator/ai-dispatch)"
if [ "$direct" = 1 ]; then
  git push --quiet origin "HEAD:$default" && echo "pushed to $default"
else
  git push --quiet -u origin chore/ai-dispatch-onboard
  gh pr create --repo "$repo" --base "$default" --fill --body "Adds the thin caller for devsoliadata-creator/ai-dispatch (verify: \`${verify:-none}\`). Merge, then \`cto new\` works here." || true
fi
echo
echo "Remaining manual step (once per account): install the Claude GitHub App on this repo -> https://github.com/apps/claude"
