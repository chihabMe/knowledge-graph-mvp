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

## ADR-008: Single-Tenant Deployment — One Self-Contained Compose Stack Per Client

Decision: The product ships as one docker compose stack per client. Each
deployment holds exactly one client's Postgres, Neo4j, SpiceDB, Redis, and
Open WebUI. There is no shared multi-tenant instance.

Reason:

- Isolation *is* the product promise: one client's documents, graph, and
  permission tuples never share a database or network with another client's.
  Infrastructure-level isolation is stronger than any in-app namespacing.
- It removes the Google restricted-scope verification burden (CASA) that a
  public multi-tenant OAuth app would require.
- Per-deployment `.env` + mounted secrets become the intended configuration
  surface, not a shortcut.
- Cost: ops effort grows linearly with clients (upgrades, monitoring,
  backups). Phase 3+ still keys all graph/permission data by connection id so
  consolidation into a shared control plane stays possible later.

Status: Accepted (2026-07-08).

## ADR-009: Drive Access Via Per-Client Service Account, Provisioned By Us; Dynamic "Share To Connect" Folder Selection

Decision: Each client deployment gets its own Google service account,
created by us in our GCP project (exception: Drake's pilot uses an SA in his
own project). Clients never touch GCP. Connecting Drive = the client shares
a folder with the service account's email as Viewer — the same action as
sharing with a person. The current Drive-ingestion work must include an admin
connection/settings flow that lists folders shared with the service account
("shared with me"), lets the admin choose the ingestion root, and writes the
chosen folder/shared-drive scope into `DriveConnection`. No per-user OAuth
tokens.

Reason:

- Zero technical work for non-technical clients; revocation is equally
  non-technical (unshare the folder).
- One SA per client bounds the blast radius of a leaked key to that client.
  A single global SA for all clients was rejected for exactly this reason.
- Per-user OAuth is not the default: tokens die with the employee who granted
  them, grant broader access than the picked folder, and public-app
  verification is expensive.
- This resolves the previously open "domain-wide delegation vs. per-user
  OAuth" question. Delegation remains the documented fallback for Workspace
  domains that block external sharing or restrict permission-list reads.

**ACL visibility under folder-level sharing — resolved 2026-07-08 by live
test.** Sharing a folder with the service account (tested at Editor role,
via `kg-graph` in a personal Google account, service account in Drake's GCP
project) lets the service account list and read files inside it, but
`permissions.list()` on those files returns `403 insufficientFilePermissions`
— folder-level sharing does not grant "manage permissions" rights on the
files inside it. This is a Drive API access-control property, not specific
to personal vs. Workspace accounts, so it is expected to reproduce for
Drake's real pilot folder too. Practical effect: under the default
"share to connect" model, per-file permission metadata will generally be
unreadable, and Phase 2 now fails those documents closed
(`exclusion_reason = permission_metadata_incomplete`, `retrieval_eligible =
False`) instead of crashing the sync or guessing. **Domain-wide delegation
is therefore not just a fallback for edge cases — it is the expected path
to get real per-file permission metadata for any client**, and Phase 4
(SpiceDB) planning should assume delegation is needed rather than treating
it as optional hardening.

Rule for root changes: changing the root folder/shared drive is a re-scope
operation — documents outside the new root must lose retrieval eligibility and
their graph/SpiceDB footprint, otherwise switching roots silently widens what
is answerable.

Status: Accepted (2026-07-08). Updated 2026-07-08: dynamic folder/shared-drive
selection is no longer deferred; it is the next Phase 2 product path before
asking the client to provide manual root IDs. Updated 2026-07-08 (live
validation): ACL-visibility question resolved — see above.

## ADR-010: neo4j-graphrag As The Extraction Engine; Per-Document Entity Scoping

Decision: Use the official `neo4j-graphrag` Python package (Apache-2.0,
maintained by Neo4j) as the LLM extraction engine, wrapped behind the
`ExtractionAdapter` boundary in `graph/extraction.py`. Only its extraction
components are used — chunk/entity/relationship writing stays in our own
fail-closed writers (`graph/writer.py`), and its entity-resolution component
is deliberately **not** used.

Reason:

- Evaluation (2026-07-09) of `neo4j-graphrag`, Graphiti, Microsoft GraphRAG,
  LightRAG/cognee, and LlamaIndex PropertyGraphIndex against our constraints
  (Neo4j-native, closed ontology, fact-level provenance, self-hostable LLM):
  - `neo4j-graphrag` links extracted entities back to their source chunk and
    chunks to documents natively, supports schema-grounded extraction with
    strict enforcement, has swappable components, and accepts any
    OpenAI-compatible LLM (OpenRouter).
  - Graphiti has strong episode provenance but is shaped for bi-temporal
    agent memory and manages the graph its own way — it would fight our
    provenance/guard model.
  - Microsoft GraphRAG is batch/Parquet-based and not Neo4j-native;
    LightRAG/cognee trade provenance rigor for cost; LlamaIndex is a viable
    runner-up but drags in a full framework for what the Neo4j package does
    natively.
- **Resolves the open fact-level vs. document-level provenance question:
  fact-level provenance is supported and adopted.** Every extracted entity
  and relationship carries `source_document_id` + chunk linkage, so Phase 5
  retrieval can filter at fact level rather than defaulting to strict
  document-level visibility.
- **Entity nodes are scoped per document** (identity =
  `source_document_id` + type + normalized name), not merged across
  documents. Cross-document entity resolution is a permission hazard: a
  merged node derived from a restricted and an unrestricted document would
  put restricted facts one hop from unrestricted context ("a fact one
  graph-hop away from a restricted file is still restricted"). Resolution
  can be revisited after SpiceDB enforcement exists (Phase 4+), never
  before.
- Extraction output is strictly validated against the declared ontology
  (`validate_extraction_result`) and rejected loudly on violation — the
  engine's own schema grounding is a second fence, not the enforcement
  point.

Status: Accepted (2026-07-09). LLM-backed extraction was smoke-validated
live on 2026-07-09 (see the Phase 3 tracker); what remains is a production
OpenRouter configuration (real key and model). The deterministic
`ParagraphChunkExtractor` remains the default engine until then.

## Open / Needs Explicit Confirmation

Not yet decisions — flagged so they don't get silently locked in by omission:
- **Freshness/recency scoring** (timestamp, importance, last-updated metadata influencing retrieval priority) — the client's own idea from 2026-05-02, not scoped into any current milestone or work package. Candidate for backlog, not part of this POC unless the client asks for it explicitly.
