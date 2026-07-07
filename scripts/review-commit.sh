#!/usr/bin/env bash
# review-commit.sh
# Runs a structured senior-engineer review of recent commits and writes
# findings to REVIEW.md so the coding agent can read and act on them.
#
# Usage:
#   ./scripts/review-commit.sh              # reviews last commit vs HEAD~1
#   ./scripts/review-commit.sh main         # reviews current branch vs main
#   ./scripts/review-commit.sh HEAD~3 HEAD  # reviews a specific range

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
REVIEW_FILE="$REPO_ROOT/REVIEW.md"
BASE="${1:-HEAD~1}"
HEAD="${2:-HEAD}"

echo "🔍 Generating senior-engineer review: $BASE..$HEAD"

# Gather context
COMMIT_MSG=$(git log "$BASE..$HEAD" --pretty=format:"%h %s" 2>/dev/null || echo "(no commits in range)")
DIFF=$(git diff "$BASE" "$HEAD" 2>/dev/null)
DIFF_STAT=$(git diff "$BASE" "$HEAD" --stat 2>/dev/null)
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")
AUTHOR=$(git log -1 --pretty=format:"%an <%ae>" 2>/dev/null || echo "unknown")

if [ -z "$DIFF" ]; then
  echo "⚠️  No diff found between $BASE and $HEAD. Nothing to review."
  exit 0
fi

# Write the review prompt context to a temp file for the engine calls
PROMPT_FILE=$(mktemp /tmp/review-prompt.XXXXXX.md)
# Clean up on any exit path — set -e can bail before the end of the script.
trap 'rm -f "$PROMPT_FILE" "${AI_ERR_FILE:-}"' EXIT

cat > "$PROMPT_FILE" << PROMPT
You are a senior engineer on a permission-safe AI knowledge graph system.
Your job is to review the following code changes and write a structured review.

IMPORTANT: Review ONLY the diff provided below. Do not search files, run tools,
or explore the repository. Respond with the review text directly and nothing
else (no preamble, no narration).

Project context:
- Stack: Django + DRF + Celery + Neo4j + SpiceDB + Redis + Postgres
- Core constraint: Permission-safe retrieval — restricted facts must never reach the LLM
- Every graph node/relationship/chunk needs source provenance
- SpiceDB is mandatory for authorization — no custom Postgres permission checks
- See AGENTS.md and ai-context/ for full rules

Commits being reviewed:
$COMMIT_MSG

Author: $AUTHOR

Diff stat:
$DIFF_STAT

Full diff:
\`\`\`diff
$DIFF
\`\`\`

Write your review in this exact format:

## Senior Engineer Review
**Timestamp:** $TIMESTAMP
**Commits:** $COMMIT_MSG
**Author:** $AUTHOR

### Summary
(2-3 sentence overview of what the change does)

### ✅ What's Good
(bullet list — what's done correctly, good patterns, good tests, etc.)

### ❌ Issues — Must Fix
(bullet list with file:line references — bugs, security issues, permission leaks, missing provenance, anything that must be fixed before merge)

### ⚠️ Improvements — Should Fix
(bullet list — code quality, test gaps, naming, missing validation, things that should be fixed but aren't blockers)

### 💡 Suggestions — Nice To Have
(bullet list — optional improvements, future considerations)

### Verdict
One of: ✅ APPROVE | 🔄 APPROVE WITH NOTES | ❌ REQUEST CHANGES

### Agent Action Items
(numbered list of concrete tasks for the coding agent to work through, ordered by priority)
PROMPT

# Run the review — Claude Code CLI first, agy as fallback, else a placeholder.
# Same failure handling as the pre-commit hook: stderr captured, non-zero exit
# treated as failure (partial stdout discarded), fallback on failure too.
REVIEW_CONTENT=""
AI_TIMEOUT="${AI_REVIEW_TIMEOUT:-240}"
AI_ERR_FILE=$(mktemp)
AI_STATUS=0

run_with_timeout() {
  # -k 10: SIGKILL 10s after the deadline in case the CLI ignores SIGTERM.
  if command -v timeout &> /dev/null; then
    timeout -k 10 "$AI_TIMEOUT" "$@" 2>>"$AI_ERR_FILE"
  else
    "$@" 2>>"$AI_ERR_FILE"
  fi
}

if command -v claude &> /dev/null; then
  echo "  Running Claude review..."
  set +e
  # --disallowedTools keeps the reviewer from wandering the repo; the prompt is
  # fed via stdin so the diff never shows up in `ps` output or hits ARG_MAX.
  REVIEW_CONTENT=$(run_with_timeout claude -p --disallowedTools \
    "Bash" "Edit" "Write" "NotebookEdit" "WebFetch" "WebSearch" \
    "Task" "Read" "Grep" "Glob" < "$PROMPT_FILE")
  AI_STATUS=$?
  set -e
  if [ "$AI_STATUS" -ne 0 ]; then
    echo "  ⚠️  claude review failed (exit $AI_STATUS) — discarding partial output."
    REVIEW_CONTENT=""
  fi
fi

if [ -z "$REVIEW_CONTENT" ] && command -v agy &> /dev/null; then
  echo "  Running agy review..."
  set +e
  REVIEW_CONTENT=$(run_with_timeout agy --print "$(cat "$PROMPT_FILE")")
  AI_STATUS=$?
  set -e
  if [ "$AI_STATUS" -ne 0 ]; then
    echo "  ⚠️  agy review failed (exit $AI_STATUS) — discarding partial output."
    REVIEW_CONTENT=""
  fi
fi

# If both engines failed or aren't available, write a manual-review placeholder
if [ -z "$REVIEW_CONTENT" ]; then
  if ! command -v claude &> /dev/null && ! command -v agy &> /dev/null; then
    FAIL_REASON="neither the claude nor the agy CLI is installed"
  elif [ "$AI_STATUS" -eq 124 ]; then
    FAIL_REASON="review timed out after ${AI_TIMEOUT}s (raise with AI_REVIEW_TIMEOUT=<seconds>)"
  else
    FAIL_REASON="the available engines ran but produced no review (last exit: $AI_STATUS)"
  fi
  # Neutralize code fences in stderr so they can't break REVIEW.md formatting.
  ERR_TAIL=$(tail -c 800 "$AI_ERR_FILE" 2>/dev/null | sed 's/```/~~~/g' || true)
  REVIEW_CONTENT="## Senior Engineer Review
**Timestamp:** $TIMESTAMP
**Commits:** $COMMIT_MSG
**Author:** $AUTHOR

> ⚠️ Automated review could not run — $FAIL_REASON.
> Run \`claude -p\` or \`agy\` manually with the prompt in \`/tmp/review-prompt.*.md\` or use \`make review\`.

Stderr tail:
\`\`\`
${ERR_TAIL:-<empty>}
\`\`\`

### Diff Stat
\`\`\`
$DIFF_STAT
\`\`\`

### Full Diff
\`\`\`diff
$DIFF
\`\`\`
"
fi

# Write the review file
cat > "$REVIEW_FILE" << REVIEW
<!-- AUTO-GENERATED BY scripts/review-commit.sh — DO NOT EDIT MANUALLY -->
<!-- Agent: read this file before your next task. Address all action items. -->

$REVIEW_CONTENT
REVIEW

echo "✅ Review written to REVIEW.md"
echo "   The coding agent should read REVIEW.md and address action items before next commit."
