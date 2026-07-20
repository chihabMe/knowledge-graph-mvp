# Task Backlog

This backlog is ordered by dependency and risk.

## Phase 0: Repository And Infrastructure

- Initialize Git repository.
- Create GitHub repository.
- Create Docker Compose infrastructure layout.
- Add Traefik routing structure.
- Add AI-agent context docs.
- Preserve old prototype compose as reference.

## Phase 1: Django Backend Foundation

- Create Django project.
- Add Django REST Framework.
- Configure PostgreSQL.
- Configure Redis.
- Configure Celery worker.
- Configure Celery Beat.
- Add health endpoint.
- Add Django admin.
- Add base app models for customers, Drive connections, documents, jobs, evaluation records.

## Phase 2: Google Drive Ingestion

- Add Google service-account credentials handling.
- Add admin Drive connection/folder-selection flow that lists eligible folders
  and shared drives, then writes the selected root into `DriveConnection`.
- Add Drive folder/shared-drive scanner.
- Export Google Docs.
- Export Google Sheets.
- Download PDFs and uploaded files.
- Store document metadata in PostgreSQL.
- Add `retrieval_eligible = False` default on source document records.
- Store selected-root membership, folder ancestry, owner metadata where
  available, and the provenance generation required by the active permission
  authority. Full ACL payloads remain optional legacy-mode metadata.
- Store controlled exclusion reasons for unsupported/out-of-scope content.
  Shared-link/public ACL classification applies only to delegated ACL mode;
  per-user OAuth grants only when Google confirms the actual user's access.
- Queue extraction jobs.
- Track content hashes and modified times.
- Add tests for mode-aware permission metadata and source permissions version
  computation.
- Add sync trigger audit logging.
- Add configured-scope enforcement tests for ingestion endpoints.
- Add tests that unverified documents remain retrieval-ineligible.

## Phase 3: Neo4j Graph And Provenance

- Define ontology.
- Evaluate extraction engines for provenance support behind an adapter.
- Create Neo4j constraints/indexes.
- Store Document and Chunk nodes.
- Store extracted entity nodes.
- Store extracted relationship edges.
- Attach source provenance to every graph element.
- Add query-layer guard that excludes any Neo4j node, relationship, or chunk missing source provenance.
- Add vector embeddings/indexes.

## Phase 4: SpiceDB Permissions

- Add SpiceDB service to Docker Compose.
- Define SpiceDB schema for users, groups, folders, and documents.
- Sync Drive permissions into SpiceDB.
- Keep source documents globally content/provenance eligible only when their
  active permission mode is ready; user retrieval additionally requires the
  mode-specific SpiceDB relationship and fresh evidence.
- Add permission checks in backend.
- Add allowed-document list lookup for retrieval.

## Phase 5: Permission-Safe Retrieval

- Build query endpoint.
- Enforce SpiceDB pre-filter.
- Query Neo4j only across allowed provenance.
- Assemble answer context.
- Call OpenRouter.
- Return answer with citations.
- Add safe refusal behavior.

## Phase 6: Open WebUI Integration

- Configure Open WebUI service.
- Configure Google OAuth/OIDC.
- Add the selected Django OpenAI-compatible model and chat endpoints over the
  existing permission-safe query service.
- Authenticate the Open WebUI service and verify its short-lived signed user
  identity JWT in Django.
- Confirm the verified Google identity is available to SpiceDB lookup.
- Add a separate admin-approved Django Drive OAuth connection flow with
  encrypted refresh-token storage.
- Check only already-indexed file IDs as each connected user; do not enumerate
  the user's Drive or use the token for content ingestion.
- Write and exactly verify direct per-user document relationships in SpiceDB.
- Intersect SpiceDB results with fresh matching per-user visibility evidence.
- Invalidate grants on root/account/mode changes, OAuth disconnect, refresh
  failure, and evidence expiry.
- Test allowed and restricted users through the real chat interface.

## Phase 7: POC Freshness And Evaluation

- Schedule 15-minute Drive content reconciliation with locking, retry, and
  stale-run recovery.
- Preserve unchanged extracted content eligibility and close it for changed or
  indeterminate content.
- Keep the existing 15-minute permission refresh and 30-minute fail-closed
  evidence lifetime.
- Surface identity-free permission and content-sync freshness through the
  authenticated health endpoint and structured logs.
- Run private evaluation fixtures through an operator management command; keep
  real client fixtures ignored and do not persist results.
- Run the live fail-closed drill before closeout.

## Phase 8: Deployment Handoff

- Add backup docs.
- Add restore docs.
- Add maintenance checklist.
- Document the permission-freshness SLA and synchronization incident runbook.
- Define, approve, configure, and test a chat-history deletion/retention policy.
- Add environment setup docs.
- Add demo script.

## Phase 9: Optional Production Hardening

- Select and validate an external alert destination if production operations
  require delivery guarantees.
- Load-test the 5-minute refresh/10-minute evidence-expiry target before use.
- Add Drive change-feed polling and optional push only when measured latency
  requirements justify the extra state and failure modes.
- Add Shared Drive change-log fan-out where the deployed corpus needs it.
- Add scheduled/persisted evaluation only when operator-run assurance is no
  longer sufficient.
