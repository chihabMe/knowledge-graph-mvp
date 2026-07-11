# Phase 4: SpiceDB Permissions

## Purpose

Model and enforce Google Drive visibility using SpiceDB before any retrieval occurs.

## Scope

- SpiceDB schema.
- Users.
- Groups.
- Folders.
- Documents.
- Inherited visibility.
- Permission sync from Drive metadata.
- Allowed-document filtering API.
- Permission-only Drive scans and folder snapshots.
- Nested Google Group resolution through the read-only Admin SDK.

## Out Of Scope

- Full answer generation.
- UI work.
- Neo4j retrieval, prompt assembly, OpenRouter calls, and Open WebUI identity.

## Selected Contract

- Checked-in `kgm/`-prefixed schema (`kg/` is invalid in SpiceDB) with distinct Drive roles combined into
  `view`, explicit folder parents, and recursive group subject sets.
- Deterministic connection-scoped opaque IDs; no raw emails or Drive IDs in
  logs or public responses.
- Public/anyone and domain principals remain excluded.
- Permission runs are durable, admin-only, rate-limited, and server-scoped;
  Celery beat also schedules them periodically because group-membership
  revocations never change a document's ACL hash, so the beat interval is the
  revocation bound. A sweeper fails runs stuck in RUNNING.
- Candidate documents become eligible only when the exact tuple set is
  verified at least as fresh as the final write token, the ACL version still
  matches, and at least one grant path exists. A failed run keeps the previous
  verified state instead of blanking the connection; the fully consistent
  SpiceDB lookup stays the query-time gate.
- Stale revocation occurs only after a complete scan; all incomplete external
  state fails closed.
- Phase 5 obtains its allowlist only through fully consistent SpiceDB
  `LookupResources` plus the active/version/verification evidence gate.

## Tasks

- [x] Design SpiceDB schema. Effort: Extra High.
- [x] Add schema migration/load workflow. Effort: Extra High.
- [x] Sync Drive users and groups. Effort: Extra High.
- [x] Sync folder/document relationships. Effort: Extra High.
- [x] Sync document permissions. Effort: Extra High.
- [x] Implement allowed-document lookup. Effort: Extra High.
- [x] Add permission leak tests. Effort: Extra High.

## Validation

- [x] User A can access allowed documents.
- [x] User B cannot access restricted documents.
- [x] Folder inheritance works.
- [x] Group access works.
- [x] Permission-only changes do not require re-embedding.
- [x] No restricted document existence is leaked through API responses.

## Completion Status

Code complete with live delegated Google Workspace ACL and Directory group
validation pending. Local SpiceDB validation passed for direct, nested-group,
multi-level folder, deny, and consistent-read revocation behavior.
