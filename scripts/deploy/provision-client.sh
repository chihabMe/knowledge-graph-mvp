#!/usr/bin/env bash
# provision-client — stand up (or manage) one isolated client stack.
#
# The tool-agnostic deploy path: plain `docker compose` with a per-client
# project name (-p <slug>) for isolated networks + volumes. Coolify wraps this
# same flow later (its API replaces the `deploy` step); everything else is
# identical.
#
#   provision-client generate <slug> [generator options]
#       Mint clients/<slug>/ (secrets + keyring + env). Fill the remaining
#       shared OAuth/OpenRouter values without committing the directory.
#   provision-client check    <slug> [--target coolify|local]
#       Safe preflight: validate callbacks, client identity, and secret inputs.
#   provision-client deploy   <slug> --image ghcr.io/chihabMe/kg-backend:vX.Y.Z
#       check -> pull -> up -d --no-build -> wait for health.
#   provision-client status   <slug>
#   provision-client down      <slug>
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

die() { echo "error: $*" >&2; exit 1; }
usage() { sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit "${1:-1}"; }

client_dir()  { echo "clients/$1"; }
client_env()  { echo "clients/$1/$1.env"; }

compose() {
  local slug="$1"; shift
  docker compose -p "$slug" \
    --env-file "$(client_env "$slug")" \
    -f infra/compose.infrastructure.yml \
    -f infra/compose.app.yml \
    -f infra/compose.deploy.yml "$@"
}

cmd_generate() {
  local slug="${1:?usage: generate <slug> [--domain D]}"; shift || true
  python3 scripts/deploy/generate_client.py "$slug" "$@"
}

cmd_check() {
  local slug="${1:?usage: check <slug> [--target coolify|local]}"; shift || true
  local target="coolify"
  while [ $# -gt 0 ]; do
    case "$1" in
      --target) target="${2:?}"; shift 2 ;;
      *) die "unknown check option: $1" ;;
    esac
  done
  case "$target" in coolify|local) ;; *) die "target must be coolify or local" ;; esac
  python3 scripts/deploy/generate_client.py "$slug" --check --target "$target"
}

cmd_deploy() {
  local slug="${1:?usage: deploy <slug> --image REF}"; shift
  local image=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --image) image="${2:?}"; shift 2 ;;
      *) die "unknown option: $1" ;;
    esac
  done
  [ -n "$image" ] || die "deploy requires --image ghcr.io/chihabMe/kg-backend:<tag>"

  cmd_check "$slug" --target local
  echo "→ pulling images for $slug ($image)"
  KG_IMAGE="$image" compose "$slug" pull
  echo "→ starting $slug (migrations run automatically)"
  KG_IMAGE="$image" compose "$slug" up -d --no-build

  echo "→ waiting for health…"
  local i
  for i in $(seq 1 30); do
    if compose "$slug" exec -T django \
      python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health/', timeout=10)" 2>/dev/null; then
      echo "✓ $slug is healthy"
      echo "  next: select the Drive root, then each user connects Google once."
      return 0
    fi
    sleep 4
  done
  die "$slug did not become healthy in time — check: provision-client status $slug"
}

cmd_status() { local slug="${1:?usage: status <slug>}"; compose "$slug" ps; }
cmd_down()   { local slug="${1:?usage: down <slug>}"; compose "$slug" down; }

main() {
  local command="${1:-}"; shift || true
  case "$command" in
    generate) cmd_generate "$@" ;;
    check)    cmd_check "$@" ;;
    deploy)   cmd_deploy "$@" ;;
    status)   cmd_status "$@" ;;
    down)     cmd_down "$@" ;;
    -h|--help|"") usage 0 ;;
    *) die "unknown command: $command (try --help)" ;;
  esac
}

main "$@"
