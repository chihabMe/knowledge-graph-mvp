# AI Coding And Security Rules

This file contains implementation rules for AI agents writing code in this repository.

The rules are based on:

- Django official deployment checklist.
- Django REST Framework authentication, permissions, and throttling guidance.
- OWASP Secure Coding Practices.
- OWASP Docker Security Cheat Sheet.
- Celery security guidance.
- Neo4j Python driver guidance.
- Twelve-Factor App configuration guidance.
- Ruff configuration guidance.

## Main Security Principle

This project handles private organizational knowledge.

Every feature must assume:

- Source documents may be confidential.
- Graph facts may reveal confidential information.
- A visible node can be connected to restricted facts.
- Retrieval bugs can become data leaks.

Security is not an add-on. Permission filtering and provenance are core product behavior.

## General AI Agent Rules

- Read `AGENTS.md` before editing.
- Read the relevant `ai-context/` file before editing.
- Make small, verifiable changes.
- Validate after each task before starting the next one.
- Do not invent architecture that conflicts with documented decisions.
- Do not introduce services, SaaS dependencies, or frameworks without updating `ai-context/04-decisions.md`.
- Do not write secrets into code, tests, docs, examples, logs, commits, or comments.
- Do not log API keys, OAuth tokens, service-account credentials, Drive file contents, answer context, or full prompts.
- Prefer boring, explicit code over clever abstractions.

## Django Rules

- Use environment variables for `SECRET_KEY`, database settings, allowed hosts, debug mode, CORS, CSRF, external URLs, and credentials.
- `DEBUG` must default to false outside local development.
- Never expose stack traces in API responses.
- Run Django checks during validation.
- Before production deployment, run:

```bash
python manage.py check --deploy
```

- Use Django ORM for relational data unless raw SQL is clearly justified.
- If raw SQL is required, use parameterized queries only.
- Validate all incoming API data with serializers or typed validation.
- Keep business logic out of views when it becomes non-trivial.

## DRF API Rules

- Use explicit authentication classes.
- Use explicit permission classes.
- Default API behavior should deny access unless allowed.
- Add throttling before exposing public or semi-public endpoints.
- Do not trust `user_email` from request JSON for protected endpoints once authentication is implemented.
- Use authenticated identity from Google/OIDC/Open WebUI integration when available.
- Return controlled error messages.
- Do not leak whether a restricted document exists.

## Celery Rules

- Tasks must be idempotent wherever possible.
- Pass primitive IDs and small payloads to tasks.
- Do not pass Django model instances to tasks.
- Set retry limits for retryable external failures.
- Use separate queues later for ingestion, permissions, extraction, retrieval, and evaluation if load requires it.
- Do not store secrets in task payloads.
- Do not log raw document contents.
- Use Redis as broker/result backend for the MVP unless a later decision changes this.

## Docker Rules

- Keep images minimal.
- Do not bake `.env`, service account JSON, API keys, or customer data into images.
- Prefer non-root container users for application containers.
- Do not run privileged containers unless explicitly justified.
- Use named volumes for persistent service data.
- Keep app containers stateless.
- Treat PostgreSQL, Neo4j, Redis, and SpiceDB as attached backing services.
- Validate compose files before committing.

## PostgreSQL Rules

- Use PostgreSQL for application metadata, job state, integration records, and Django auth/session/admin tables.
- Do not store raw full document text in PostgreSQL unless a later design explicitly requires it.
- Use migrations for schema changes.
- Do not make schema changes without tests or a migration sanity check.

## Neo4j Rules

- Use Neo4j for graph data, document/chunk graph representation, entity relationships, graph traversal, and vector retrieval.
- Every graph node and relationship derived from source material must include provenance.
- Do not create graph facts without source document references.
- Use transactions for multi-step graph writes.
- Do not fetch large graph result sets all at once.
- Add constraints and indexes intentionally.
- Retrieval queries must be designed for permission filtering by source provenance.

## SpiceDB And Permission Rules

- SpiceDB is mandatory for v1 permission enforcement.
- Do not replace SpiceDB with ad hoc PostgreSQL permission checks.
- Permission checks happen before retrieval.
- Neo4j retrieval must be filtered to allowed source documents.
- If provenance is incomplete, default to deny.
- If a graph fact comes from multiple source documents, require all required source documents to be visible unless a later documented policy says otherwise.
- Add leak tests for every retrieval feature.

## Google Drive Rules

- First pilot assumes Google Workspace service-account access with domain-wide delegation.
- Store Drive metadata needed for provenance and sync:
  - Drive file ID.
  - URL.
  - Title.
  - MIME type.
  - Modified time.
  - Content hash.
  - Folder path.
  - Permissions version.
- Separate content updates from permission-only updates.
- Do not re-embed documents when only permissions changed.
- Never log downloaded document content.

## OpenRouter And LLM Rules

- Never send unrestricted graph context to an LLM.
- Send only context that passed SpiceDB filtering.
- Include citations for facts in generated answers.
- If context is insufficient, say so.
- If user lacks access, refuse without revealing hidden facts.
- Keep prompts and context compact.
- Avoid storing full prompts unless explicitly needed for evaluation and sanitized.

## Testing Rules

Each feature should include tests appropriate to its risk.

Minimum test categories:

- Unit tests for pure logic.
- API tests for endpoints.
- Integration tests for service wiring where practical.
- Permission leak tests for retrieval behavior.
- Regression tests for bugs.

Phase 1 required tests:

- Health endpoint success.
- Health endpoint dependency failure behavior.
- Celery smoke task registration or eager execution.
- Django settings do not require secrets in test mode.

Later required tests:

- User A can retrieve allowed facts.
- User B cannot retrieve restricted facts.
- Restricted facts do not leak through graph traversal.
- Permission-only changes update SpiceDB without re-embedding.
- Content changes re-index and update Neo4j.

## Validation Rules

After making code changes, stage the intended change set and run the review gate:

```bash
make review-staged
```

Then read `REVIEW.md`, present the findings to the user, and wait for the user
to choose whether Codex should fix the findings or ignore them for now.

Before committing code, run the relevant subset:

```bash
make config
make lint
make test
docker compose -f infra/compose.infrastructure.yml config
docker compose -f infra/compose.infrastructure.yml -f infra/compose.app.yml config
```

If Django code changed, also run:

```bash
python manage.py check
```

If deployment/security settings changed, also run:

```bash
python manage.py check --deploy
```

## When To Use Higher Model Effort

Use high effort for:

- Django project structure.
- Settings and environment design.
- Docker and Compose changes.
- Celery worker setup.
- Database model design.
- API design.
- Test architecture.

Use extra-high effort for:

- SpiceDB schema design.
- Google Drive permission inheritance.
- Permission-safe retrieval.
- Graph provenance rules.
- Neo4j query filters that enforce visibility.
- OpenWebUI/OpenRouter context assembly.
- Any code path that decides what private information a user can see.

Do not use extra-high effort for:

- Simple docs edits.
- Renaming files.
- Basic endpoint boilerplate.
- Formatting.
- Small test fixes.

## External References

- Django deployment checklist: https://docs.djangoproject.com/en/6.0/howto/deployment/checklist/
- DRF authentication: https://www.django-rest-framework.org/api-guide/authentication/
- DRF permissions: https://www.django-rest-framework.org/api-guide/permissions/
- DRF throttling: https://www.django-rest-framework.org/api-guide/throttling/
- OWASP Secure Coding Practices: https://owasp.org/www-project-secure-coding-practices-quick-reference-guide/stable-en/02-checklist/
- OWASP Docker Security Cheat Sheet: https://cheatsheetseries.owasp.org/cheatsheets/Docker_Security_Cheat_Sheet.html
- Celery security: https://docs.celeryq.dev/en/v5.6.0/userguide/security.html
- Neo4j Python driver manual: https://neo4j.com/docs/python-manual/current/
- Twelve-Factor App config: https://12factor.net/config
- Ruff configuration: https://docs.astral.sh/ruff/configuration/
