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
- Content hashing.
- Ingestion job state.

## Out Of Scope

- Final graph extraction quality.
- SpiceDB permission enforcement.
- Open WebUI answering.

## Tasks

- [ ] Add Google Drive credential configuration. Effort: Extra High.
- [ ] Build Drive folder scanner. Effort: High.
- [ ] Fetch file metadata. Effort: High.
- [ ] Export Google Docs and Sheets. Effort: High.
- [ ] Download PDFs and uploaded files. Effort: High.
- [ ] Store document metadata in PostgreSQL. Effort: High.
- [ ] Track content hash and modified time. Effort: High.
- [ ] Queue extraction jobs. Effort: High.
- [ ] Add ingestion tests. Effort: High.

## Validation

- [ ] Test folder scan succeeds.
- [ ] Supported files ingest.
- [ ] Unsupported files are skipped safely.
- [ ] No file contents or credentials appear in logs.
- [ ] Metadata includes Drive file ID, URL, title, MIME type, modified time, content hash, and folder path.

## Completion Status

Not started.

