# Architecture Decisions

## ADR-001: Use Django Instead Of FastAPI For The Main Backend

Decision: Use Django + Django REST Framework for the target backend.

Reason:

- The project needs admin screens, app metadata, job records, evaluation records, and user/config management.
- Django gives a stronger foundation for a business application than a minimal API-only framework.
- Celery and PostgreSQL integration patterns are mature.

Status: Accepted.

## ADR-002: Keep Neo4j As The Knowledge Graph Database

Decision: Use Neo4j for graph data and vector retrieval.

Reason:

- The core product depends on graph relationships.
- Neo4j supports graph traversal and vector indexes in one store.
- Adding a separate vector DB in v1 increases permission-filtering complexity.

Status: Accepted.

## ADR-003: Keep SpiceDB For Authorization

Decision: Use SpiceDB for permission checks.

Reason:

- The product depends on Google Drive-like relationship permissions.
- SpiceDB is designed for relationship-based access control.
- Custom permission logic is too risky for permission-safe retrieval.

Status: Accepted.

## ADR-004: Use Docker Compose For First Deployments

Decision: Use Docker Compose on a single-customer VM.

Reason:

- The first product is implementation-led, not multi-tenant SaaS.
- Each customer gets an isolated deployment.
- Docker Compose is simpler than Kubernetes for early deployments.

Status: Accepted.

## ADR-005: Use Traefik For Routing And TLS

Decision: Use Traefik as reverse proxy.

Reason:

- User prefers Traefik.
- It handles Docker service routing well.
- It can route Open WebUI, Django, Dozzle, and Uptime Kuma cleanly.

Status: Accepted.

## ADR-006: FastAPI Prototype Is Not The Target Backend

Decision: The old FastAPI/local-file prototype is not the target implementation. Django + DRF is the canonical backend.

Reason:

- It was useful for proving the first local-file concept.
- The target stack is now Django + DRF + Celery.

Status: Accepted.

## ADR-007: Treat Graphify As A Helper, Not The Core Runtime

Decision: Graphify can be evaluated as an extraction or AI-navigation helper, but it should not own the whole ingestion, permission, or retrieval architecture.

Reason:

- The hard part of this project is permission-safe Drive ingestion, provenance, re-indexing, and retrieval filtering.
- Graphify may help create or inspect graph structure, but the backend must control Drive syncing, source provenance, SpiceDB checks, and Neo4j writes.
- Keeping extraction behind an adapter lets the project compare `neo4j-graphrag`, Graphify, and Graphiti without locking the architecture too early.

Status: Accepted.
