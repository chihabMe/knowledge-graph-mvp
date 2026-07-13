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

- User with access retrieves answer.
- User without access does not retrieve answer.
- Group-based access works.
- Folder-inherited access works.
- Permission-only change updates SpiceDB without re-embedding.
- Expired permission-verification evidence denies access even when a stale
  SpiceDB grant remains.

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

### Operations Tests

- `GET /health` checks all core services.
- Celery worker is running.
- Celery Beat is running.
- Redis is reachable.
- PostgreSQL is reachable.
- Neo4j is reachable.
- SpiceDB is reachable.
