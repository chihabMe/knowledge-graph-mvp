# Phase 2: Google Drive Ingestion

## Purpose

Ingest supported Google Drive content and metadata into the system while preserving source identity and sync state.

## Scope

- Per-client Google service-account connection for selected-root content.
- Optional legacy domain-wide delegation diagnostics; employee visibility now
  belongs to ADR-015 per-user OAuth in Phase 6.
- Admin-selectable Drive root folder/shared-drive scope persisted in
  `DriveConnection`.
- Drive folder/shared-drive scanning.
- Google Docs export.
- Google Sheets export.
- PDF/downloaded file support.
- Metadata persistence in PostgreSQL.
- Drive sharing metadata capture for the optional delegated ACL mode.
- Folder ancestry and owner/creator metadata capture.
- Source permissions version tracking.
- Retrieval eligibility flag, defaulting to false.
- Sync trigger audit records.
- Content hashing.
- Ingestion job state.

## Out Of Scope

- Final graph extraction quality.
- SpiceDB permission enforcement.
- Open WebUI answering.

## Source Permissions Version

The current delegated ACL implementation computes `source_permissions_version`
as a SHA-256 hash of a canonical JSON payload built from the sorted Google
Drive permissions response for the file.

The payload should include stable permission fields such as permission ID, type,
role, email/domain, allow-file-discovery flags, and inherited status where
available. It should exclude volatile fetch timestamps.

Store this value with `last_permission_sync_time` so permission-only changes can
be detected without re-downloading or re-embedding file content.

ADR-015 changes the meaning in per-user OAuth mode: Phase 6 will migrate this
field to a deterministic, non-empty provenance generation tied to the
connection, selected-root generation, permission authority, and file ID.
Per-user access freshness belongs in `UserDocumentVisibility`, not this global
field, and visibility changes must not trigger re-embedding.

## Tasks

- [x] Add Google Drive credential configuration. Effort: Extra High.
- [x] Add admin Drive connection flow that lists eligible folders/shared drives
  and persists the selected root scope. Effort: Extra High.
- [x] Build Drive folder scanner. Effort: High.
- [x] Fetch file metadata. Effort: High.
- [~] Fetch owner/creator metadata. Effort: High. (Owner captured; Drive v3
  exposes no creator field — Revisions API follow-up, never fabricated.)
- [x] Fetch folder ancestry metadata. Effort: Extra High.
- [x] Fetch raw sharing and permission metadata. Effort: Extra High.
- [x] Export Google Docs and Sheets. Effort: High.
- [x] Download PDFs and uploaded files. Effort: High.
- [x] Store document metadata in PostgreSQL. Effort: High.
- [x] Add `retrieval_eligible = False` default field to source document records. Effort: Extra High.
- [x] Store permission metadata and source permissions version. Effort: Extra High.
- [x] Store controlled exclusion reasons for shared-link/public files in Phase 2. Effort: High.
- [x] Track content hash and modified time. Effort: High.
- [x] Queue extraction jobs. Effort: High. (Stub task; Phase 3 wires real extraction.)
- [x] Write audit record with user identity, timestamp, and configured scope whenever sync is triggered via API. Effort: High.
- [x] Add ingestion tests. Effort: High.
- [x] Add permission metadata storage and `source_permissions_version` tests. Effort: Extra High.
- [x] Add test that `POST /api/ingest/drive/sync/` ignores request-body Drive scope and uses server-side configuration only. Effort: Extra High.
- [x] Add test that unverified documents remain retrieval-ineligible. Effort: Extra High.
- [x] Add admin diagnostic for selected-root permission metadata readability. Effort: High.
- [x] Add admin endpoint to set or clear the optional delegated Workspace
  subject for domain-wide delegation testing. Effort: High.

## Validation

- [x] Test folder scan succeeds. Live-validated 2026-07-08: root discovery
  and file listing both succeeded against a temporary Drive folder using a
  pilot service account. See ADR-009 for the pilot-folder caveat (personal
  account, not a client Workspace domain).
- [~] Supported files ingest. (Metadata capture live-validated end-to-end;
  the one available live test file failed legacy `permissions.list()` — see
  ADR-009 — so the current pre-cutover code correctly excluded it before the
  content-export path ran. The content-export/storage path itself remains
  offline-test-only. ADR-015 removes readable service-account ACLs as a content
  prerequisite after the Phase 6 mode-aware cutover; live validation must then
  use the selected-root service-account content path plus separate per-user
  visibility checks.)
- [x] Unsupported files are skipped safely.
- [x] No file contents or credentials appear in logs. Live-validated
  2026-07-08: inspected `django` and `celery-worker` container logs across
  two real sync runs; no document content, permission payloads, or
  credential material present, only HTTP status/reason and task lifecycle
  lines.
- [x] Metadata includes Drive file ID, URL, title, MIME type, modified time, content hash, and folder path.
- [~] Metadata includes owner/creator, folder ancestry, sharing metadata, and source permissions version. (Creator intentionally empty — no Drive v3 creator field. Sharing metadata itself is the same open item as "Supported files ingest" above: the live test file's permissions were unreadable, so full sharing metadata was never actually captured, only the failure path.)
- [x] Permission metadata can be refreshed without downloading or re-embedding file content.
- [x] `source_permissions_version` changes when Drive permissions change and stays stable when only fetch time changes.
- [x] Source documents default to `retrieval_eligible = False`.
- [x] Public/shared-link files are excluded from retrieval in Phase 2 with a stored reason.
- [x] Sync trigger audit records include actor identity, timestamp, and configured scope.
- [x] Ingestion API ignores Drive scope/folder values from request bodies.
- [x] Admin can choose the ingestion root through a controlled backend flow;
  manual `.env` root IDs are only a bootstrap/developer fallback.
- [x] Admin can check whether the selected root's sampled files expose Drive
  permission metadata before relying on content ingestion.
- [x] Admin can set or clear the optional domain-wide delegated subject through
  a controlled backend endpoint without accepting Drive scope changes.

## Completion Status

Code complete. Drive client (BFS folder/shared-drive scan, metadata,
permissions), Docs/Sheets export + binary download, content storage with
change detection, Celery sync task with post-commit extraction queueing
(Phase 3 stub), admin root list/select endpoints, and the admin-only,
rate-limited, audited `POST /api/ingest/drive/sync/` endpoint are implemented
with offline tests (fake Drive service — no network). The admin connection flow
also retains dormant domain-wide delegated-subject code, but supported POC
deployments do not expose it. After ADR-015's Phase 6 cutover, unreadable
service-account ACLs will no longer block the POC content path; Phase 6 will
verify employee visibility with per-user OAuth over the already-indexed IDs.
Until that mode-aware implementation lands, the existing code still follows
the legacy global eligibility gate. Remaining content onboarding work is to
share the pilot folder/shared drive, select it through the backend, and run live
validation. Creator metadata remains a follow-up via the Revisions API.
