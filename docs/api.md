# Backend API

This document summarizes the operator-facing ingestion endpoints. Per-user
OAuth is the only supported POC permission mode; see the Phase 6 docs for its
session, consent, status, disconnect, and Open WebUI query contracts.

## Authentication

`GET /api/health/` is public. Every other current endpoint uses Django REST
Framework session authentication and requires an authenticated staff user
(`IsAdminUser`). Unsafe requests such as `POST` also require a valid CSRF cookie
and matching `X-CSRFToken` header.

Interactive endpoints use the trusted Google/OIDC-backed Django session. The
Open WebUI compatibility route uses its dedicated backend service credential
plus signed trusted identity; neither contract accepts a caller-supplied user
email as permission authority.

## Implemented endpoints

| Method | Path | Access | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/health/` | Public | Report controlled status for Django, PostgreSQL, Redis, Neo4j, and SpiceDB. |
| `POST` | `/api/tasks/smoke-test/` | Admin | Queue the Celery smoke task. Operators normally use `make smoke` instead. |
| `GET` | `/api/ingest/drive/roots/` | Admin | List Drive roots visible to the configured service account. |
| `POST` | `/api/ingest/drive/connection/root/` | Admin | Persist a visible folder/shared-drive root and invalidate documents after a scope change. |
| `POST` | `/api/ingest/drive/sync/` | Admin | Queue a Drive content/metadata sync for the server-configured root. Request-body scope values are ignored. |

## Request bodies

Select a Drive root returned by the root-list endpoint:

```json
{
  "scope_type": "folder",
  "root_id": "drive-resource-id"
}
```

`scope_type` must be `folder` or `shared_drive`. The backend verifies that the
resource appears in the service account's visible root candidates before
persisting it.

The Drive-sync endpoint accepts no client-selected scope. It always reads the
enabled `DriveConnection` and selected root from server-side state.

## Common responses

Successful sync triggers return HTTP `202 Accepted` with a durable `run_id`.

The old delegated-subject, ACL-readiness, and delegated permission-sync routes
are retained only in dormant code and are not registered by supported POC
configuration.

## Not implemented

The following interfaces are targets for later phases and must not be presented
as available today:

- `/api/query/`
- `/v1/models`
- `/v1/chat/completions`
- `/api/eval/run/`
- Local-folder ingestion
- Bearer API-key authentication
- Google/OIDC login and Open WebUI identity propagation

See `ai-context/phases/phase-5-permission-safe-retrieval.md` and
`ai-context/phases/phase-6-open-webui-integration.md` for their acceptance
criteria.
