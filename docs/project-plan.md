# MVP Project Plan

## Goal

Build a repeatable client-owned AI knowledge graph system. The client should
log into one chat interface, ask questions about their business knowledge, and
receive answers grounded in their own documents.

## Architecture

```text
Open WebUI
  -> FastAPI OpenAI-compatible backend
    -> Neo4j retrieval
    -> OpenRouter model call

Ingestion source
  -> Processing pipeline
    -> Documents, chunks, terms, and relationships in Neo4j
```

## Tech Stack

- Interface: Open WebUI
- Backend: Python, FastAPI
- Graph database: Neo4j
- Model gateway: OpenRouter
- Deployment: Docker Compose
- Reverse proxy later: Caddy or Traefik
- First ingestion path: local mounted folder
- Next ingestion path: Google Drive API

## Version 1 Milestones

### Milestone 1: Local Runnable Stack

- Docker Compose for Open WebUI, Neo4j, and backend.
- Backend health endpoint.
- OpenAI-compatible chat endpoint.
- Environment-based configuration.

### Milestone 2: File Ingestion

- Read supported files from `data/import`.
- Extract text and metadata.
- Chunk documents.
- Store documents and chunks in Neo4j.
- Extract basic terms and relationships.

### Milestone 3: Graph Retrieval Chat

- Accept OpenAI-compatible chat requests from Open WebUI.
- Search relevant graph nodes and chunks.
- Build a grounded prompt.
- Call OpenRouter.
- Return the answer in OpenAI-compatible format.

### Milestone 4: Google Drive Connector

- Create Google Cloud OAuth app.
- Authorize a client Drive folder.
- List and sync files.
- Feed changed files into the existing ingestion pipeline.
- Track Google Drive file IDs and modified timestamps.

### Milestone 5: Deployment Template

- Add Caddy/Traefik SSL.
- Add per-client subdomain settings.
- Add backup/restore docs.
- Add migration docs for moving to another VM.

## What Version 1 Should Not Include

- Billing
- Multi-tenant SaaS admin
- Custom frontend
- Local LLM hosting
- Complex permissions
- Many connectors
- Human graph editing UI

## Success Criteria

- A client can log into Open WebUI.
- Their documents are ingested into Neo4j.
- The system answers questions using graph context.
- Answers are better than simple keyword search.
- The stack can be deployed again for another client.
- The client can own and move the environment later.
