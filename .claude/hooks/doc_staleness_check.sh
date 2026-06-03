#!/usr/bin/env bash
# Runs at session Stop. Checks which tracked source files changed (vs HEAD)
# and prints targeted reminders about CLAUDE.md, README.md, and test coverage.
set -euo pipefail

cd "$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0

changed=$(git diff --name-only HEAD 2>/dev/null || true)
[ -z "$changed" ] && exit 0

warn() { echo "  REMINDER: $1"; }
any_reminder=0

# ── CLAUDE.md triggers ──────────────────────────────────────────────────────
if echo "$changed" | grep -qE '^agents/base\.py$|^models/results\.py$|^tools/langchain_adapter\.py$'; then
  warn "Core agent layer changed → review CLAUDE.md §Key Design Patterns (§1 and §4) and README Architecture section"
  any_reminder=1
fi

if echo "$changed" | grep -qE '^workflows/orchestrator\.py$|^workflows/router\.py$'; then
  warn "Workflow layer changed → review CLAUDE.md §Key Design Patterns (§2–3) and README Workflow routing table"
  any_reminder=1
fi

if echo "$changed" | grep -qE '^config\.py$|^pyproject\.toml$'; then
  warn "Config or deps changed → review CLAUDE.md §Framework Currency Rule and §Invariants, README config/quick-start"
  any_reminder=1
fi

if echo "$changed" | grep -qE '^main\.py$'; then
  warn "main.py changed → review README API section for new/changed endpoints"
  any_reminder=1
fi

# ── Test coverage gaps ───────────────────────────────────────────────────────
while IFS= read -r f; do
  # Only new/modified files in agents/ or tools/ (not __init__.py)
  [[ "$f" == *__init__* ]] && continue
  base=$(basename "$f" .py)
  test_f="tests/test_${base}.py"
  if [ ! -f "$test_f" ]; then
    warn "$f has no test file at $test_f — add to BACKLOG.md if intentionally deferred"
    any_reminder=1
  fi
done < <(echo "$changed" | grep -E '^(agents|tools)/[^/]+\.py$' || true)

# ── AGENT_REGISTRY guard ─────────────────────────────────────────────────────
if echo "$changed" | grep -qE '^agents/[^/]+\.py$'; then
  if grep -q "class.*BaseAgent" $(echo "$changed" | grep -E '^agents/[^/]+\.py$') 2>/dev/null; then
    if ! grep -q "AGENT_REGISTRY" workflows/orchestrator.py 2>/dev/null; then
      warn "New BaseAgent subclass detected but AGENT_REGISTRY not found in workflows/orchestrator.py"
      any_reminder=1
    fi
  fi
fi

[ "$any_reminder" -eq 1 ] && echo "(review Update Triggers section in CLAUDE.md for details)"
exit 0
