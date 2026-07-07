#!/usr/bin/env bash
# Runs the same two-stage review gate as the pre-commit hook without creating a
# commit. Use this after code changes so the coding agent can show REVIEW.md to
# the user before deciding whether to fix or ignore review findings.

set -euo pipefail

if ! REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"; then
  echo "Not inside a git repository."
  exit 1
fi

HOOK="$REPO_ROOT/scripts/hooks/pre-commit"
REVIEW_FILE="$REPO_ROOT/REVIEW.md"
REVIEW_TIMEOUT="${REVIEW_TIMEOUT:-300}"

if [ ! -x "$HOOK" ]; then
  echo "Review hook is not executable: $HOOK"
  echo "Run: chmod +x scripts/hooks/pre-commit"
  exit 1
fi

if git diff --cached --quiet 2>/dev/null; then
  echo "No staged changes to review."
  echo "Stage the intended change set first, then run: make review-staged"
  exit 1
fi

set +e
rm -f "$REVIEW_FILE"
if command -v timeout >/dev/null 2>&1; then
  # -k 10: SIGKILL 10s after the deadline in case the hook ignores SIGTERM.
  timeout -k 10 "$REVIEW_TIMEOUT" bash "$HOOK"
else
  echo "WARNING: timeout command not found; running review without a time limit."
  bash "$HOOK"
fi
STATUS=$?
set -e

if [ "$STATUS" -eq 124 ]; then
  echo ""
  echo "Review timed out after ${REVIEW_TIMEOUT}s. Check the review agent output or rerun with REVIEW_TIMEOUT=<seconds>."
  exit 1
fi

if [ ! -f "$REVIEW_FILE" ]; then
  echo ""
  echo "ERROR: REVIEW.md was not produced. Check hook output above."
  exit 1
fi

if [ "$STATUS" -eq 0 ]; then
  echo ""
  echo "Review completed. Read $REVIEW_FILE and present the findings to the user."
  exit 0
fi

echo ""
echo "Review completed with blocking findings. Read $REVIEW_FILE and ask the user whether to fix or ignore them."
# A completed review exits 0 even with findings; REVIEW.md carries the verdict.
exit 0
