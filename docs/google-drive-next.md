# Google Drive Connector Plan

The MVP currently ingests from `data/import` so the graph/chat loop can be
tested immediately. Google Drive should be added as the next connector without
changing the downstream ingestion logic.

## Proposed Flow

1. Create a Google Cloud project.
2. Enable the Google Drive API.
3. Configure OAuth consent for the client.
4. Store OAuth credentials as backend secrets.
5. Let the backend list files in an approved folder.
6. Download supported file types into memory.
7. Convert each file into a `DocumentRecord`.
8. Send each record into `GraphStore.upsert_document`.
9. Save Google file ID, modified time, and checksum in Neo4j.
10. Skip unchanged files on later syncs.

## Suggested Future Endpoints

```http
GET /integrations/google-drive/auth-url
GET /integrations/google-drive/callback
POST /ingest/google-drive
```

## Metadata To Store

- Google Drive file ID
- File name
- Folder path
- MIME type
- Web URL
- Created time
- Modified time
- Last ingested time
- Content checksum

## Implementation Note

The connector should produce the same `DocumentRecord` model used by local file
ingestion. That keeps retrieval and graph storage independent from the source.
