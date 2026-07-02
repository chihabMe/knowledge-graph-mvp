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

## 3. Current Prototype Boundary

The current repo started as a local-file MVP. It already has a FastAPI backend,
Neo4j integration, local file ingestion, OpenRouter calls, and an
OpenAI-compatible chat endpoint.

The next real project direction is to evolve it toward the client scope:

- Google Drive ingestion
- Real provenance metadata
- SpiceDB permission sync
- Permission-safe retrieval
- Open WebUI integration
- Evaluation and leak tests

Do not treat the current local-file ingestion as the final architecture. It is
only a test harness and fallback path.

## 4. Core Stack

Use these technologies unless the user explicitly changes the direction:

| Layer | Tool | Purpose |
| --- | --- | --- |
| Chat UI | Open WebUI | User-facing chat interface |
| User login | Google OAuth/OIDC in Open WebUI | User identity must match Google Drive identity |
| Model gateway | OpenRouter | Hosted LLM access and model flexibility |
| Backend | Python + FastAPI | Ingestion, retrieval, permission filtering, health endpoints |
| Graph store | Neo4j | Graph nodes, relationships, chunks, vector indexes |
| Extraction/indexing | neo4j-graphrag first | Text extraction, chunking, embeddings, graph extraction |
| Alternative extraction | Graphify / Graphiti | Consider later if better for incremental graph updates |
| Permission engine | SpiceDB | Relationship-based authorization |
| Permission datastore | Postgres | Persistent SpiceDB datastore |
| Deployment | Docker Compose | Single-customer isolated VM deployment |
| Reverse proxy later | Caddy or Traefik | SSL and subdomains |

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

Required provenance fields:

- `source_documents`
- `source_chunk_ids`
- `extraction_run_id`
- `confidence`
- `created_at`
- `updated_at`

Core document metadata:

- `source_document_id`
- `drive_file_id`
- `drive_url`
- `title`
- `mime_type`
- `modified_time`
- `content_hash`
- `source_permissions_version`

No orphan facts are allowed. If a node, relationship, chunk, or extracted fact
cannot point back to source documents, it should not be used for retrieval.

Strict default:

```text
If fact-level provenance is not reliable, require all source documents connected
to that graph element to be visible before exposing it.
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

The first real pilot assumes service-account access with Google Workspace
domain-wide delegation.

The Drive connector should:

- Connect to one configured Drive folder or shared drive scope.
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

### `POST /ingest/drive/sync`

Starts or resumes Google Drive ingestion.

Expected behavior:

- Scan configured folder/shared drive.
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

## 16. Development Phases

### Phase 0: Low-Budget Technical Prototype

Use this if the budget is around `$900` for 4 weeks.

Goal:

```text
Prove the core idea, not a production-safe MVP.
```

Includes:

- Docker stack
- Local files or limited Drive ingestion
- Basic Neo4j graph
- Basic retrieval
- OpenRouter answer generation
- Simulated permissions
- Small demo dataset

Does not include:

- Production-grade Google Workspace delegation
- Real full Drive permission inheritance
- Robust SpiceDB integration
- Change feed re-indexing
- Guaranteed leak-proof behavior
- Full handoff package

Call this a proof of concept, not the full MVP.

### Phase 1: Risky Core Prototype

Goal:

```text
Can we read real Drive content into a graph and keep secrets safe?
```

Build:

- Customer ontology
- Google Drive connector
- Neo4j graph extraction
- Provenance metadata
- SpiceDB permission model
- Pre-retrieval permission filter
- First leak tests

### Phase 2: Answer Quality Prototype

Goal:

```text
Can users ask useful questions through Open WebUI?
```

Build:

- Open WebUI integration
- Hybrid retrieval
- Source citations
- OpenRouter answer flow
- Refusal behavior

### Phase 3: Current And Testable Prototype

Goal:

```text
Can the graph stay current and can we prove it is safe?
```

Build:

- Drive change feed
- Incremental re-indexing
- Permission-only update handling
- Evaluation runner
- Handoff docs
- Basic health checks

## 17. Pricing And Scope Notes

A real permission-safe MVP is likely a 6-8 week build and should not be priced
like a simple chatbot.

Approximate client pricing guidance:

- Technical proof of concept: `$900-$3,000`
- Discounted founding pilot: `$15,000-$20,000`
- Proper first implementation: `$25,000-$45,000`
- Monthly maintenance: `$500-$1,500/month`

If working with a `$900` budget, agents must keep scope narrow and avoid
claiming production safety.

## 18. Non-Goals For The First MVP

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
