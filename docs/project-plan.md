# POC Project Plan

## Goal

Build a repeatable client-owned AI knowledge graph proof of concept. The client should log into one chat interface, ask questions about their business knowledge, and receive answers grounded in their own documents.

The critical product requirement is permission safety: users must not receive facts derived from Google Drive files they cannot access.

## Architecture

```text
Open WebUI
  -> Django REST Framework backend
    -> SpiceDB permission checks
    -> Neo4j graph/vector retrieval
    -> OpenRouter model call

Google Drive
  -> Celery ingestion and sync workers
    -> PostgreSQL metadata and job state
    -> Neo4j documents, chunks, entities, and relationships
    -> SpiceDB document visibility relationships
```

## Tech Stack

- Host environment: Ubuntu VM
- Deployment: Docker Compose
- Interface: Open WebUI
- Backend: Django + Django REST Framework
- Background jobs: Celery
- Broker/cache: Redis
- Application metadata: PostgreSQL
- Graph database: Neo4j
- Authorization engine: SpiceDB
- Model gateway: OpenRouter
- Deployment: Docker Compose
- Reverse proxy: Traefik
- First real ingestion path: Google Drive API

## POC Milestones

### Milestone 1: Backend Foundation

- Docker Compose for infrastructure and app services.
- Django backend.
- Health endpoint.
- Celery worker.
- PostgreSQL, Redis, and Neo4j connectivity.
- Repeatable lint/test/start commands.

### Milestone 2: Google Drive Content And Visibility

- Configure a per-client service account for selected-root content ingestion.
- Add controlled admin folder/shared-drive selection before live sync.
- Add admin-approved per-user Drive OAuth for employee visibility.
- Store Drive connection records.
- Track sync runs.
- List supported Drive files.
- Store file/provenance metadata, per-user visibility evidence, and content
  hashes in PostgreSQL.

### Milestone 3: Content Extraction And Graph Build

- Export supported Drive files.
- Extract text and metadata.
- Chunk documents.
- Store documents and chunks in Neo4j.
- Extract entities and relationships.
- Build extraction behind an adapter boundary before committing to one engine.
- Preserve provenance on every derived graph element.

### Milestone 4: SpiceDB Permission Sync

- Model users and indexed documents. Keep the old group/folder ACL code dormant
  until a separate cleanup; it is not a supported POC mode.
- Verify already-indexed IDs with each connected employee's OAuth credential.
- Sync direct user/document visibility with freshness evidence.
- Build allowed-document checks.

### Milestone 5: Permission-Safe Retrieval Chat

- Accept query requests from Open WebUI.
- Resolve authenticated user identity.
- Ask SpiceDB for allowed source documents before retrieval.
- Restrict Neo4j retrieval to allowed provenance.
- Return cited answers through OpenRouter.
- Refuse restricted or insufficient-context answers safely.

### Milestone 6: Deployment Template

- Add per-client subdomain settings.
- Add backup/restore docs.
- Add maintenance checklist.
- Add migration docs for moving to another VM.

## Delivery Timeline

4–6 week target. The build milestones above map to the 7 work packages defined in the developer scope doc (`output/pdf/organizational-knowledge-graph-developer-scope-v6.pdf`, WP1–WP7). Tracking against work packages directly, not a flattened set of phases, because collapsing them hides real prerequisites (WP6 must be decided before WP1 extraction is finalized) and distinct concerns (WP5 and WP7 are different problems that happen to land in the same week).

- **Week 1 — Foundation + WP6 start (Ontology):** Finalize scope, stand up the backend/Docker foundation, confirm the Google Drive access approach, prepare data models. Begin WP6 — decide entity/relationship types and the provenance visibility rule (any-source-visible vs. all-sources-visible) before extraction is finalized. (Milestone 1, start of Milestone 2)
- **Week 2 — WP1 (Document Intake & Graph Building), Drive connector half:** Google Drive ingestion — Docs, Sheets, PDFs, selected-root/provenance metadata, and sync tracking. Full copied ACL metadata belongs only to dormant delegated code and is not POC work. (Milestone 2 content foundation)
- **Week 3 — WP1 continued + WP6 finalized:** Neo4j graph/provenance layer; evaluate the best extraction approach (neo4j-graphrag vs. Graphify/Graphiti); provenance tagging on every node/relationship. Ontology (WP6) locked before this closes. (Milestone 3)
- **Week 4 — WP4 (Identity & Permissions):** Open WebUI Google login, separate admin-approved Django Drive consent, indexed-ID visibility checks, and direct per-user SpiceDB relationships with fresh evidence. Start of the permission pre-filter that WP2 depends on. (Milestone 4, start of Milestone 5)
- **Week 5 — WP2 (Retrieval Middleware) + WP3 (Hybrid Retrieval + Embeddings):** Open WebUI/OpenRouter integration, permission pre-filter wired into retrieval, embeddings + vector index alongside graph traversal, citations, chat flow testing. (Milestone 5 complete)
- **Week 6 — WP5 (Change-Driven Re-Indexing) + WP7 (Evaluation):** Two distinct packages, not one cleanup step. WP5: Drive change-feed triggered re-indexing plus cheap per-user visibility refresh without re-embedding; Google evaluates inherited, group, nested-group, and Shared Drive access for the real user. WP7: fixed test-question set and leak tests including graph-path probes (not just document probes). Plus deployment/handoff docs. (Milestone 6)

Grouped into 3 delivery phases only for payment tracking (see `private/client-agreement.md` for amounts) — the phase boundary is a billing convenience, the work package list above is the real plan:

- **Phase 1 (Weeks 1–3):** Foundation, WP6 Ontology, WP1 Document Intake & Graph Building.
- **Phase 2 (Weeks 4–5):** WP4 Identity & Permissions, WP2 Retrieval Middleware, WP3 Hybrid Retrieval + Embeddings.
- **Phase 3 (Week 6):** WP5 Change-Driven Re-Indexing, WP7 Evaluation, deployment/handoff.

## What The POC Should Not Include

- Billing.
- Multi-tenant SaaS admin.
- Custom frontend.
- Local LLM hosting.
- Many connectors.
- Human graph editing UI.
- Retrieval that bypasses SpiceDB or provenance.

## Success Criteria

- A client can log into Open WebUI.
- Google Drive metadata and supported content can be ingested.
- Neo4j stores graph data with source provenance.
- SpiceDB enforces document visibility before retrieval.
- The system answers questions using only allowed graph context.
- Answers include source citations.
- Restricted facts do not leak through direct retrieval or graph paths.
- The stack can be deployed again for another client.
- The client can own and move the environment later.
