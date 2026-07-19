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

Celery beat also enqueues a run per ready connection every
`PERMISSION_SYNC_INTERVAL_SECONDS` (default 900). This is the healthy refresh
cadence for Google Group membership changes: removing a member changes no
document ACL hash, so only a reconciliation run deletes the stale member tuple.
A companion sweeper fails runs stuck in RUNNING after
`PERMISSION_SYNC_STALE_RUN_TIMEOUT_MINUTES`.

Successful verification evidence is trusted for at most
`PERMISSION_VERIFICATION_MAX_AGE_SECONDS` (default 1800). This value must be
greater than the sync interval. Once the evidence expires, query-time lookup
denies the document even if an old SpiceDB tuple remains. This makes the
maximum age—not an indefinitely retrying scheduler—the hard revocation bound
when permission runs repeatedly fail. Running Celery beat is therefore required
for retrieval to remain continuously available.

Every run reconciles the exact connection-scoped tuple set, verifies it at
least as fresh as the final write, and commits eligibility only when the
document ACL version still matches (documents with no effective grant path
are excluded). A failed run keeps the previous verified state rather than
blanking the connection only until its evidence expires — the fully consistent
SpiceDB lookup remains the query-time gate either way. Incomplete ACLs/groups,
public/domain principals, hierarchy cycles, SpiceDB errors, verification
mismatches, and expired evidence deny access.

## Phase 5 Handoff

Phase 5 must obtain its Neo4j source-document allowlist only through:

```python
from authorization.lookup import allowed_source_document_ids

source_document_ids = allowed_source_document_ids(authenticated_user_email)
```

The function performs fully consistent SpiceDB `LookupResources` calls and
maps only opaque returned resources to active, retrieval-eligible rows with a
matching verified ACL version and an unexpired verification timestamp. Any
required SpiceDB call failure returns `()`. An empty tuple always means deny
all; callers must never interpret it as “omit the filter.” PostgreSQL supplies
synchronization evidence only and never makes the allow decision.
