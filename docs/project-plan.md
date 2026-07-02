# MVP Project Plan

## Goal

Build a repeatable client-owned AI knowledge graph system. The client should log into one chat interface, ask questions about their business knowledge, and receive answers grounded in their own documents.

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

## Version 1 Milestones

### Milestone 1: Backend Foundation

- Docker Compose for infrastructure and app services.
- Django backend.
- Health endpoint.
- Celery worker.
- PostgreSQL, Redis, and Neo4j connectivity.
- Repeatable lint/test/start commands.

### Milestone 2: Google Drive Metadata Ingestion

- Configure service-account domain-wide delegation.
- Store Drive connection records.
- Track sync runs.
- List supported Drive files.
- Store file metadata and content hashes in PostgreSQL.

### Milestone 3: Content Extraction And Graph Build

- Export supported Drive files.
- Extract text and metadata.
- Chunk documents.
- Store documents and chunks in Neo4j.
- Extract entities and relationships.
- Preserve provenance on every derived graph element.

### Milestone 4: SpiceDB Permission Sync

- Model users, groups, folders, and documents.
- Sync Drive sharing metadata.
- Resolve folder/group inheritance.
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

## What Version 1 Should Not Include

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
