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
- Store Drive sharing metadata, owner/creator data, folder ancestry, and permission version.
- Store controlled exclusion reasons for shared-link/public files in Phase 2.
- Queue extraction jobs.
- Track content hashes and modified times.
- Add tests for permission metadata storage and source permissions version computation.
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
- Switch source documents to retrieval-eligible only after SpiceDB relationships are written and verified.
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
- Connect Open WebUI to backend endpoint or pipeline.
- Confirm user identity is available to backend.

## Phase 7: Change Feed And Evaluation

- Add Google Drive change feed handling.
- Separate content updates from permission-only updates.
- Add evaluation question set.
- Add leak tests.
- Add scheduled evaluation job.

## Phase 8: Deployment Handoff

- Add backup docs.
- Add restore docs.
- Add maintenance checklist.
- Add environment setup docs.
- Add demo script.
