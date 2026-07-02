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

## Working Rules

- Do not treat this as a normal chatbot project. The core product is permission-safe retrieval over a Google Drive-backed knowledge graph.
- Do not send unrestricted graph or document context to any LLM.
- Preserve provenance on all graph facts, chunks, nodes, and relationships.
- Use SpiceDB for authorization. Do not replace it with ad hoc PostgreSQL permission checks.
- Use PostgreSQL for application metadata and job state.
- Use Neo4j for graph data, graph traversal, and vector retrieval.
- Use Django + Django REST Framework for the main backend once the backend is rebuilt.
- Use Celery workers for ingestion, sync, extraction, indexing, and evaluation jobs.
- Keep Docker Compose as the first deployment target.
- Keep Open WebUI as the user-facing chat interface unless the user explicitly changes that direction.

## Repository Map

- `ai-context/`: Canonical markdown files for AI agents.
- `ai-context/phases/`: Phase-by-phase implementation trackers with task status and recommended model effort.
- `docs/`: Human-facing docs, API notes, and feature plans.
- `infra/`: Docker, Traefik, monitoring, and deployment configuration.
- `apps/backend/`: Target location for the Django backend once implementation starts.
- `data/import/`: Local sample ingestion files for development and tests.
- `AGENT_PROJECT_BRIEF.md`: Detailed project brief from the planning phase.

## Current Architecture Status

The target architecture is Django-based. The previous FastAPI prototype has been removed so the project can start clean.

The next implementation step should be a controlled Django foundation build:

1. Create Django project/app structure.
2. Add DRF APIs.
3. Add PostgreSQL-backed app models.
4. Add Celery tasks.
5. Connect Neo4j and SpiceDB through service modules.
6. Add tests around provenance and permission safety.
