# Phase 1 Execution Plan

This file defines the first implementation phase for AI agents.

Phase 1 is not Google Drive ingestion, graph extraction, permissions, or Open WebUI chat.
Phase 1 is the backend and infrastructure foundation that makes those later features safe to build.

## Phase 1 Goal

Create a reliable Django backend foundation that runs in Docker, connects to the required backing services, exposes health checks, runs background jobs, and has a repeatable test/lint workflow.

The phase is complete only when the backend can prove:

- Django starts inside Docker.
- DRF exposes versioned API routes.
- PostgreSQL connection works.
- Redis connection works.
- Celery worker can execute a task.
- Neo4j connection works.
- The infrastructure compose files validate.
- Tests and linting run from one command.

## Required Model Effort

Use the following model-effort policy when assigning work to AI models:

- Medium effort is acceptable for small isolated edits, docs, simple settings, and straightforward tests.
- High effort should be used for Django project structure, Docker wiring, Celery configuration, environment settings, and test architecture.
- Extra-high effort should be used for security-sensitive flows, permission enforcement, SpiceDB modeling, retrieval filtering, provenance logic, and anything that could leak private client data.

Phase 1 mostly requires medium to high effort.
Do not use extra-high for every small edit. Save it for design-critical or security-critical decisions.

## Task 1: Create Backend Skeleton

Create:

```text
apps/backend/
  manage.py
  pyproject.toml
  Dockerfile
  config/
  core/
  health/
  integrations/
```

Rules:

- Use Django and Django REST Framework.
- Keep app code under `apps/backend/`.
- Use environment variables for all runtime configuration.
- Do not hardcode secrets, hostnames, credentials, API keys, or customer IDs.
- Keep settings split clearly enough for local and production behavior.

Validation:

- `python manage.py check` passes.
- `python manage.py test` runs.
- Django imports without errors.
- No secrets are committed.

Recommended model effort: high.

## Task 2: Add Python Tooling

Add:

- Ruff for linting and formatting.
- pytest.
- pytest-django.
- coverage configuration if low effort.

Rules:

- Use `pyproject.toml` as the primary Python tooling config.
- Keep line length and target Python version explicit.
- Do not add heavy tools before they are useful.

Validation:

- Ruff check passes.
- Ruff format check passes.
- Pytest passes.

Recommended model effort: medium.

## Task 3: Wire Docker App Compose

Create or finalize:

```text
infra/compose.app.yml
```

The app compose file should define:

- Django web service.
- Celery worker service.
- Optional Celery beat service, if needed for scheduled jobs.

Rules:

- Reuse PostgreSQL, Redis, Neo4j, SpiceDB, and Traefik from infrastructure compose.
- Do not duplicate infrastructure services in the app compose file.
- Use health checks where practical.
- Run the web container as a non-root user where practical.

Validation:

- `docker compose -f infra/compose.infrastructure.yml config` passes.
- `docker compose -f infra/compose.infrastructure.yml -f infra/compose.app.yml config` passes.
- Backend container can start.

Recommended model effort: high.

## Task 4: Add Database Configuration

Configure Django to use PostgreSQL.

Rules:

- Use environment variables for database name, user, password, host, and port.
- Do not use SQLite as the project default.
- Keep migrations in source control.

Validation:

- `python manage.py migrate` succeeds in Docker.
- Django can query the database.
- Database credentials come from `.env`.

Recommended model effort: high.

## Task 5: Add Health API

Create:

```text
GET /api/health/
```

The response should include:

```json
{
  "status": "ok",
  "services": {
    "django": "ok",
    "postgres": "ok",
    "redis": "ok",
    "neo4j": "ok"
  }
}
```

Rules:

- Health checks must not expose secrets.
- Failures should return controlled error labels, not stack traces.
- Use timeouts for external service checks.

Validation:

- API returns HTTP 200 when all services are available.
- API returns degraded/error status when a dependency is unavailable.
- Tests cover success and at least one failure path.

Recommended model effort: high.

## Task 6: Add Celery Smoke Task

Create:

```text
POST /api/tasks/smoke-test/
```

The endpoint should enqueue a simple Celery task and return a task ID.

Rules:

- The task must be idempotent.
- Do not pass complex Django model objects to Celery.
- Pass primitive IDs or values only.
- Do not block the request waiting for task completion.

Validation:

- Endpoint returns a Celery task ID.
- Worker logs show task execution.
- A test verifies task registration or eager-mode execution.

Recommended model effort: medium-high.

## Task 7: Add Makefile Commands

Add commands such as:

```text
make config
make up
make down
make logs
make migrate
make test
make lint
make format
```

Rules:

- Commands should use Docker where possible.
- Commands should be understandable by future AI agents and human developers.

Validation:

- Each command either works or clearly documents prerequisites.

Recommended model effort: medium.

## Task 8: Commit Phase 1 Foundation

After all validation passes:

- Commit with a clear message.
- Push to `main` only if the user wants direct main pushes.
- Otherwise create a feature branch.

Recommended commit message:

```text
Build Django backend foundation
```

Validation before commit:

- `git status` reviewed.
- No `.env`, credentials, generated cache, database volumes, or logs staged.
- Compose config passes.
- Tests pass.
- Lint passes.

Recommended model effort: medium.

## Phase 1 Completion Criteria

Phase 1 is complete when this works from a clean checkout:

```bash
cp .env.example .env
make config
make up
make migrate
make test
make lint
```

And:

```text
GET /api/health/
```

returns dependency status without leaking sensitive configuration.

