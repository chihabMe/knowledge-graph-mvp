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

Phase 2 code is complete for controlled Google Drive content ingestion:
per-client service-account identity, keyless Application Default Credentials
(ADC), legacy mounted-key compatibility, admin root selection, server-side
scoped sync, content export/storage, metadata capture, audit records, and
post-commit extraction queueing are implemented. Live Drive validation
on 2026-07-18 confirmed that keyless impersonated ADC lets the shared-folder
service account discover the pilot root and export document content. The same
Viewer identity received `403 insufficientFilePermissions` from
`permissions.list()`, confirming that folder sharing does not reliably expose
effective user ACLs. ADR-015
therefore keeps the service account as the content reader and moves the POC's
employee-visibility authority to admin-approved per-user Drive OAuth.

Phase 3 is code complete and merged into `main`: the graph app, ontology,
Neo4j setup, extraction adapter, document/chunk/entity/relationship writers,
source provenance guard, Chunk vector-index setup, and extraction-recovery
hardening are implemented and covered by tests. Phase 4's delegated ACL/group
synchronization path is code complete, but ADR-015 supersedes it as the default
POC permission authority. Phase 6 now has live-validated per-user OAuth
visibility snapshots, direct SpiceDB document grants, per-user freshness
evidence, and mode-aware retrieval;
domain-wide delegation remains an optional future mode rather than a POC
blocker. The next product-risk
dependency, Phase 5 retrieval, is code complete: the authenticated
`/api/query/` contract, SpiceDB pre-filter, fresh PostgreSQL evidence gate,
provenance-constrained Neo4j keyword/vector/one-hop fact retrieval, bounded
context, server-owned citations, OpenRouter answer synthesis, and safe refusal
path are implemented. Phase 6's Open WebUI adapter and Compose integration are
code complete and locally validated. Real Google session bootstrap, separate
admin-approved Drive consent, and indexed-ID visibility synchronization have
now passed for two Workspace users with intentionally different document
visibility. Real Open WebUI Google login and fail-closed evidence-expiry
behavior have also passed. The complete two-user chat, access removal and
restoration, OAuth disconnect and reconnect, provider-route, evidence-expiry,
and SpiceDB outage matrix has passed. A post-consent callback also queued and
completed a user-specific refresh without waiting for the periodic scheduler.
Phase 6 intentionally remains open pending formal closeout review and final
report acceptance.

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

Employee Google Drive OAuth
  -> check only already-indexed Drive file IDs as that employee
  -> fresh per-user visibility evidence
  -> direct SpiceDB user/document relationships

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

The first real pilot uses a per-client Google service account only for content
ingestion from an explicitly shared folder or Shared Drive. The preferred
credential source is keyless Application Default Credentials: local development
uses short-lived service-account impersonation, while a Google Compute Engine
deployment uses the attached service account through the metadata server. A
long-lived service-account JSON key is legacy compatibility, not the default.
Employee
authorization is separate: each pilot user grants admin-approved per-user
OAuth access so Google can answer whether that user can see each already
indexed file. Domain-wide delegation is not required for the POC path.

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
- Capture the selected-root membership and folder path needed for scope and
  provenance; full ACL metadata is not required in per-user OAuth mode.
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

Phase 4's existing delegated mode uses checked-in Authzed schema definitions
prefixed with `kgm/` and models Drive roles, folder inheritance, and recursive
Google Groups. ADR-015 retains that implementation only as an optional legacy
mode. The POC default adds a distinct direct relation from an opaque user
subject to an indexed document after Google confirms visibility using that
user's OAuth credential. The relation must not pretend to be a Drive ACL role.
Raw Drive IDs and email addresses never appear in SpiceDB object IDs, logs, or
API responses.

The active POC permission model must represent:

- Users.
- Indexed files/documents.
- The user's OAuth authorization generation.
- Per-user document visibility checks and their freshness.
- Direct verified user-to-document relationships in SpiceDB.

Google itself resolves direct, inherited, Shared Drive, Google Group, and
nested-group access when the indexed file is requested with that employee's
credential. The POC does not copy Directory group membership or full Drive ACL
payloads in the per-user mode.

The per-user visibility process should:

- Establish the Django browser session through a dedicated Google OIDC
  bootstrap that reuses the identity-only Open WebUI login client. The
  bootstrap requests only `openid` and email, verifies state, nonce, PKCE,
  issuer, audience, email verification, and the Workspace hosted domain, and
  stores no Google token. It then launches the separate Drive consent flow.
- Use a separate Django OAuth web flow with `openid`, `email`, and
  `drive.metadata.readonly`; Open WebUI login tokens are not reused as Drive
  authorization credentials.
- Require Workspace admin app approval and one consent per pilot user. Admin
  approval permits the app; it does not grant file access without the user's
  authorization.
- Encrypt refresh tokens at rest with a deployment secret distinct from Django,
  Open WebUI, service-bearer, and identity-JWT secrets. Tokens never enter logs,
  API responses, Celery arguments, or SpiceDB.
- Check only active documents already ingested from the selected root, using
  Drive `files.get` with Shared Drive support; never enumerate or ingest the
  employee's unrestricted Drive corpus.
- Record explicit fresh per-user visibility evidence for positive grants and
  write a distinct direct relationship to SpiceDB.
- Reconcile and verify one user's relationships independently. A failed or
  unknown check denies that document for that user and can never become a
  grant.
- Expire per-user visibility evidence at query time. A stale SpiceDB tuple is
  insufficient without a matching fresh PostgreSQL evidence row.
- Invalidate affected user visibility evidence when the selected root, OAuth
  account, authorization generation, or permission mode changes.
- Refresh visibility separately from content extraction so access changes never
  trigger re-embedding.

The query process should:

- Ask SpiceDB which documents the verified Open WebUI user can see.
- Restrict retrieval to Neo4j graph elements whose provenance is allowed.
- Use fully consistent `LookupResources` calls and then intersect returned
  opaque resources with active documents plus fresh, matching per-user
  visibility evidence. PostgreSQL stores synchronization evidence only and
  never grants access by itself.
- Return no context when the user has not connected Drive, the OAuth identity
  does not exactly match the signed Open WebUI email, the token is revoked, a
  visibility check is missing or stale, or SpiceDB verification fails.

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

Neo4j 5's vector-index query procedure does not accept the per-request Drive
document allowlist before candidate selection. The permission-safe path must
therefore `MATCH` allowed, provenance-complete chunks first and then compute
`vector.similarity.*` inside that bounded set. Calling the global vector index
and filtering its candidates afterward is forbidden. The provisioned Chunk
vector index remains available for a future pre-filter-capable strategy but is
not an authorization boundary.

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

The single-tenant Open WebUI deployment exposes exactly one logical model,
`client-knowledge-graph`, and automatically makes that model discoverable to
authenticated users from the configured Workspace domain. Open WebUI's model
ACL bypass is acceptable only with this one-model allowlist; it is not document
authorization. Django still verifies the short-lived signed identity and
SpiceDB plus fresh per-user evidence remain authoritative for every source.
Adding another upstream model requires removing the bypass or explicitly
reviewing its exposure.

The compatible chat endpoint supports bounded non-streaming requests and the
buffered Server-Sent Events required by pinned Open WebUI 0.10.2. Open WebUI's
bounded tool inventory is accepted only as ignored compatibility metadata; the
adapter never executes those tools. The complete permission-safe answer and
server-owned citations are decided before any streaming event is emitted.

Important:

- Open WebUI login should use Google OAuth/OIDC.
- OAuth signup must be restricted to the same configured Workspace domain.
- The logged-in identity must match the Google Drive identity used for
  permission checks.
- Open WebUI Google login and Django Drive consent are separate trust
  boundaries. The Django callback verifies the Google identity, and every
  query requires its normalized email to equal the signed Open WebUI email.
- Because Open WebUI does not create a Django session, the browser first uses a
  minimal Google OIDC session-bootstrap endpoint backed by the existing
  identity-only login client. That flow stores no Google token and redirects
  directly into the separate Drive authorization endpoint after a successful,
  domain-restricted login.
- Local password login should be disabled or hidden for production pilots.

## 13. Change-Driven Re-Indexing

Do not rely on nightly full rescans as the main update strategy.

Use Google Drive's change feed.

Required behavior:

- Content change -> re-extract text, graph facts, chunks, and embeddings.
- Permission-only change -> refresh affected users' visibility evidence and
  SpiceDB relationships only.
- Folder move/share change -> update effective access.
- Google Group membership change -> Google resolves the user's effective file
  access on the next visibility refresh; do not re-index content.

Avoid expensive re-embedding for permission-only updates.

Production permission-freshness target:

- Keep the current 15-minute refresh and 30-minute evidence lifetime for the
  POC until delayed/failed-run monitoring is operational.
- For the bounded single-client production pilot, refresh connected users at
  least every 5 minutes and expire positive visibility evidence after 10
  minutes. This creates a normal 0-5 minute propagation window and a hard
  fail-closed bound after two missed refresh opportunities.
- Alert before the 10-minute evidence deadline, not after access has already
  failed closed. Track scheduler heartbeat, last successful run age, run
  duration, denied/unknown/error counts, and refresh backlog.
- Use Drive change-feed and push-notification signals to trigger faster
  affected-document/user refreshes where possible, but retain the periodic
  sweep as the reconciliation authority. Notifications are signals rather than
  authorization facts, channels expire, and inherited folder changes may need
  descendant reconciliation.
- Revalidate the interval against real user/document counts and Google quota
  consumption before increasing the configured pilot caps.

Chat-history retention is a separate production policy from retrieval
authorization. Revocation must block future retrieval but cannot retract an
answer already delivered into a chat. Before production handoff, agree a
client-approved deletion/retention period, document user/admin deletion and
account-removal behavior, and avoid promising per-document historical-answer
purging unless an answer-to-source deletion index is implemented. The pilot
recommendation is a configurable 30-day default, subject to the client's legal,
security, and records requirements.

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

### `GET /api/health/`

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

### `POST /ingest/drive/connection/delegated-subject` (legacy optional mode)

Sets or clears the optional delegated Workspace user used for domain-wide
delegation.

Expected behavior:

- Admin-only.
- Accepts only `delegated_subject_email`; an empty string clears the override.
- Validates non-empty values as email addresses.
- Does not accept Drive root or scope changes.
- When the value changes, marks retrievable documents for that connection
  non-retrievable until permissions are refreshed under the new identity.

### `GET /ingest/drive/permissions/check` (legacy optional mode)

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

### `GET /api/session/google/start` and `GET /api/session/google/callback`

Creates the authenticated Django browser session required by the Drive OAuth
endpoints. The flow reuses the Open WebUI identity-only Google client, requests
only `openid` and email, and verifies one-time session state, nonce, PKCE,
issuer, audience, email verification, and the configured Workspace domain.
It stores no Google token and redirects immediately to the separate Drive
authorization flow.

### `GET /api/drive/oauth/start` and `GET /api/drive/oauth/callback`

Starts and completes the separate per-user Drive authorization-code flow. The
callback verifies state, Google identity, granted scopes, email verification,
and the configured Workspace domain before storing only an encrypted refresh
credential. A successful callback immediately queues the existing bounded
visibility refresh for only the verified Django identity. If dispatch is
temporarily unavailable, the connection remains valid and the durable queued
run or periodic scheduler retries without requiring another consent flow. The
result page distinguishes active synchronization from scheduled fallback. It
never returns tokens to Open WebUI or the browser.

### `GET /api/drive/oauth/status` and `POST /api/drive/oauth/disconnect`

Returns controlled connection status without credential data and lets the user
revoke local access. Disconnect immediately invalidates that user's visibility
evidence and removes their managed SpiceDB relationships; Google token
revocation is attempted without making local denial depend on its success.

### `POST /api/drive/visibility/sync`

Queues a bounded refresh for the authenticated user's already-indexed
documents. The request cannot supply file IDs, a Drive root, another identity,
or a wider scope. A scheduled task also refreshes connected users before their
evidence expires.

### `POST /ingest/drive/sync`

Starts or resumes Google Drive ingestion.

Expected behavior:

- Scan the folder/shared-drive scope currently stored on the enabled
  `DriveConnection`.
- Pull changed content.
- Update Neo4j.
- Return counts for scanned, ingested, skipped, failed.

### `POST /permissions/sync` (legacy optional mode)

Creates an admin-only, rate-limited permission-sync audit run and queues it.
The request cannot supply or widen Drive scope.

Expected behavior:

- Return HTTP 202 with only `run_id`, `status`, and `connection_id`.
- A companion admin-only `GET /permissions/sync/{run_id}/` returns controlled
  status/count fields and never names, emails, Drive IDs, ACLs, or exceptions.
- Pull Drive ACL/folder metadata and referenced group membership, update and
  verify SpiceDB relationships, and keep unverified documents ineligible.

### `POST /api/query/`

Receives:

```json
{
  "question": "What projects is Sarah responsible for?"
}
```

The user identity comes only from the authenticated Django session. Request
payload identity fields such as `user_email` are outside the contract and are
rejected. The server-side user email is normalized and passed to the SpiceDB
allowed-document lookup before Neo4j is queried.

Returns:

```json
{
  "answer": "Sarah is responsible for...",
  "citations": [
    {
      "title": "Project Plan",
      "drive_file_id": "abc123",
      "drive_url": "https://drive.google.com/...",
      "chunk_id": "42:4"
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

The Phase 5 backend returns a bounded answer from permission-filtered hybrid
keyword/vector/one-hop graph evidence. Embeddings and answer synthesis use
separate opt-in OpenRouter adapters. The model receives only JSONL context that
survived SpiceDB, provenance, and fresh-evidence gates; it returns only answer
text plus a support decision, while citation URLs remain server-owned.

### `GET /v1/models` (Phase 6 target)

Returns the single logical knowledge-graph model exposed to Open WebUI. The
request must carry the configured Open WebUI-to-Django service bearer key. User
identity is not required for connection discovery, and no graph, document, or
permission data is returned.

### `POST /v1/chat/completions` (Phase 6 target)

Accepts an OpenAI-compatible chat request from the server-side Open WebUI
connection. The endpoint must authenticate the service bearer key, verify the
short-lived signed Open WebUI identity JWT, extract a bounded user question,
and call the existing `answer_query()` service. Request-supplied identity and
plain forwarded email headers are never authorization evidence.

Invalid compatible requests return a bounded OpenAI-style `error` envelope
rather than raw DRF serializer details. A conversation beyond the configured
message bound receives the controlled `conversation_too_long` code and an
instruction to start a new chat. Other malformed or oversized payloads receive
only `invalid_request`. Neither path reflects request content or calls the
permission, retrieval, embedding, or answer-provider services.

The first slice may be non-streaming. It must translate the existing answer,
controlled refusal, and server-owned permitted citations without sending chat
history or any unrestricted context directly to OpenRouter.

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

Status: content-ingestion code complete; per-user visibility completion is now
tracked in Phase 6 and does not require service-account ACL visibility.

Purpose: ingest supported Google Drive files and metadata while preserving
source identity and sync state. This phase captures the selected-root scope,
Drive identity, provenance metadata, modified time, and content hash. Full ACL
capture is required only by the optional delegated permission mode.

Current foundation: service-account Drive access, admin root selection,
server-side scoped sync, content export/storage, PostgreSQL metadata, source
versioning, audit records, and extraction queueing. Folder-only sharing is
sufficient for the content path; per-user OAuth supplies employee visibility.

### Phase 3: Neo4j Graph And Provenance

Status: code complete and merged into `main` (2026-07-11).

Purpose: build the document, chunk, entity, relationship, and vector
representation in Neo4j. Evaluate `neo4j-graphrag`, Graphify, and Graphiti
behind an adapter, then choose based on provenance quality and maturity.

Current foundation: `neo4j-graphrag` is selected behind the adapter, graph
setup applies constraints plus the Chunk vector index, and live smoke testing
has written chunks, entities, and relationships with complete source
provenance. The remaining retrieval seam belongs to Phase 5, where queries
compose the provenance guard with SpiceDB allowed-document IDs.

Minimum extraction bar: fact-level source attribution must identify which source
document and chunk produced the fact. Document-level-only provenance is not
sufficient for permission-safe retrieval unless the retrieval policy uses the
strict rule requiring all source documents for a graph element to be visible.

### Phase 4: SpiceDB Permissions

Status: delegated ACL/group implementation code complete (2026-07-11) but
superseded as the default POC authority by ADR-015. The direct per-user
visibility path is planned as Phase 6 completion work.

Purpose: model Google Drive visibility in SpiceDB and expose allowed-document
lookup for retrieval. Do not replace this with ad hoc PostgreSQL permission
checks.

If SpiceDB is unavailable or a document's SpiceDB relationships are not written
and verified, retrieval must fail closed and return no context for that
document.

Current legacy foundation: checked-in `kgm/` schema and idempotent lifecycle commands,
opaque connection-scoped identifiers, permission-only Drive folder/document
snapshots, read-only nested group resolution, exact TOUCH/DELETE reconciliation,
at-least-as-fresh verification and ACL-version CAS, durable admin sync runs,
SpiceDB health, and the internal fully consistent
`allowed_source_document_ids()` Phase 5 handoff. Public/domain visibility and
incomplete permissions remain excluded. Query-time evidence expiry provides a
hard fail-closed bound when scheduled permission synchronization repeatedly
fails; successful syncs refresh that evidence. The new POC mode will keep the
fully consistent lookup and evidence-expiry pattern while replacing ACL/group
copying with direct user/document relationships verified by user OAuth.

### Phase 5: Permission-Safe Retrieval

Status: code complete and live validated (2026-07-13).

Purpose: answer questions using only Neo4j graph/vector context derived from
documents the user may see. Restricted facts must not leak through graph paths,
embeddings, citations, or prompt context.

Current foundation: `/api/query/` accepts only a question and derives identity
from the authenticated Django session; `allowed_source_document_ids()` runs
before any embedding, Neo4j, or answer-provider call; fresh permission evidence
is rechecked before response assembly. Keyword chunks, vector-similar chunks,
and bounded one-hop entity facts compose the allowed-document filter and
provenance guard on every returned node and relationship. Neo4j vector
similarity is computed only after the permission/provenance `MATCH`, never by
globally retrieving vector-index candidates and filtering afterward. Bounded
JSONL context retains the exact evidence eligible for server-owned Drive
citations. OpenRouter receives only that context and must return structured
answer/support fields; every empty or failed authorization, embedding,
retrieval, context, or model path shares one controlled refusal.

Live development acceptance re-embedded both chunks of the OAuth Drive PDF at
1,536 dimensions with zero missing provenance. An allowed relevant query used
both keyword and vector evidence and returned an OpenRouter answer with only
the permitted PDF citations. Unrelated, restricted-user, expired-evidence,
unauthenticated, and spoofed-identity requests were refused or rejected without
restricted context or citations.

### Phase 6: Open WebUI Integration

Status: implementation and core live acceptance validated; formal closeout is
intentionally pending (2026-07-18). Completion follows the admin-approved
per-user OAuth plan in
`docs/phase-6-pre-authorized-oauth-completion-plan.md`.

Purpose: expose the backend through Open WebUI and make sure the backend
receives a trusted Google/OIDC user identity.

Accepted pattern: a thin OpenAI-compatible adapter in Django, protected by a
service bearer key and a short-lived signed Open WebUI identity JWT. The
existing `/api/query/` and `answer_query()` permission boundary remain the
single retrieval implementation.

Implemented evidence includes fail-closed startup settings, constant-time
service authentication, strict HS256 identity verification, one-model
discovery, bounded chat request parsing, safe citation rendering, buffered
streaming, Compose hardening, adapter leak tests, and a successful local
Open WebUI chat using synthetic Atlas data. This does not close the phase: the
local acceptance used password bootstrap and extractive answering rather than
real Google login and the production OpenRouter route. ADR-015 WP1-WP3 are
complete locally: fail-closed per-user settings, a dedicated versioned
token-encryption boundary, additive authorization/evidence models and
migration, read-only secret mounts, and the session-bound Django Drive OAuth
connect/status/reconnect/disconnect flow are validated. The OAuth flow binds
the exact authenticated session email to verified Google claims, rejects
broader Drive scopes, persists only encrypted refresh credentials, rotates
evidence generations, and disconnects locally before best-effort revocation.
The additive `oauth_viewer` SpiceDB relation now has exact one-user scoped
read/reconcile/delete helpers, causal verification, schema assertions, and
delegated-mode isolation. It is not populated by onboarding alone. The
user-token Drive adapter is also implemented and fake-validated: it accepts
only an authorization primary key, selects active indexed IDs from PostgreSQL,
and issues only bounded `files.get` metadata checks with Shared Drive support;
it has no list/export/download or request-supplied-ID path. Keyless ADC live
validation selected the exact renamed `Knowledge Graph Pilot` root, and the
controlled authority switch to `per_user_oauth` re-ingested and
graph-extracted all three pilot documents. In this mode successful graph
extraction marks the coarse document-content gate ready without treating that
state as an authorization grant; retrieval still requires the independently
verified direct SpiceDB tuple plus fresh matching per-user evidence. Durable visibility runs
now pre-invalidate only one
authorization, reconcile and causally verify that user's direct tuples, commit
generation-bound positive evidence only after verification, retry through
bounded locks, schedule refreshes, and sweep stale work fail-closed. Retrieval
uses a fully consistent direct `oauth_viewer` read in per-user mode, intersects
it with fresh matching `UserDocumentVisibility`, and repeats the mode-aware
PostgreSQL deny gate after Neo4j before any context reaches OpenRouter. It never
unions or falls back to delegated grants. ADR-017's identity-only Google OIDC
session bootstrap is implemented with state, nonce, PKCE, exact claim/domain
verification, unusable-password users, and no Google token persistence. The
separate Drive authorization flow also uses session-bound state and PKCE and
exchanges only the authorization code after callback-state validation. On
2026-07-18 both pilot users completed the identity bootstrap and Drive consent.
Each durable visibility run considered the same three indexed documents and
finished with exactly two verified-visible and one denied result. The final
fully consistent SpiceDB plus fresh-evidence lookup returned only the user's
own private document and the document shared with both users, while denying the
other user's private document. Real Open WebUI Google login then succeeded for
the first pilot user. Its first chat requests correctly refused because the
user's 1,800-second visibility-evidence lifetime had expired; after a fresh
three-document synchronization, permission-filtered retrieval again returned
only that user's private document and the shared document. The exact signed
Open WebUI identity plus service-bearer adapter request also passed through the
production OpenRouter route with `openai/gpt-4.1-mini`: it returned both
permitted server-owned citations and no other user's source. The configured
DeepSeek route then passed through the visible UI for both users: each received
only their own private verification code and permitted source titles. Removing
User 1's private-document share produced one visible, two denied, and zero
unknown results; new chats refused the removed fact until the share was
restored and a later run rebuilt the exact two-document allowlist. Stopping
SpiceDB caused controlled refusals with no context, and restart did not reuse
the interrupted positive evidence; later successful runs restored each exact
allowlist. Disconnecting User 2 immediately removed context and citations;
reconnection remained denied until a fresh two-visible/one-denied run completed,
then returned only User 2's permitted code and sources. Evidence-expiry and
provider-route cases also passed. The OAuth callback now queues an immediate
user-specific visibility refresh with periodic scheduling as fallback. A live
User 2 disconnect/reconnect then refused safely while evidence was absent and,
after the callback-triggered run completed, returned only the permitted private
code and the shared source without waiting for the periodic scheduler. A
shared-document question returned the correct shared fact, although the
server-owned citations also included another permitted User 2 source; this is
a citation-relevance issue rather than an authorization leak. The adapter also
normalizes over-32-message and other serializer failures into controlled
OpenAI-compatible errors without reflecting request content or reaching the
query service. Phase 6 remains open by operator decision pending formal report
review and closeout.

### Phase 7: Change Feed And Evaluation

Purpose: keep graph data and permissions current through the Drive change feed,
prove safety with repeatable answer-quality and leak tests, implement the
5-minute refresh/10-minute evidence-expiry production target, and monitor
failed or delayed synchronization before enabling those tighter limits.

### Phase 8: Deployment Handoff

Purpose: make the POC understandable, maintainable, recoverable, and reusable
for future client implementations. Handoff includes the permission-freshness
SLA, synchronization monitoring/runbook, and a client-approved chat-history
deletion and retention policy.

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
