# Client-Owned AI Knowledge Graph POC

This project is a client-owned organizational knowledge graph system.

The proof of concept lets users ask questions through Open WebUI, retrieves context from Google Drive content structured in Neo4j, and enforces source-document visibility before retrieval.

The main success test is permission safety:

```text
Users must not receive facts derived from Google Drive files they cannot access.
```

## Current Stack

- Host: Ubuntu VM
- Deployment: Docker Compose
- Backend: Django + Django REST Framework
- Background jobs: Celery
- Broker/cache: Redis
- App metadata: PostgreSQL
- Knowledge graph: Neo4j
- Authorization engine: SpiceDB
- Reverse proxy: Traefik
- Logs: Dozzle
- Uptime checks: Uptime Kuma
- User chat UI: Open WebUI
- Model gateway: OpenRouter

## Repository Map

- `AGENTS.md`: entry instructions for future AI agents.
- `CLAUDE.md`: auto-loaded entry point for Claude Code; states the core invariants and points to `AGENTS.md`.
- `ai-context/`: canonical project context for AI agents.
- `ai-context/archive/`: completed-phase material kept for historical reference.
- `ai-context/phases/`: phase-by-phase task trackers with completion status and model-effort level.
- `apps/backend/`: Django backend.
- `infra/`: Docker Compose and infrastructure configuration.
- `docs/`: human-facing planning and setup docs.
- `data/import/`: local sample-data folder placeholder.

## Common Commands

Validate all Compose layers. In a fresh clone, this command uses
`.env.example` for interpolation and service `env_file` validation only; it
does not start containers:

```bash
make config
```

Before starting any service, copy and replace the environment defaults:

```bash
cp .env.example .env
```

Runtime commands intentionally require the real `.env` and never use
`.env.example` as deployment configuration.

Start the core development services:

```bash
make up
```

Start the core services with the production image target:

```bash
make up-prod
```

Run migrations:

```bash
make migrate
```

Run tests and linting:

```bash
make test
make lint
make migration-check
```

GitHub CI runs Compose validation, full-history secret scanning, locked
dependency installation, lint/format checks, migration-drift detection, Django
checks, and the backend test suite. The required branch-protection check is
named `Backend validation`.

Check service health:

```bash
make health
```

Queue a Celery smoke task:

```bash
make smoke
```

## Service Startup

`make up` layers `infra/compose.dev.yml` over the base Compose files. It starts
the core services needed for backend development, bind-mounts backend source,
and runs Django with autoreload:

- PostgreSQL
- Redis
- Neo4j
- SpiceDB
- Django
- Celery worker

`make up-all` also starts optional services:

- Open WebUI
- Dozzle
- Uptime Kuma
- Traefik
- Celery beat

Use `make up-prod` or `make up-all-prod` to run the same service sets with the
production Docker target (Gunicorn, root-owned application source, and no bind
mounts).

## Local Ports

Internal services bind to localhost-only alternate ports by default:

- PostgreSQL: `15432 -> 5432`
- Redis: `16379 -> 6379`
- Neo4j HTTP: `17474 -> 7474`
- Neo4j Bolt: `17687 -> 7687`
- SpiceDB gRPC: `15051 -> 50051`
- SpiceDB HTTP: `18443 -> 8443`

## Current Phase

Phases 1 and 2 are code complete (Phase 2's live content-export validation
waits on domain-wide delegation — see ADR-009). Phase 3, Neo4j graph and
provenance, is code complete and merged into `main`; its guard-wiring seam
moved to the Phase 5 tracker. Phase 4 SpiceDB permission sync is code complete
with live delegated Workspace validation pending. Next: Phase 5
permission-safe retrieval.

Read:

- `ai-context/phases/phase-3-neo4j-graph-and-provenance.md`
- `ai-context/phases/phase-4-spicedb-permissions.md`
- `ai-context/phases/phase-2-google-drive-ingestion.md`
- `docs/google-drive-next.md`
- `ai-context/07-ai-coding-security-rules.md`

## Important Rule

Do not build retrieval features that bypass SpiceDB or source-document provenance.
