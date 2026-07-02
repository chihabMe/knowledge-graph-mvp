# AGENTS.md

This file is the entry point for future AI agents working on this repository.

Before making changes, read these files in order:

1. `ai-context/00-project-overview.md`
2. `ai-context/01-architecture.md`
3. `ai-context/02-task-backlog.md`
4. `ai-context/03-implementation-rules.md`
5. `ai-context/04-decisions.md`
6. `ai-context/05-test-and-acceptance.md`
7. `ai-context/06-phase-1-execution-plan.md`
8. `ai-context/07-ai-coding-security-rules.md`
9. `AGENT_PROJECT_BRIEF.md`

## Pre-Commit Review Workflow (Read This Before Every Commit)

This repository has an offline pre-commit hook that acts as a senior engineer
review gate. It runs automatically on `git commit` — you cannot skip it without
an explicit override.

**Before every commit, you must:**

1. Check if `REVIEW.md` exists in the repo root.
   - If it exists, read it. It contains findings from the previous review.
   - Address all **❌ Critical** items before staging new changes.
   - Address **⚠️ Warning** items in the same or next commit.

2. Stage your changes with `git add`.

3. Run `git commit`. The pre-commit hook fires automatically and will:
   - **Stage 1 — Static checks** (offline, always runs):
     - Ruff lint and format checks on staged Python files.
     - Hardcoded secrets / DEBUG=True / raw SQL / stack trace leak scan.
     - Neo4j provenance field check.
     - SpiceDB permission bypass check.
     - Celery task model-passing check.
     - Docker Compose validation.
     - pytest (skip with `SKIP_TESTS=1 git commit ...`).
   - **Stage 2 — AI deep review** (calls agy/Claude, requires CLI):
     - Reads the staged diff and produces a structured senior engineer review.
     - Supplements the static checks with reasoning about design and correctness.
   - Writes all findings to `REVIEW.md`.
   - **Blocks the commit** if any critical issue is found.

4. After the hook runs — whether the commit passed or was blocked:
   - Read `REVIEW.md` in full.
   - **Present the findings to the user clearly.** Show them the critical issues,
     warnings, and the AI review verdict.
   - **Ask the user:** "Do you want me to fix these issues? (yes/no)"
   - **Wait for the user's explicit answer before touching any code.**
   - If the user says **yes** → apply the fixes, then retry the commit.
   - If the user says **no** → leave the code as-is and note open warnings.

**Critical rule: Never fix review findings silently or automatically.**
**Never use `--no-verify` or `SKIP_REVIEW=1` unless the user explicitly says so.**

To reinstall the hook after a fresh clone:
```bash
make install-hooks
```

## Working Rules

- Do not treat this as a normal chatbot project. The core product is permission-safe retrieval over a Google Drive-backed knowledge graph.
- Do not send unrestricted graph or document context to any LLM.
- Preserve provenance on all graph facts, chunks, nodes, and relationships.
- Use SpiceDB for authorization. Do not replace it with ad hoc PostgreSQL permission checks.
- Use PostgreSQL for application metadata and job state.
- Use Neo4j for graph data, graph traversal, and vector retrieval.
- Use Django + Django REST Framework for the main backend.
- Use Celery workers for ingestion, sync, extraction, indexing, and evaluation jobs.
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
- For backend architecture questions, run Graphify queries from `apps/backend/`:

```bash
graphify query "SmokeTaskView HealthView urlpatterns"
```

- To refresh the backend graph after meaningful backend changes:

```bash
graphify apps/backend
graphify cluster-only apps/backend
```

- A full repository graph requires an LLM API key because the repo contains markdown documentation. Do not run full-repo semantic extraction over client data unless the user explicitly approves the model/backend.

## Current Architecture Status

The backend foundation has been built with Django + Django REST Framework.

The next implementation step should be controlled Google Drive ingestion work:

1. Add PostgreSQL-backed Drive connection and sync models.
2. Add Google Drive credential configuration.
3. Add mocked Drive API client tests before real API calls.
4. Store Drive metadata before downloading content.
5. Preserve future permission/provenance requirements in every model.
