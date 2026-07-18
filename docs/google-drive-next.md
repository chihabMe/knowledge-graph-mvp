# Google Drive Ingestion Plan

> **Status update (2026-07-14):** The service-account selected-root content
> path in this document remains current. ADR-015 supersedes its ACL/group
> permission-authority sections: the POC will use admin-approved per-user Drive
> OAuth and the active completion plan in
> `docs/phase-6-pre-authorized-oauth-completion-plan.md`.

Phase 2 starts the real client-data path. The goal is not answer generation yet.
The goal is to safely ingest Google Drive file records, supported content, and
permission-related metadata so later Neo4j and SpiceDB phases have reliable
source data.

## Default Access Model

The first pilot uses a per-client Google service account and share-to-connect
setup for content only: the client shares the intended folder or Shared Drive,
then an admin selects that root. Each employee separately grants the approved
Django Drive OAuth client metadata access so Google can verify that employee's
visibility over the already-indexed IDs. Domain-wide delegation is optional,
not the POC default or an automatic fallback.

## Phase 2 Flow

1. Configure Google Cloud project and Drive API access.
2. Create/provision the per-client service account.
3. Provide the content identity through keyless ADC: local development uses
   short-lived service-account impersonation and Google Cloud deployment uses
   an attached service account. Keep the legacy JSON-key path only for an
   explicit deployment that cannot use ADC; never store credentials in source.
4. Let the client share the intended folder/shared drive with the service
   account.
5. In the backend admin connection flow, list eligible folders/shared drives.
6. Persist the admin's selected root scope in `DriveConnection`.
7. Start a Drive sync run from the Django backend.
8. Celery lists files in the selected scope.
9. For each file, store metadata in PostgreSQL before content extraction.
10. Capture selected-root and provenance metadata; full ACL capture is required
    only by the optional legacy delegated mode.
11. Export or download supported content types.
12. Compute a SHA-256 content hash and compare modified time/checksum.
13. Queue extraction/indexing work only when content changed.
14. Allow permission-only refreshes without re-downloading or re-embedding content.

## Retrieval Eligibility Gate

Drive ingestion does not make a document queryable by itself.

A document must not be eligible for retrieval for a user until:

- Its source metadata has been stored.
- Its source provenance can be attached to derived graph records.
- Google has confirmed that user's access to the already-indexed file ID.
- The direct user/document SpiceDB relationship has been written and verified.
- Matching per-user visibility evidence remains fresh.

If SpiceDB is unavailable, stale, or missing relationships for a document,
retrieval must fail closed. The backend should return no context for that
document rather than allowing unfiltered Neo4j retrieval.

The source document record should default to `retrieval_eligible = false`.
Phase 2 creates and preserves this coarse global deny field. In per-user mode,
it never grants access; Phase 6 additionally requires the direct SpiceDB
relationship and fresh per-user evidence.

## Metadata To Store

Required file metadata:

- Google Drive file ID
- File name/title
- MIME type
- Web URL
- Created time
- Modified time
- Last ingested time
- Content hash/checksum
- Folder path
- Parent folder IDs
- Shared drive ID, if applicable

Legacy delegated-mode permission metadata (not required by per-user mode):

- Owner/creator identity where available
- Direct users with access
- Direct Google Groups with access
- Domain/shared-link visibility
- Folder ancestry used for inherited access
- Raw Drive permission IDs where useful for change detection
- Source permissions version or hash
- Last permission sync time

## Public And Shared-Link Handling

Shared-link and domain-wide visibility must be captured even if the first pilot
does not expose those documents through chat.

Phase 2 default: exclude public, domain-wide, and shared-link files from
retrieval and store a controlled exclusion reason.

Represent exclusion reasons with a controlled field on the source document
record, for example `exclusion_reason`, using a fixed enum/choice set:

- `public_link_not_supported`
- `domain_wide_visibility_not_supported`
- `unsupported_mime_type`
- `missing_required_metadata`
- `permission_metadata_incomplete`

Later phases may model public/domain visibility in SpiceDB with an explicit
`public` or `domain` principal, but those files must remain ineligible for
retrieval until that model exists and is tested.

Do not silently treat shared-link files as private, and do not silently expose
them to every user.

## Source Permissions Version (legacy delegated interpretation)

`source_permissions_version` is a SHA-256 hash of a canonical JSON payload built
from the sorted Google Drive permissions response for the file.

The canonical payload should include stable permission fields such as permission
ID, type, role, email/domain, allow-file-discovery flags, and inherited status
where available. It should exclude volatile fetch timestamps.

Store `source_permissions_version` with `last_permission_sync_time`. A changed
permissions version should trigger a permission refresh and later SpiceDB sync
without requiring content re-download or re-embedding.

## Supported POC File Types

Required:

- Google Docs
- Google Sheets
- PDFs

Optional if low-risk:

- Markdown/text files
- CSV files
- Word documents

Unsupported files should be skipped safely with a controlled reason. Do not log
raw file contents.

## Backend Shape

Use Django models for:

- Drive connection configuration
- Drive sync run state
- Source document records
- Source file metadata
- Source permission metadata
- Extraction job state

Use Celery tasks for:

- Folder/shared-drive scanning
- Metadata refresh
- Permission metadata refresh
- Content export/download
- Content hash computation
- Queueing later extraction jobs

The Drive connector should produce a stable internal document record shape so
later Neo4j, SpiceDB, and retrieval code do not depend directly on Google API
objects.

## Suggested API Endpoints

```http
GET /api/ingest/drive/roots/
POST /api/ingest/drive/connection/root/
POST /api/ingest/drive/connection/delegated-subject/
GET /api/ingest/drive/permissions/check/
POST /api/ingest/drive/sync/
POST /api/permissions/sync/
GET /api/health/
```

`GET /api/ingest/drive/roots/` lists root folders and shared drives visible to
the configured Google Drive connection.

`POST /api/ingest/drive/connection/root/` accepts a `scope_type` and `root_id`
from that visible list, then persists the selected ingestion scope in
`DriveConnection`. When the selected root changes, existing documents for that
connection are marked non-retrievable until the new scope is synced; the
response includes `rescoped_document_count` for operator visibility.

`POST /api/ingest/drive/connection/delegated-subject/` accepts a single
`delegated_subject_email` value. A valid email configures the Workspace user
used for domain-wide delegation; an empty string clears it. When the value
changes, retrievable documents for that connection are marked non-retrievable
until permissions are refreshed under the new identity. The endpoint is
admin-only, rate limited, and ignores any Drive root/scope fields in the
request body.

`GET /api/ingest/drive/permissions/check/` samples files under the selected
root and returns only counts/status for Drive ACL readability and folder-listing
failures. It is an operator diagnostic for validating whether the configured
service account or delegated subject can call `permissions.list()` before
content ingestion is trusted. It must not return raw permission entries,
filenames, folder names, or document contents.

`POST /api/ingest/drive/sync/` starts or resumes Drive ingestion for the
configured pilot scope. The Drive scope, folder ID, or shared drive ID must be
read from server-side configuration. The endpoint must ignore any Drive scope
or folder value sent in the request body.

`POST /api/permissions/sync/` refreshes permission metadata and, in later
phases, writes SpiceDB relationships.

Phase ownership: Phase 2 may create the endpoint stub and refresh/store
permission metadata. Phase 4 writes and verifies SpiceDB relationships.

All endpoints that trigger background work must require admin-level
authentication, must be rate limited, and must write audit records for who
triggered the job. They must not accept arbitrary Drive scopes from untrusted
request bodies.

## Validation

- The backend can list eligible folders/shared drives for an admin to choose.
- The selected folder/shared drive is persisted and scanned.
- Google Docs, Sheets, and PDFs are detected.
- Supported files are exported or downloaded.
- Unsupported files are skipped without crashing the sync.
- Metadata is persisted before extraction.
- Permission metadata is captured early enough for Phase 4 SpiceDB sync.
- Permission-only updates can be detected separately from content changes.
- Documents are not retrievable until SpiceDB relationships are written and verified.
- Admin-only ingestion/sync endpoints enforce authentication and rate limits.
- Sync endpoints read Drive scope from server-side configuration only.
- Shared-link/public files are excluded from retrieval in Phase 2 with a controlled reason.
- No credentials, tokens, raw document text, or full prompts appear in logs.
