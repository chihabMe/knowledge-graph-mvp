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

## ADR-006B: Google Drive As Primary Ingestion Source; Notion Second; Obsidian Optional

Decision: Build Google Drive ingestion first. Notion is a likely second source later. Obsidian is not a required source — it stays an optional power-user feature.

Reason:

- The client's target buyers are non-technical organizations, not the DIY/power-user crowd Obsidian setup assumes.
- Google Drive and Notion are the sources most target users already have their organizational knowledge in.

Status: Accepted (2026-06-23).

## ADR-006C: OpenRouter As The Model Gateway

Decision: Use OpenRouter for AI model access rather than calling a single model provider directly.

Reason:

- Lets the client (and their customers) swap models without code changes, including cost-efficient or zero-data-retention providers.
- Avoids hard-coding the product to one vendor while cloud-vs-local-model tradeoffs are still unsettled for this market.

Status: Accepted (2026-06-30).

## ADR-006D: Open WebUI As The Only Chat Front End For V1

Decision: Do not build a custom frontend. Open WebUI is the chat interface for the proof of concept.

Reason:

- It is off-the-shelf, supports Google OAuth/OIDC SSO, and has a Pipeline/Function slot for custom retrieval middleware.
- Building a custom UI is explicitly out of scope for the POC (see `docs/project-plan.md`, "What The POC Should Not Include") and would trade build speed for polish the client does not need yet.

Status: Accepted (2026-06-30).

## ADR-007: Treat Graphify As A Helper, Not The Core Runtime

Decision: Graphify can be evaluated as an extraction or AI-navigation helper, but it should not own the whole ingestion, permission, or retrieval architecture.

Reason:

- The hard part of this project is permission-safe Drive ingestion, provenance, re-indexing, and retrieval filtering.
- Graphify may help create or inspect graph structure, but the backend must control Drive syncing, source provenance, SpiceDB checks, and Neo4j writes.
- Keeping extraction behind an adapter lets the project compare `neo4j-graphrag`, Graphify, and Graphiti without locking the architecture too early.

Status: Accepted.

## Open / Needs Explicit Confirmation

Not yet decisions — flagged so they don't get silently locked in by omission:

- **Service account with domain-wide delegation vs. per-user OAuth for Drive access.** The developer scope doc (`output/pdf/organizational-knowledge-graph-developer-scope-v6.pdf`, WP1) still lists this as unresolved, but `docs/project-plan.md` Milestone 2 already assumes domain-wide delegation. Confirm this with the client before WP1 implementation locks it in.
- **Fact-level vs. document-level provenance granularity** — depends on whether the chosen extraction engine (neo4j-graphrag / Graphify / Graphiti) supports per-fact tagging. Decides how strict the WP6 visibility default must be. Deferred until extraction engine evaluation (Week 2–3).
- **Freshness/recency scoring** (timestamp, importance, last-updated metadata influencing retrieval priority) — the client's own idea from 2026-05-02, not scoped into any current milestone or work package. Candidate for backlog, not part of this POC unless the client asks for it explicitly.
