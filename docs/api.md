# Backend API

This document describes the endpoints implemented in the current Django + DRF
backend through Phase 4. Phase 5 query and Phase 6 Open WebUI endpoints are not
implemented yet.

## Authentication

`GET /api/health/` is public. Every other current endpoint uses Django REST
Framework session authentication and requires an authenticated staff user
(`IsAdminUser`). Unsafe requests such as `POST` also require a valid CSRF cookie
and matching `X-CSRFToken` header.

The backend does **not** currently support `Authorization: Bearer
<BACKEND_API_KEY>`. Production Google/OIDC authentication and trusted identity
propagation belong to Phase 6.

## Implemented endpoints

| Method | Path | Access | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/health/` | Public | Report controlled status for Django, PostgreSQL, Redis, Neo4j, and SpiceDB. |
| `POST` | `/api/tasks/smoke-test/` | Admin | Queue the Celery smoke task. Operators normally use `make smoke` instead. |
| `GET` | `/api/ingest/drive/roots/` | Admin | List Drive roots visible to the configured service account. |
| `POST` | `/api/ingest/drive/connection/root/` | Admin | Persist a visible folder/shared-drive root and invalidate documents after a scope change. |
| `POST` | `/api/ingest/drive/connection/delegated-subject/` | Admin | Set or clear the delegated Workspace subject and invalidate existing retrieval eligibility when it changes. |
| `GET` | `/api/ingest/drive/permissions/check/` | Admin | Return controlled counts showing whether permission metadata is readable under the selected root. |
| `POST` | `/api/ingest/drive/sync/` | Admin | Queue a Drive content/metadata sync for the server-configured root. Request-body scope values are ignored. |
| `POST` | `/api/permissions/sync/` | Admin | Queue a permission-only Drive-to-SpiceDB reconciliation. |
| `GET` | `/api/permissions/sync/{run_id}/` | Admin | Return controlled permission-run status and counts. |

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

Set a delegated Workspace subject:

```json
{
  "delegated_subject_email": "workspace-operator@example.com"
}
```

An empty string clears the delegated subject. Changing the value marks
currently eligible documents ineligible until their permissions are refreshed.

The Drive-sync and permission-sync POST endpoints accept no client-selected
scope. They always read the enabled `DriveConnection` and selected root from
server-side state.

## Common responses

The permission-readiness endpoint returns fields including:

```json
{
  "connection_id": 1,
  "permission_metadata_access": "ok",
  "sampled_files": 10,
  "permissions_readable": 10,
  "permissions_unreadable": 0,
  "folder_listing_errors": 0,
  "checked_all_available_files": true
}
```

`permission_metadata_access` is one of `ok`, `partial`, `blocked`,
`listing_failed`, or `no_files`.

Successful sync triggers return HTTP `202 Accepted` with a durable `run_id`.
The permission-run detail endpoint returns controlled counts and an error code;
it never returns document names, Drive IDs, principals, ACL payloads, or raw
exceptions.

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
