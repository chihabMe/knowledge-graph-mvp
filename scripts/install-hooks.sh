#!/usr/bin/env bash
# scripts/install-hooks.sh
# Installs git hooks from scripts/hooks/ into .git/hooks/
# Run once after cloning: bash scripts/install-hooks.sh

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel)"
HOOKS_SOURCE="$REPO_ROOT/scripts/hooks"
HOOKS_TARGET="$REPO_ROOT/.git/hooks"
REVIEW_STAGED_SCRIPT="$REPO_ROOT/scripts/review-staged.sh"

if [ ! -d "$HOOKS_SOURCE" ]; then
  echo "❌ scripts/hooks/ not found. Run from the repo root."
  exit 1
fi

echo "Installing git hooks from scripts/hooks/ → .git/hooks/"

for hook in "$HOOKS_SOURCE"/*; do
  hook_name=$(basename "$hook")
  target="$HOOKS_TARGET/$hook_name"

  cp "$hook" "$target"
  chmod +x "$target"
  echo "  ✅ Installed: $hook_name"
done

if [ -f "$REVIEW_STAGED_SCRIPT" ]; then
  chmod +x "$REVIEW_STAGED_SCRIPT"
  echo "  ✅ Ensured executable: scripts/review-staged.sh"
fi

echo ""
echo "Done. Hooks will fire automatically on git operations."
echo "To skip a review on a specific commit: SKIP_REVIEW=1 git commit ..."
