# Phase 2: Google Drive Ingestion

## Purpose

Ingest supported Google Drive content and metadata into the system while preserving source identity and sync state.

## Scope

- Google Workspace service-account domain-wide delegation.
- Drive folder/shared-drive scanning.
- Google Docs export.
- Google Sheets export.
- PDF/downloaded file support.
- Metadata persistence in PostgreSQL.
- Drive sharing metadata capture.
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

`source_permissions_version` must be a SHA-256 hash of a canonical JSON payload
built from the sorted Google Drive permissions response for the file.

The payload should include stable permission fields such as permission ID, type,
role, email/domain, allow-file-discovery flags, and inherited status where
available. It should exclude volatile fetch timestamps.

Store this value with `last_permission_sync_time` so permission-only changes can
be detected without re-downloading or re-embedding file content.

## Tasks

- [x] Add Google Drive credential configuration. Effort: Extra High.
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

## Validation

- [~] Test folder scan succeeds. (Passes against a fake Drive service; live
  run blocked on Drake's service-account credentials.)
- [~] Supported files ingest. (Offline tests pass; live run pending credentials.)
- [x] Unsupported files are skipped safely.
- [~] No file contents or credentials appear in logs. (Code review holds: no
  content/credential logging paths, error rows store exception class only;
  live log inspection pending credentials.)
- [x] Metadata includes Drive file ID, URL, title, MIME type, modified time, content hash, and folder path.
- [~] Metadata includes owner/creator, folder ancestry, sharing metadata, and source permissions version. (Creator intentionally empty — no Drive v3 creator field.)
- [x] Permission metadata can be refreshed without downloading or re-embedding file content.
- [x] `source_permissions_version` changes when Drive permissions change and stays stable when only fetch time changes.
- [x] Source documents default to `retrieval_eligible = False`.
- [x] Public/shared-link files are excluded from retrieval in Phase 2 with a stored reason.
- [x] Sync trigger audit records include actor identity, timestamp, and configured scope.
- [x] Ingestion API ignores Drive scope/folder values from request bodies.

## Completion Status

Code complete. Drive client (BFS folder/shared-drive scan, metadata,
permissions), Docs/Sheets export + binary download, content storage with
change detection, Celery sync task with post-commit extraction queueing
(Phase 3 stub), and the admin-only, rate-limited, audited
`POST /api/ingest/drive/sync/` endpoint are implemented with offline tests
(fake Drive service — no network). Remaining: live validation against a real
Drive once Drake provides the service-account credentials, and the creator
metadata follow-up via the Revisions API.
