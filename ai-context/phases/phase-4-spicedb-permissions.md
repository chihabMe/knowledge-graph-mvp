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

## Out Of Scope

- Full answer generation.
- UI work.

## Tasks

- [ ] Design SpiceDB schema. Effort: Extra High.
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

