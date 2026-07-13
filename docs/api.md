# Backend API

The canonical backend is Django + Django REST Framework. The old
FastAPI/local-file prototype interfaces are not implemented by this backend.

This page is a compact index. Detailed contracts and security behavior live in
the linked operator documents and `AGENT_PROJECT_BRIEF.md`.

## Current Interfaces

### Health

```http
GET /api/health/
```

Returns controlled dependency health information.

### Celery smoke test

```http
POST /api/tasks/smoke-test/
```

Queues the bounded development smoke task.

### Drive ingestion administration

```http
GET  /api/ingest/drive/roots/
POST /api/ingest/drive/connection/root/
POST /api/ingest/drive/connection/delegated-subject/
GET  /api/ingest/drive/permissions/check/
POST /api/ingest/drive/sync/
```

These are authenticated administrative interfaces. Drive scope comes from
server-side `DriveConnection` state; callers cannot widen scope through sync
request data.

### Permission synchronization

```http
POST /api/permissions/sync/
GET  /api/permissions/sync/<run_id>/
```

These are authenticated administrative interfaces with controlled audit
responses. See `docs/permission-sync.md`.

### Permission-safe query

```http
POST /api/query/
```

Current request:

```json
{
  "question": "Who owns the technical implementation?"
}
```

Identity comes only from the authenticated Django session. Request fields such
as `user_email` are rejected. SpiceDB authorization and fresh permission
evidence are checked before permission-constrained Neo4j retrieval or any
OpenRouter answer call. See `docs/permission-safe-retrieval.md`.

## Planned Phase 6 Interfaces — Not Yet Implemented

### Model discovery

```http
GET /v1/models
```

Will expose one logical knowledge-graph model to the server-side Open WebUI
connection. It will require a dedicated service bearer key.

### Chat completions

```http
POST /v1/chat/completions
```

Will accept a bounded OpenAI-compatible request from Open WebUI, require the
service bearer key plus a short-lived signed user identity JWT, and adapt the
question to the existing `answer_query()` service. Plain forwarded email
headers and request-body identity will not be trusted.

The selected contract, implementation order, tests, configuration, and live
acceptance gates are documented in `docs/phase-6-implementation-plan.md` and
ADR-014.
