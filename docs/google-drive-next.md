# Google Drive Ingestion Plan

Phase 2 starts the real client-data path. The goal is not answer generation yet.
The goal is to safely ingest Google Drive file records, supported content, and
permission-related metadata so later Neo4j and SpiceDB phases have reliable
source data.

## Default Access Model

The first pilot assumes Google Workspace service-account access with
domain-wide delegation.

Per-user OAuth can be evaluated later if a customer cannot support domain-wide
delegation, but it should not be the default for the first POC because it makes
bulk ingestion, scheduled sync, and permission refresh harder.

## Phase 2 Flow

1. Configure Google Cloud project and Drive API access.
2. Configure service account with Workspace domain-wide delegation.
3. Store service-account configuration as backend secrets, never in source.
4. Configure one allowed folder or shared drive scope for the pilot.
5. Start a Drive sync run from the Django backend.
6. Celery lists files in the configured scope.
7. For each file, store metadata in PostgreSQL before content extraction.
8. Capture permission-related metadata for later SpiceDB sync.
9. Export or download supported content types.
10. Compute a SHA-256 content hash and compare modified time/checksum.
11. Queue extraction/indexing work only when content changed.
12. Allow permission-only refreshes without re-downloading or re-embedding content.

## Retrieval Eligibility Gate

Drive ingestion does not make a document queryable by itself.

A document must not be eligible for retrieval until:

- Its source metadata has been stored.
- Its source provenance can be attached to derived graph records.
- Its Drive permission metadata has been synced.
- The matching SpiceDB relationships have been written and verified.

If SpiceDB is unavailable, stale, or missing relationships for a document,
retrieval must fail closed. The backend should return no context for that
document rather than allowing unfiltered Neo4j retrieval.

The source document record should default to `retrieval_eligible = false`.
Phase 2 creates and preserves this field. Phase 4 is responsible for switching
it to true only after SpiceDB relationships are written and verified.

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

Required permission-related metadata:

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

## Source Permissions Version

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
POST /api/ingest/drive/sync/
POST /api/permissions/sync/
GET /api/health/
```

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

- A configured folder/shared drive can be scanned.
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
