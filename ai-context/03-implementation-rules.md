# Implementation Rules

## Security Rules

- Never send unrestricted Drive or Neo4j context to an LLM.
- Always filter by SpiceDB before retrieval.
- Every graph node, relationship, and chunk must include source provenance.
- If provenance is missing, exclude the item from retrieval.
- If fact-level provenance is not available, default to strict document-level visibility.
- Do not replace SpiceDB with custom PostgreSQL permission logic.

## Architecture Rules

- Django is the main application backend.
- Django REST Framework exposes APIs.
- Celery handles long-running work.
- Redis is for queue/cache/locks only.
- PostgreSQL stores app metadata, not graph facts.
- Neo4j stores graph facts, relationships, chunks, and vector indexes.
- SpiceDB stores permission relationships.
- Open WebUI is the chat UI.
- Traefik is the reverse proxy.

## Coding Rules For Future Implementation

- Keep integrations behind service modules.
- Do not put Google Drive, Neo4j, SpiceDB, or OpenRouter logic directly in views.
- API views should validate input, call services/tasks, and return responses.
- Celery tasks should be idempotent where possible.
- Store job state in PostgreSQL.
- Use explicit environment variables for secrets and external URLs.
- Add tests for permission-sensitive changes.

## Scope Rules

Do not build these in the first MVP unless explicitly requested:

- Multi-tenant SaaS billing.
- Custom polished frontend.
- Mobile app.
- Local LLM hosting.
- Complex custom admin dashboard.
- Human graph editing UI.
- Kubernetes deployment.

