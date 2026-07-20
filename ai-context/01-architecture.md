# Architecture

## Target Stack

| Layer | Technology | Role |
| --- | --- | --- |
| Host environment | Ubuntu VM | Single-customer deployment host |
| Deployment | Docker Compose | Repeatable per-customer service stack |
| Chat UI | Open WebUI | User-facing chat interface |
| Backend | Django + Django REST Framework | API, admin, metadata, orchestration |
| Background jobs | Celery + Celery Beat | Ingestion, permission sync, indexing, evaluation |
| Queue/cache | Redis | Celery broker, locks, short-lived cache |
| App database | PostgreSQL | Django models, job state, config, evaluation records |
| Knowledge graph | Neo4j | Graph entities, relationships, chunks, vector index |
| Authorization | SpiceDB | Relationship-based permission engine |
| SpiceDB datastore | PostgreSQL | Persistent SpiceDB datastore |
| Extraction/indexing | neo4j-graphrag first, with Graphify/Graphiti evaluated behind an adapter | Text extraction, chunking, embeddings, graph extraction |
| Model gateway | OpenRouter | LLM access |
| Reverse proxy | Traefik | Routing, TLS, service exposure |
| Logs | Dozzle | Live Docker logs |

## Main Services

### Django API

Responsibilities:

- Public REST API.
- Admin configuration.
- Google Drive connection records.
- Ingestion job records.
- Evaluation questions/results.
- Health checks.
- Query orchestration.

### Celery Worker

Responsibilities:

- Drive file sync.
- Per-user Drive visibility sync.
- Text extraction.
- Entity/relationship extraction.
- Neo4j writes.
- SpiceDB relationship writes.
- Change-feed processing.
- Evaluation runs.

### Redis

Responsibilities:

- Celery broker.
- Celery result backend if needed.
- Distributed locks.
- Short-lived cache.

Redis is not a system of record.

### PostgreSQL

Responsibilities:

- Django app metadata.
- Job status.
- Evaluation data.
- Customer configuration.
- Optional separate database/schema for SpiceDB datastore.

### Neo4j

Responsibilities:

- Documents.
- Chunks.
- Entities.
- Relationships.
- Source provenance.
- Vector indexes.
- Graph traversal retrieval.

### SpiceDB

Responsibilities:

- Users.
- Groups.
- Folders.
- Files/documents.
- Visibility relationships.
- Permission checks before retrieval.

### Open WebUI

Responsibilities:

- Chat interface.
- User login through Google OAuth/OIDC.
- Calls Django through its server-side OpenAI-compatible connection.
- Forwards the authenticated user in a short-lived signed identity JWT.

The primary retrieval path does not use an Open WebUI Pipeline/Function. The
connection uses a separate service bearer key, and direct browser-to-backend
model connections are disabled.

## Query Flow

```text
User asks question in Open WebUI
  -> Open WebUI authenticates the user through Google OAuth/OIDC
  -> Open WebUI sends service credentials + signed identity JWT + question
  -> Django verifies both credentials
  -> Django matches the signed email to an active per-user Drive authorization
  -> backend asks SpiceDB which documents user can view
  -> backend intersects with fresh per-user visibility evidence
  -> backend queries Neo4j only across allowed source provenance
  -> backend assembles context + citations
  -> backend calls OpenRouter
  -> backend returns answer to Open WebUI
```

## Ingestion Flow

```text
Google Drive file/change detected
  -> service-account Celery task downloads/exports selected-root content
  -> Drive metadata and selected-root provenance stored
  -> text conversion and chunking
  -> extraction into entities/relationships
  -> embeddings generated
  -> Neo4j updated with provenance
```

## Per-User Visibility Flow

```text
Workspace admin approves the Django Drive OAuth client
  -> employee grants Drive metadata access once
  -> Django stores only an encrypted refresh credential
  -> Celery checks already-indexed file IDs as that employee
  -> direct user/document relationships verified in SpiceDB
  -> matching per-user freshness evidence committed in PostgreSQL
```

## Deployment Shape

Use Docker Compose for the first customer deployment:

- `traefik`
- `django`
- `celery-worker`
- `celery-beat`
- `redis`
- `postgres`
- `neo4j`
- `spicedb`
- `open-webui`
- `dozzle`
