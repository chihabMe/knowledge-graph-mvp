# AGENTS.md

This file is the entry point for future AI agents working on this repository.

## Required Reading

Always read before making any change:

1. `ai-context/00-project-overview.md`
2. `ai-context/03-implementation-rules.md`
3. The current phase tracker in `ai-context/phases/` (see `README.md` for
   which phase is active).

Read on demand, when the task touches that area:

- `ai-context/01-architecture.md` — infrastructure, Docker, or service wiring.
- `ai-context/02-task-backlog.md` — planning or reprioritizing work.
- `ai-context/04-decisions.md` — before proposing a stack or design change.
- `ai-context/05-test-and-acceptance.md` — writing or changing tests.
- `ai-context/07-ai-coding-security-rules.md` — anything touching auth,
  permissions, ingestion, retrieval, or secrets. When in doubt, read it.
- `AGENT_PROJECT_BRIEF.md` — the full canonical brief; read for any
  non-trivial feature work.

Archived material lives in `ai-context/archive/` — historical reference only.

## Source Of Truth

`AGENT_PROJECT_BRIEF.md` is the canonical document for project scope, stack,
data contracts, and rules. The `ai-context/` files, `README.md`, and
`docs/project-plan.md` are working summaries of it.

When a fact changes (stack, scope, phase status, a rule), update
`AGENT_PROJECT_BRIEF.md` first, then update any summary file that repeats the
fact. If two documents disagree, `AGENT_PROJECT_BRIEF.md` wins — and the
disagreement itself is a bug: fix the stale copy in the same change.

## Manual Audit Only

Do not run repository audit commands automatically. The human operator decides
when an audit is needed and will ask for it explicitly.

Do not fix or dismiss audit findings unless the human operator explicitly asks
for that follow-up work.

## Daily Report (Write Before Ending A Work Session)

Before the final commit of a work session, write or update
`docs/daily-reports/YYYY-MM-DD.md` (today's date). Keep it short and factual:

- **What changed** — the commits/work of the day, one line each.
- **Decisions** — anything a future agent or the client would need to know.
- **Next steps** — where the next session should start.

If a report for today already exists, append to it instead of overwriting.
This is the running project journal; the phase trackers hold task status, the
daily report holds the narrative.

## Working Rules

- Do not treat this as a normal chatbot project. The core product is permission-safe retrieval over a Google Drive-backed knowledge graph.
- Do not send unrestricted graph or document context to any LLM.
- Preserve provenance on all graph facts, chunks, nodes, and relationships.
- Use SpiceDB for authorization. Do not replace it with ad hoc PostgreSQL permission checks.
- Use PostgreSQL for application metadata and job state.
- Use Neo4j for graph data, graph traversal, and vector retrieval.
- Use Django + Django REST Framework for the main backend.
- Use Celery workers for ingestion, sync, extraction, and indexing. The POC
  evaluation runner is an operator command; scheduled evaluation is deferred.
- Keep Docker Compose as the first deployment target.
- Keep Open WebUI as the user-facing chat interface unless the user explicitly changes that direction.

## Repository Map

- `ai-context/`: Canonical markdown files for AI agents.
- `ai-context/phases/`: Phase-by-phase implementation trackers with task status and recommended model effort.
- `docs/`: Human-facing docs, API notes, and feature plans.
- `infra/`: Docker, Traefik, monitoring, and deployment configuration.
- `apps/backend/`: Django backend.
- `data/import/`: Local sample ingestion files for development and tests.
- `AGENT_PROJECT_BRIEF.md`: Detailed project brief from the planning phase.

## Graphify Usage

Graphify is used as a local AI navigation tool, not as a runtime dependency.

- Generated Graphify output is ignored by Git.
- Backend code graph location: `apps/backend/graphify-out/`.
- For backend codebase or architecture questions, query the graph before broad
  searches or multi-file reads. Run queries from `apps/backend/` and keep the
  result small; then open the source files needed to verify or change code:

```bash
graphify query "SmokeTaskView HealthView urlpatterns" --budget 800
```

- The local post-commit and post-checkout hooks refresh this backend-only graph
  after code changes, using AST extraction only. For uncommitted meaningful
  backend changes, or if a refresh fails, update it manually:

```bash
graphify update apps/backend
```

- A full repository graph requires an LLM API key because the repo contains markdown documentation. Do not run full-repo semantic extraction over client data unless the user explicitly approves the model/backend.

## Current Architecture Status

The backend foundation and controlled Google Drive ingestion code are built.
Phase 3 graph construction is code complete and merged into `main`: the graph
app, ontology, Neo4j setup, extraction adapter, document, chunk, entity, and
relationship writers, provenance guard, vector-index setup, and extraction
recovery hardening are in place.

Phase 5 permission-safe retrieval is code complete and live validated. The
authenticated query path performs SpiceDB authorization before embeddings or
Neo4j, composes the allowlist and provenance guard into keyword/vector/one-hop
retrieval, assembles bounded context, calls OpenRouter behind a service
boundary, and keeps citations server-owned.

Phase 6 Open WebUI integration with admin-approved per-user Drive OAuth is
complete: the live two-user acceptance matrix passed through the real Open
WebUI route and the operator approved formal closeout on 2026-07-19.

The next implementation steps are:

1. Execute Phase 8 from
   `ai-context/phases/phase-8-deployment-handoff.md`: deployment, backup,
   restore, maintenance, retention, troubleshooting, clean-server validation,
   demo, and client handoff documentation.
2. Keep `.env.example` and tracked docs OpenRouter-shaped.
3. Preserve provenance on every graph write and keep retrieval fail-closed.
4. Treat delegated ACL/group sync as dormant implementation only: do not expose,
   document, deploy, or test it as a POC completion requirement, and never union
   it with ADR-015 per-user relationships or fall back to it automatically.
