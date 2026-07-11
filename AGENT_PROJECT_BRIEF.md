# Agent Project Brief: Google Drive Knowledge Graph

This document is the canonical brief for AI agents working on this project.
Read it before building, changing, or planning features.

## 1. Main Purpose

The software is a permission-safe AI knowledge layer for a business.

It connects to an organization's Google Drive, reads business documents, turns
the documents into a structured knowledge graph, and lets employees ask
questions through an AI chat interface.

The system must do more than search documents. It should help the AI understand
relationships between people, projects, procedures, customers, machines, parts,
vendors, policies, and ideas.

The most important promise is:

```text
If a user cannot see the source document in Google Drive, the AI must not use
facts from that document to answer the user.
```

This includes indirect leaks. A restricted fact must not leak through graph
relationships, summaries, embeddings, citations, or related visible documents.

## 2. Product Positioning

This is not a generic chatbot, and it is not only a document search tool.

It is an implementation system that can be deployed for one customer at a time.
Each customer gets an isolated environment:

- Their own Open WebUI instance
- Their own backend
- Their own Neo4j database
- Their own SpiceDB/Postgres permission store
- Their own Google Drive connection
- Their own model/API configuration

No customer data should be mixed in a shared datastore.

The business model is service-led:

- Initial implementation fee
- Optional monthly maintenance
- Client owns the deployment and can move it later

## 3. Current POC Boundary

The current repository is now a Django-based proof-of-concept foundation.
Phase 0 and Phase 1 established the repository, Docker Compose infrastructure,
Django + DRF backend, Celery worker, PostgreSQL, Redis, Neo4j, SpiceDB, health
checks, and repeatable validation commands.

Phase 2 code is complete for controlled Google Drive ingestion: per-client
service-account configuration, admin root selection, server-side scoped sync,
content export/storage, metadata capture, permission-version hashing,
audit records, and post-commit extraction queueing are implemented. Live
Drive validation confirmed the expected folder-sharing limitation:
`permissions.list()` can fail under folder-only sharing, so full permission
metadata capture for the pilot still depends on domain-wide delegation in a
real Workspace.

Phase 3 is active on `phase-3/graph-foundation`: the graph app, ontology,
Neo4j setup, extraction adapter, document/chunk/entity/relationship writers,
source provenance guard, and Chunk vector-index setup are implemented and
covered by tests. The next product-risk dependency is Phase 4 SpiceDB
permission sync, followed by Phase 5 retrieval that composes SpiceDB's
allowed-document list with Neo4j provenance filtering before any LLM call.

Do not reintroduce the old FastAPI/local-file prototype architecture. Django +
DRF + Celery is the canonical backend direction.

## 4. Core Stack

Use these technologies unless the user explicitly changes the direction:

| Layer | Tool | Purpose |
| --- | --- | --- |
| Host environment | Ubuntu VM | Single-customer deployment host |
| Deployment | Docker Compose | Repeatable isolated service stack |
| Chat UI | Open WebUI | User-facing chat interface |
| User login | Google OAuth/OIDC in Open WebUI | User identity must match Google Drive identity |
| Model gateway | OpenRouter | Hosted LLM access and model flexibility |
| Backend | Django + Django REST Framework | Ingestion, retrieval, permission filtering, health endpoints, admin, metadata |
| Background jobs | Celery + Redis | Drive sync, extraction, permission sync, evaluation jobs |
| App metadata | PostgreSQL | Django models, job state, integration records, evaluation records |
| Graph store | Neo4j | Graph nodes, relationships, chunks, vector indexes |
| Extraction/indexing | neo4j-graphrag first | Text extraction, chunking, embeddings, graph extraction |
| Alternative extraction/helper | Graphify / Graphiti | Evaluate behind an adapter; do not make either the core architecture by default |
| Permission engine | SpiceDB | Relationship-based authorization |
| Permission datastore | Postgres | Persistent SpiceDB datastore |
| Reverse proxy | Traefik | TLS, routing, and subdomains |

## 5. High-Level Data Flow

```text
Google Drive
  -> Drive connector
  -> content extraction
  -> ontology-guided graph extraction
  -> Neo4j documents/chunks/entities/relationships/vectors

Google Drive sharing metadata
  -> permission sync
  -> SpiceDB users/groups/folders/files/relationships

Open WebUI question
  -> backend query endpoint
  -> identify logged-in Google user
  -> ask SpiceDB which source documents user may see
  -> query Neo4j only over visible provenance
  -> assemble context with citations
  -> call OpenRouter
  -> return permission-safe answer
```

## 6. Critical Security Rule

Permissions must be enforced before retrieval, not after answer generation.

Bad pattern:

```text
Retrieve everything -> ask LLM not to reveal restricted content
```

Correct pattern:

```text
Identify user -> ask SpiceDB for allowed documents -> restrict Neo4j retrieval
to graph elements derived from allowed documents -> send only allowed context
to the LLM
```

The LLM should never receive restricted context.

## 7. Provenance Rules

Every piece of knowledge written to Neo4j must know where it came from.

Required provenance fields on **every** node, relationship, and chunk
(ADR-011; this is what the retrieval guard filters on):

- `source_document_id`
- `connection_id`
- `drive_file_id`
- `source_permissions_version`

Each graph element derives from exactly **one** source document: entities are
scoped per document and never merged across documents (ADR-010), so a plural
`source_documents` list cannot occur and is not stored. Chunk-level (fact)
attribution is structural rather than a field: every entity anchors to the
chunk it was found in via a `mentions` edge, and every extracted relationship
edge carries the `chunk_index` of its source chunk.

Core document metadata (on the Document node):

- `source_document_id`
- `drive_file_id`
- `drive_url`
- `title`
- `mime_type`
- `content_hash` — identifies the extracted content version; extraction jobs
  refuse to overwrite a newer version
- `source_permissions_version`

Deferred audit metadata (optional, not required for permission safety, and
never a substitute for the required fields above): `extraction_run_id`,
`confidence`, per-element `created_at`/`updated_at`. Re-extraction fully
replaces a document's derived graph data, so element age and version follow
from the Document node's `content_hash`.

No orphan facts are allowed. If a node, relationship, chunk, or extracted fact
cannot point back to its source document, it must not be used for retrieval.

Strict default (enforced by construction under per-document scoping):

```text
Every graph element belongs to exactly one source document; expose it only if
that document is visible to the requesting user.
```

Preferred long-term behavior:

```text
If fact-level provenance exists, expose only facts contributed by visible source
documents.
```

## 8. Ontology

The ontology defines what kinds of things and relationships matter for a
customer. It should be configurable per customer, but the underlying system
should stay the same.

Initial entity types:

- `Document`
- `Person`
- `Project`
- `Customer`
- `Organization`
- `Procedure`
- `Machine`
- `Part`
- `Vendor`
- `Policy`
- `Task`
- `Topic`

Initial relationship types:

- `mentions`
- `authored`
- `responsible_for`
- `references`
- `supersedes`
- `belongs_to`
- `depends_on`
- `works_on`
- `owns`
- `related_to`

Agents should not casually add new entity or relationship types. If a feature
needs a new type, update the ontology documentation and tests.

## 9. Google Drive Ingestion Requirements

The first real pilot assumes a per-client Google service account, with
domain-wide delegation only as a fallback when Workspace policy blocks the
share-to-connect path.

The Drive connector should:

- Let an admin connect Google Drive, list eligible root folders/shared drives,
  choose the ingestion root dynamically, and persist that scope in
  `DriveConnection`.
- Treat manually supplied root IDs as a bootstrap/developer fallback, not the
  client-facing setup path.
- List supported files.
- Export Google Docs to text or Markdown.
- Export Google Sheets to CSV/text summaries.
- Read PDFs and uploaded text/doc files where practical.
- Capture file metadata.
- Capture sharing metadata.
- Track folder paths and inherited permissions.
- Store content hashes to avoid unnecessary re-indexing.
- Feed content into a common ingestion interface.

Supported v1 file types:

- Google Docs
- Google Sheets
- PDFs
- Markdown/text files
- Word documents if easy
- CSV files if easy

Google Drive ingestion should produce the same internal document record shape
regardless of file type.

## 10. Permission Sync Requirements

Use SpiceDB. Do not invent a custom permission system.

The permission model must represent:

- Users
- Google Groups
- Folders
- Files/documents
- Folder inheritance
- Group membership
- Direct sharing
- Link/domain sharing if supported in the pilot

The sync process should:

- Read Drive sharing metadata.
- Write relationships into SpiceDB.
- Refresh document permissions separately from content extraction.
- Handle permission-only changes without re-embedding documents.
- Prefer live or frequently refreshed group membership resolution.

The query process should:

- Ask SpiceDB which documents a user can see.
- Restrict retrieval to Neo4j graph elements whose provenance is allowed.

## 11. Retrieval Requirements

The retrieval layer is the translator between Open WebUI, permissions, Neo4j,
and OpenRouter.

For each question:

1. Receive the user question and authenticated user identity.
2. Resolve the user's Google identity.
3. Ask SpiceDB for allowed source documents.
4. Run hybrid retrieval in Neo4j:
   - vector search for fuzzy semantic matches
   - graph traversal for related entities and relationships
5. Exclude any graph element that does not pass provenance visibility.
6. Assemble concise context.
7. Include source citations.
8. Call OpenRouter.
9. Return answer, source citations, and refusal when needed.

Answer behavior:

- If context is insufficient, say what is missing.
- If the user lacks access, refuse safely.
- If sources conflict, mention uncertainty and cite both visible sources.
- Never reveal that a restricted document contains the answer.

## 12. Open WebUI Integration

Open WebUI is the intended front end.

The backend may integrate through either:

- An Open WebUI Pipeline/Function, or
- An OpenAI-compatible API endpoint used by Open WebUI.

The prototype currently favors an OpenAI-compatible endpoint because it is easy
to connect and test.

Important:

- Open WebUI login should use Google OAuth/OIDC.
- The logged-in identity must match the Google Drive identity used for
  permission checks.
- Local password login should be disabled or hidden for production pilots.

## 13. Change-Driven Re-Indexing

Do not rely on nightly full rescans as the main update strategy.

Use Google Drive's change feed.

Required behavior:

- Content change -> re-extract text, graph facts, chunks, and embeddings.
- Permission-only change -> update SpiceDB only.
- Folder move/share change -> update effective access.
- Google Group membership change -> update or resolve permissions without
  re-indexing content.

Avoid expensive re-embedding for permission-only updates.

## 14. Evaluation And Leak Testing

The prototype is not successful unless leak tests pass.

Maintain an evaluation set with roughly 20 questions:

- Normal answer questions
- Source citation questions
- "Not enough context" questions
- Restricted document refusal questions
- Graph-path leak tests

Graph-path leak test example:

```text
The answer exists only as a node or relationship extracted from a restricted
document. The user can see a related public document but not the restricted
source. The system must refuse or say it lacks access/context.
```

The evaluation runner should report:

- Question
- Test user
- Expected behavior
- Actual answer
- Sources returned
- Pass/fail
- Leak risk notes

## 15. Public Backend Interfaces

These are the target public interfaces:

### `GET /health`

Reports health for:

- Backend
- Neo4j
- SpiceDB
- Postgres
- Drive connector
- OpenRouter configuration

### `GET /ingest/drive/roots`

Lists eligible root folders/shared drives visible to the configured Google
Drive connection.

### `POST /ingest/drive/connection/root`

Persists the admin-selected Drive ingestion root after matching it against
the visible candidate list.

### `POST /ingest/drive/connection/delegated-subject`

Sets or clears the optional delegated Workspace user used for domain-wide
delegation.

Expected behavior:

- Admin-only.
- Accepts only `delegated_subject_email`; an empty string clears the override.
- Validates non-empty values as email addresses.
- Does not accept Drive root or scope changes.
- When the value changes, marks retrievable documents for that connection
  non-retrievable until permissions are refreshed under the new identity.

### `GET /ingest/drive/permissions/check`

Samples files under the selected Drive root and reports whether the configured
connection can read Drive permission metadata for them.

Expected behavior:

- Admin-only.
- Reads the selected root from server-side `DriveConnection` state.
- Returns counts of sampled files with readable/unreadable ACL metadata and
  folder-listing failures.
- Does not return raw permission payloads or document content.
- Used to validate service-account vs. domain-wide delegation readiness before
  relying on content ingestion.

### `POST /ingest/drive/sync`

Starts or resumes Google Drive ingestion.

Expected behavior:

- Scan the folder/shared-drive scope currently stored on the enabled
  `DriveConnection`.
- Pull changed content.
- Update Neo4j.
- Return counts for scanned, ingested, skipped, failed.

### `POST /permissions/sync`

Refreshes Drive permissions into SpiceDB.

Expected behavior:

- Pull sharing metadata.
- Update SpiceDB relationships.
- Return counts for users, groups, folders, files, relationships.

### `POST /query`

Receives:

```json
{
  "user_email": "employee@example.com",
  "question": "What projects is Sarah responsible for?"
}
```

Returns:

```json
{
  "answer": "Sarah is responsible for...",
  "citations": [
    {
      "title": "Project Plan",
      "drive_file_id": "abc123",
      "drive_url": "https://drive.google.com/...",
      "chunk_id": "abc123:4"
    }
  ],
  "refused": false,
  "reason": null
}
```

If the answer is restricted or unavailable:

```json
{
  "answer": "I do not have enough accessible context to answer that.",
  "citations": [],
  "refused": true,
  "reason": "insufficient_accessible_context"
}
```

### `POST /eval/run`

Runs the fixed pilot evaluation set and leak tests.

## 16. Implementation Phases

These are the repository phases tracked in `ai-context/phases/`.

### Phase 0: Repository And Infrastructure

Status: complete.

Purpose: create the clean repository baseline, Docker Compose infrastructure,
Traefik routing structure, monitoring services, and AI-agent documentation.

### Phase 1: Django Backend Foundation

Status: complete.

Purpose: prove the service foundation works before building high-risk Drive,
graph, and permission features. This includes Django, DRF, PostgreSQL, Redis,
Celery, Neo4j connectivity, health checks, tests, linting, and Makefile
commands.

### Phase 2: Google Drive Ingestion

Status: code complete; live client validation still depends on a Drive setup
where file permission metadata is readable.

Purpose: ingest supported Google Drive files and metadata while preserving
source identity and sync state. This phase must capture Drive file metadata,
owner/creator metadata, folder ancestry, sharing metadata, source permissions
version, modified time, and content hash.

Current foundation: service-account Drive access, admin root selection,
server-side scoped sync, content export/storage, PostgreSQL metadata, source
permission version hashing, audit records, and extraction queueing. Folder-only
sharing can list/read files but generally cannot read per-file permission
metadata; domain-wide delegation is expected for safe live client ingestion.

### Phase 3: Neo4j Graph And Provenance

Status: in progress on `phase-3/graph-foundation`.

Purpose: build the document, chunk, entity, relationship, and vector
representation in Neo4j. Evaluate `neo4j-graphrag`, Graphify, and Graphiti
behind an adapter, then choose based on provenance quality and maturity.

Current foundation: `neo4j-graphrag` is selected behind the adapter, graph
setup applies constraints plus the Chunk vector index, and live smoke testing
has written chunks, entities, and relationships with complete source
provenance. Remaining work is the retrieval seam where Phase 5 queries compose
the provenance guard with SpiceDB allowed-document IDs.

Minimum extraction bar: fact-level source attribution must identify which source
document and chunk produced the fact. Document-level-only provenance is not
sufficient for permission-safe retrieval unless the retrieval policy uses the
strict rule requiring all source documents for a graph element to be visible.

### Phase 4: SpiceDB Permissions

Purpose: model Google Drive visibility in SpiceDB and expose allowed-document
lookup for retrieval. Do not replace this with ad hoc PostgreSQL permission
checks.

If SpiceDB is unavailable or a document's SpiceDB relationships are not written
and verified, retrieval must fail closed and return no context for that
document.

### Phase 5: Permission-Safe Retrieval

Purpose: answer questions using only Neo4j graph/vector context derived from
documents the user may see. Restricted facts must not leak through graph paths,
embeddings, citations, or prompt context.

### Phase 6: Open WebUI Integration

Purpose: expose the backend through Open WebUI and make sure the backend
receives a trusted Google/OIDC user identity.

### Phase 7: Change Feed And Evaluation

Purpose: keep graph data and permissions current through the Drive change feed,
and prove safety with repeatable answer-quality and leak tests.

### Phase 8: Deployment Handoff

Purpose: make the POC understandable, maintainable, recoverable, and reusable
for future client implementations.

## 17. Pricing And Scope Notes

A real permission-safe implementation is larger than a simple chatbot and
should be priced according to security, ingestion, retrieval, evaluation, and
handoff risk.

Approximate client pricing guidance:

- Technical proof of concept: `$900-$3,000`
- Discounted founding pilot: `$15,000-$20,000`
- Proper first implementation: `$25,000-$45,000`
- Monthly maintenance: `$500-$1,500/month`

If working with a `$900` budget, agents must keep scope narrow and call it a
technical proof of concept, not a production-safe implementation.

## 18. Non-Goals For The First POC

Do not build these unless explicitly requested:

- Multi-tenant SaaS billing
- Custom polished frontend
- Mobile app
- Local LLM hosting
- Complex admin dashboard
- Dozens of connectors
- Human graph editing UI
- Enterprise monitoring suite
- Fine-grained role management beyond Drive-backed visibility

## 19. Feature-Building Rules For AI Agents

When asked to build a feature:

1. Preserve permission safety as the top priority.
2. Do not send unrestricted graph context to an LLM.
3. Preserve provenance on every graph write.
4. Prefer extending existing backend services over adding unrelated frameworks.
5. Keep the system single-customer and isolated by default.
6. Add tests for permission behavior when touching retrieval or ingestion.
7. Add or update docs when changing public endpoints or data contracts.
8. Avoid building UI polish before the core Drive/graph/permission loop works.
9. Run deterministic local validation that matches the change. Extra audit
   commands are human-triggered only.

## 20. Definition Of Done

A feature is done only when:

- It works for the intended path.
- It fails safely.
- It preserves source provenance.
- It respects visible-document filtering.
- It has at least one meaningful test or documented manual verification path.
- It does not broaden the scope into full SaaS without approval.

For the full prototype, done means:

- Drive content can be ingested.
- Neo4j contains provenance-rich graph data.
- SpiceDB controls visible source documents.
- Retrieval only uses allowed graph context.
- Open WebUI can ask and answer through the backend.
- Leak tests pass.
- Basic deployment and maintenance docs exist.
