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

- [~] Add Google Drive credential configuration. Effort: Extra High.
- [ ] Build Drive folder scanner. Effort: High.
- [ ] Fetch file metadata. Effort: High.
- [ ] Fetch owner/creator metadata. Effort: High.
- [ ] Fetch folder ancestry metadata. Effort: Extra High.
- [ ] Fetch raw sharing and permission metadata. Effort: Extra High.
- [ ] Export Google Docs and Sheets. Effort: High.
- [ ] Download PDFs and uploaded files. Effort: High.
- [~] Store document metadata in PostgreSQL. Effort: High.
- [x] Add `retrieval_eligible = False` default field to source document records. Effort: Extra High.
- [~] Store permission metadata and source permissions version. Effort: Extra High.
- [~] Store controlled exclusion reasons for shared-link/public files in Phase 2. Effort: High.
- [~] Track content hash and modified time. Effort: High.
- [ ] Queue extraction jobs. Effort: High.
- [ ] Write audit record with user identity, timestamp, and configured scope whenever sync is triggered via API. Effort: High.
- [~] Add ingestion tests. Effort: High.
- [~] Add permission metadata storage and `source_permissions_version` tests. Effort: Extra High.
- [ ] Add test that `POST /api/ingest/drive/sync/` ignores request-body Drive scope and uses server-side configuration only. Effort: Extra High.
- [ ] Add test that unverified documents remain retrieval-ineligible. Effort: Extra High.

## Validation

- [ ] Test folder scan succeeds.
- [ ] Supported files ingest.
- [ ] Unsupported files are skipped safely.
- [ ] No file contents or credentials appear in logs.
- [ ] Metadata includes Drive file ID, URL, title, MIME type, modified time, content hash, and folder path.
- [ ] Metadata includes owner/creator, folder ancestry, sharing metadata, and source permissions version.
- [ ] Permission metadata can be refreshed without downloading or re-embedding file content.
- [ ] `source_permissions_version` changes when Drive permissions change and stays stable when only fetch time changes.
- [x] Source documents default to `retrieval_eligible = False`.
- [~] Public/shared-link files are excluded from retrieval in Phase 2 with a stored reason.
- [ ] Sync trigger audit records include actor identity, timestamp, and configured scope.
- [ ] Ingestion API ignores Drive scope/folder values from request bodies.

## Completion Status

In progress. Initial Drive configuration, PostgreSQL metadata/sync models,
source permission version hashing, controlled public-link exclusion, and mocked
metadata-sync tests are in place. Real Drive API calls, folder scanning,
content export/download, API endpoints, and Celery queueing are still pending.
