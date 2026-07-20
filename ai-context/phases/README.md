# Phase Tracker Index

This folder tracks implementation by phase.

Each phase file contains:

- Purpose.
- Scope.
- Out-of-scope items.
- Task checklist.
- Recommended model effort per task.
- Validation requirements.
- Completion status.

Status values:

- `[ ]` Not started.
- `[~]` In progress.
- `[x]` Complete.
- `[!]` Blocked.

Model effort values:

- Medium: simple docs, small isolated code, formatting, straightforward tests.
- High: architecture, Django settings, Docker, Celery, database wiring, API structure, test architecture.
- Extra High: authentication, authorization, SpiceDB, Google Drive permission inheritance, provenance, retrieval filtering, data-leak prevention.

## Phase Files

- `phase-0-repository-and-infrastructure.md`
- `phase-1-django-foundation.md`
- `phase-2-google-drive-ingestion.md`
- `phase-3-neo4j-graph-and-provenance.md`
- `phase-4-spicedb-permissions.md`
- `phase-5-permission-safe-retrieval.md`
- `phase-6-open-webui-integration.md`
- `phase-7-change-feed-and-evaluation.md`
- `phase-8-deployment-handoff.md`
- `phase-9-production-hardening.md`
