# Phase 1: Django Backend Foundation

## Purpose

Build the backend foundation that later Google Drive, Neo4j, SpiceDB, and Open WebUI features will depend on.

This phase proves that the project can run as a real service:

- Django starts.
- DRF routes work.
- PostgreSQL connects.
- Redis connects.
- Celery executes jobs.
- Neo4j connectivity can be checked.
- Tests and linting are repeatable.
- Docker Compose config remains valid.

## Scope

- Django project under `apps/backend/`.
- DRF base API.
- Environment-driven settings.
- PostgreSQL configuration.
- Redis configuration.
- Celery worker.
- Health endpoint.
- Smoke task endpoint.
- Basic tests.
- Makefile commands.

## Out Of Scope

- Google Drive API integration.
- SpiceDB schema design.
- Open WebUI pipeline.
- OpenRouter calls.
- Graph extraction.
- Permission-safe retrieval.

## Tasks

- [x] Create Django backend skeleton. Effort: High.
- [x] Add Python tooling with Ruff and pytest. Effort: Medium.
- [x] Wire Django and Celery into app Compose file. Effort: High.
- [x] Configure PostgreSQL for Django. Effort: High.
- [x] Add `/api/health/` endpoint. Effort: High.
- [x] Add Celery smoke task and API endpoint. Effort: High.
- [x] Add Makefile commands. Effort: Medium.
- [ ] Run full validation. Effort: Medium.
- [ ] Commit and push Phase 1 foundation. Effort: Medium.

## Validation

Required before Phase 1 is complete:

- [x] `make config`
- [x] `make lint`
- [x] `make test`
- [x] `make up`
- [x] `make migrate`
- [x] `GET /api/health/` returns controlled dependency status.
- [x] Celery smoke task can be queued.
- [ ] `docker compose -f infra/compose.infrastructure.yml config`
- [x] `docker compose -f infra/compose.infrastructure.yml -f infra/compose.app.yml config`

## Completion Status

Not started.
