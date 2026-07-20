# Test And Acceptance Plan

## POC Acceptance Criteria

- Google Drive files are ingested.
- Document metadata is stored in PostgreSQL.
- Graph facts are stored in Neo4j.
- Every graph fact has source provenance.
- Drive permissions are synced into SpiceDB.
- Retrieval asks SpiceDB for allowed documents before querying Neo4j.
- Restricted facts do not reach the LLM.
- Answers include source citations.
- Restricted or missing context produces safe refusal behavior.
- Leak tests pass.

## Required Test Categories

### Ingestion Tests

- Google Doc ingestion.
- Google Sheet ingestion.
- PDF ingestion.
- Uploaded file ingestion where supported.
- Content hash prevents unnecessary re-indexing.

### Provenance Tests

- Document nodes have required metadata.
- Chunk nodes have source document IDs.
- Entity nodes have source document IDs.
- Relationship edges have source document IDs.
- Missing provenance excludes graph item from retrieval.

### Permission Tests

- Admin approval without user consent produces no grant.
- OAuth callback rejects bad/replayed state, wrong audience/domain, unverified
  email, and missing Drive scope.
- Stored refresh credentials are encrypted and never serialized or logged.
- Visibility sync checks only already-indexed file IDs and never enumerates the
  user's Drive or downloads content.
- User with access retrieves answer.
- User without access does not retrieve answer.
- Group-based and folder-inherited access work when Google confirms the
  connected user's visibility to the indexed file.
- Permission-only change updates SpiceDB without re-embedding.
- Expired permission-verification evidence denies access even when a stale
  SpiceDB grant remains.
- OAuth disconnect, token revocation/refresh failure, account/root/mode change,
  and per-user evidence expiry deny even when a stale SpiceDB tuple remains.
- One user's visibility refresh cannot touch another user's evidence or tuples.
- Dormant delegated ACL tests are regression coverage only; live delegated
  validation is not a POC acceptance or completion requirement.

### Retrieval Tests

- Vector search returns relevant chunks.
- Graph traversal finds related entities.
- Hybrid retrieval returns cited context.
- Restricted context is filtered before LLM call.

### Leak Tests

- Restricted document answer is refused.
- Restricted graph-path answer is refused.
- Citation list never includes inaccessible files.
- LLM prompt payload never includes inaccessible chunks.
- `run_evaluation --dataset-dir <private-dir>` checks positive answers and
  citations plus both the allowed and denied sides of every leak case.
- Evaluation output contains case IDs, pass/fail reason codes, counts, and
  timings only; it never prints or persists client questions, answers,
  identities, or source content.

### Operations Tests

- `GET /api/health/` checks the currently integrated core services.
- Celery worker is running.
- Celery Beat is running.
- Redis is reachable.
- PostgreSQL is reachable.
- Neo4j is reachable.
- SpiceDB is reachable.
- Scheduled Drive content reconciliation completes at the configured interval.
- Freshness health reports content-sync age/failure without identifiers.
