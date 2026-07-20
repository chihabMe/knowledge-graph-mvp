#!/usr/bin/env bash
# Provisions a per-client Google service account per ADR-009: each client
# deployment gets its own service account, created by us in our GCP project;
# clients never touch GCP. Connecting Drive is then just the client sharing a
# folder with this service account's email, as Viewer.
#
# Usage: scripts/provision-drive-service-account.sh <client-slug> [gcp-project-id]
#
# <client-slug> must be safe as a GCP service-account id: lowercase letters,
# digits, hyphens; 6-24 chars after the "kg-" prefix we add (GCP's own id
# limit is 6-30 chars total).

set -euo pipefail

if ! command -v gcloud >/dev/null 2>&1; then
  echo "gcloud CLI not found. Install the Google Cloud SDK first."
  exit 1
fi

CLIENT_SLUG="${1:-}"
if [ -z "$CLIENT_SLUG" ]; then
  echo "Usage: $0 <client-slug> [gcp-project-id]"
  exit 1
fi
if ! [[ "$CLIENT_SLUG" =~ ^[a-z][a-z0-9-]{2,20}[a-z0-9]$ ]]; then
  echo "Invalid client slug: $CLIENT_SLUG"
  echo "Use lowercase letters, digits, and hyphens (3-22 chars)."
  exit 1
fi

PROJECT_ID="${2:-}"
if [ -z "$PROJECT_ID" ]; then
  PROJECT_ID="$(gcloud config get-value project 2>/dev/null || true)"
fi
if [ -z "$PROJECT_ID" ] || [ "$PROJECT_ID" = "(unset)" ]; then
  echo "No GCP project configured. Pass one explicitly: $0 $CLIENT_SLUG <gcp-project-id>"
  exit 1
fi

SA_ID="kg-${CLIENT_SLUG}"
SA_EMAIL="${SA_ID}@${PROJECT_ID}.iam.gserviceaccount.com"

if ! REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null)"; then
  echo "Not inside a git repository."
  exit 1
fi
KEY_DIR="$REPO_ROOT/secrets/drive-service-accounts"
KEY_PATH="$KEY_DIR/${CLIENT_SLUG}.json"
mkdir -p "$KEY_DIR"

if [ -e "$KEY_PATH" ]; then
  echo "Key file already exists, refusing to overwrite: $KEY_PATH"
  echo "Remove it first if you intend to issue a new key for $SA_EMAIL."
  exit 1
fi

if gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" >/dev/null 2>&1; then
  echo "Service account already exists: $SA_EMAIL"
else
  echo "Creating service account $SA_EMAIL in project $PROJECT_ID..."
  gcloud iam service-accounts create "$SA_ID" \
    --project="$PROJECT_ID" \
    --display-name="Knowledge Graph - ${CLIENT_SLUG}"
fi

echo "Issuing a new key for $SA_EMAIL..."
gcloud iam service-accounts keys create "$KEY_PATH" \
  --iam-account="$SA_EMAIL" \
  --project="$PROJECT_ID"

echo ""
echo "Done. Key stored at: $KEY_PATH"
echo "(This directory is gitignored — never commit key material.)"
echo ""
echo "Next steps:"
echo "  1. Set this client's .env: GOOGLE_SERVICE_ACCOUNT_FILE=$KEY_PATH"
echo "  2. Ask the client to share their Drive root folder or shared drive"
echo "     with $SA_EMAIL as Viewer (or Editor, if content export needs it)."
echo "  3. Configure admin-approved per-user Drive OAuth. Each pilot user"
echo "     connects Google once so the system can verify which indexed"
echo "     documents that user may access."
