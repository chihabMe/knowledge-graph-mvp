# SpiceDB Permission Sync

Phase 4 synchronizes the selected Google Drive scope into SpiceDB without
exporting content or starting retrieval.

## Operations

Apply or verify the checked-in schema:

```bash
python manage.py spicedb_schema_apply
python manage.py spicedb_schema_check
```

`POST /api/permissions/sync/` creates and queues an admin-only audit run. The
request body cannot select Drive scope. `GET /api/permissions/sync/{run_id}/`
returns controlled counts/status only. Neither endpoint exposes document
names, Drive IDs, principals, ACL payloads, connection details, or raw errors.

Every run invalidates candidate documents before tuple mutation, reconciles
the exact connection-scoped tuple set, verifies it at least as fresh as the
final write, and restores eligibility only when the document ACL version still
matches. Incomplete ACLs/groups, public/domain principals, hierarchy cycles,
SpiceDB errors, and verification mismatches deny access.

## Phase 5 Handoff

Phase 5 must obtain its Neo4j source-document allowlist only through:

```python
from authorization.lookup import allowed_source_document_ids

source_document_ids = allowed_source_document_ids(authenticated_user_email)
```

The function performs fully consistent SpiceDB `LookupResources` calls and
maps only opaque returned resources to active, retrieval-eligible rows with a
matching verified ACL version. Any required SpiceDB call failure returns `()`.
An empty tuple always means deny all; callers must never interpret it as “omit
the filter.” PostgreSQL supplies synchronization evidence only and never makes
the allow decision.
