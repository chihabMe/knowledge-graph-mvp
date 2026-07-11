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

- Checked-in `kg/`-prefixed schema with distinct Drive roles combined into
  `view`, explicit folder parents, and recursive group subject sets.
- Deterministic connection-scoped opaque IDs; no raw emails or Drive IDs in
  logs or public responses.
- Public/anyone and domain principals remain excluded.
- Permission runs are durable, admin-only, rate-limited, and server-scoped.
- Candidate documents are ineligible until the exact tuple set is verified at
  least as fresh as the final write token and its ACL version still matches.
- Stale revocation occurs only after a complete scan; all incomplete external
  state fails closed.
- Phase 5 obtains its allowlist only through fully consistent SpiceDB
  `LookupResources` plus the active/version/verification evidence gate.

## Tasks

- [~] Design SpiceDB schema. Effort: Extra High.
- [ ] Add schema migration/load workflow. Effort: Extra High.
- [ ] Sync Drive users and groups. Effort: Extra High.
- [ ] Sync folder/document relationships. Effort: Extra High.
- [ ] Sync document permissions. Effort: Extra High.
- [ ] Implement allowed-document lookup. Effort: Extra High.
- [ ] Add permission leak tests. Effort: Extra High.

## Validation

- [ ] User A can access allowed documents.
- [ ] User B cannot access restricted documents.
- [ ] Folder inheritance works.
- [ ] Group access works.
- [ ] Permission-only changes do not require re-embedding.
- [ ] No restricted document existence is leaked through API responses.

## Completion Status

Not started.
