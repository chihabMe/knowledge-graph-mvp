# Phase 6 Per-User OAuth Cutover Work Report

Date: 2026-07-17
Branch: `codex/phase-6-open-webui-integration`

## Executive Summary

The reviewed Phase 6 implementation was integrated into the active branch and
the main gaps blocking the transition from delegated ACL synchronization to
admin-approved per-user Google Drive OAuth were addressed.

The system now has a mode-aware content path that does not require the service
account to read employee ACL metadata when `per_user_oauth` is active. It also
has a controlled, deny-first operator command for switching permission
authorities without combining legacy ACL grants and per-user OAuth grants.

This work is locally complete and validated. The deployment has not been
switched to per-user OAuth because real customer OAuth credentials, Workspace
users, and live acceptance evidence are still required.

## Repository Integration

- Fast-forwarded the active Phase 6 branch to the complete `review/phase-6`
  history.
- Integrated the Open WebUI adapter, signed identity boundary, separate Drive
  OAuth flow, encrypted refresh-token storage, indexed-file visibility checks,
  direct SpiceDB relationships, visibility synchronization, and mode-aware
  retrieval already present in the reviewed work.
- Preserved the previous uncommitted Open WebUI slice in the named Git stash
  `stash@{0}` before integration.
- Left the unrelated untracked `.claude/` directory untouched.
- No commit or push was created during this work session.

## Mode-Aware Google Drive Ingestion

Previously, a file was excluded whenever the service account could read its
content but received an error from `permissions.list()`. That behavior is still
required for the legacy `delegated_acl` mode, where copied ACL metadata is the
permission authority, but it incorrectly blocked the new per-user OAuth design.

The ingestion path now behaves as follows:

- `delegated_acl` continues to fail closed when ACL metadata is incomplete.
- `per_user_oauth` can ingest supported content from the selected root even
  when the service account cannot read the file's ACL metadata.
- Incomplete ACL snapshots remain stored as diagnostics; they do not become
  employee authorization evidence.
- A deterministic provenance generation is derived from the connection,
  authorization generation, active authority, and Drive file ID.
- Individual user-sharing changes do not change this provenance generation or
  trigger content re-embedding. User freshness remains in
  `UserDocumentVisibility`.
- Content storage alone does not make a document retrievable.
- In per-user mode, successful graph extraction is required before the coarse
  global `retrieval_eligible` content-ready gate becomes true.
- Delegated SpiceDB verification fields remain empty in per-user mode and
  cannot accidentally become a grant source.

## Controlled Permission-Authority Switch

Added the management command:

```bash
python manage.py switch_drive_permission_authority \
  <connection_id> per_user_oauth
```

The command requires the target authority to match
`GOOGLE_PERMISSION_AUTHORITY`. Its cutover sequence is deliberately
fail-closed:

1. Validate the connection and selected Drive root.
2. Disable the connection before contacting SpiceDB.
3. Rotate the connection authorization generation.
4. Mark all connection documents retrieval-ineligible.
5. Clear legacy global SpiceDB verification evidence.
6. Invalidate per-user visibility evidence and stored authorizations.
7. Mark successful graph extractions for a one-time provenance refresh.
8. Delete every managed SpiceDB relationship under the connection prefix.
9. Read the relationships back and require an exact empty result.
10. Activate the target authority and re-enable the connection only after
    cleanup succeeds.

PostgreSQL and SpiceDB cannot participate in one transaction. For that reason,
any SpiceDB failure leaves the connection disabled with local evidence denied.
The command never automatically falls back to delegated ACLs and never unions
the two permission models.

## OAuth Disconnect Hardening

OAuth disconnect already denied access locally before attempting remote Google
token revocation. It now also attempts to delete the disconnected user's direct
`oauth_viewer` SpiceDB relationships.

Local PostgreSQL denial remains authoritative. If tuple cleanup or Google
revocation is unavailable, the deleted freshness evidence and disconnected
authorization prevent stale tuples from granting retrieval. A future
connection-wide authority cutover also removes any leftover relationships.

## Documentation And Configuration

Updated the following sources to reflect the implemented behavior:

- `AGENT_PROJECT_BRIEF.md`
- `AGENTS.md`
- `README.md`
- `ai-context/04-decisions.md`
- Phase 2 and Phase 6 trackers
- Phase 6 OAuth completion plan
- Phase 6 local implementation report
- `.env.example`
- `docs/daily-reports/2026-07-17.md`

`GOOGLE_PERMISSION_AUTHORITY=delegated_acl` remains the safe example default.
The example now documents that operators must first configure the per-user
OAuth settings, restart Django and Celery with `per_user_oauth`, and then run
the controlled authority-switch command.

## Validation Evidence

The following validation passed:

- Complete backend test suite: **416 tests passed**.
- Final focused ingestion, extraction, authority-switch, and OAuth-disconnect
  suite: **25 tests passed**.
- Ruff linting: passed.
- Ruff formatting verification: passed across 124 files.
- Django migration drift: no changes detected.
- Django system check: passed.
- Infrastructure, production application, and development Compose rendering:
  passed.
- Production deployment check completed with two existing non-blocking HSTS
  warnings for `SECURE_HSTS_INCLUDE_SUBDOMAINS` and `SECURE_HSTS_PRELOAD`.

The full suite was executed in the backend image with the repository mounted at
its real layout so the deployment-contract tests could inspect the tracked
`infra/` files.

## Current Operational State

The code supports the selected per-user OAuth architecture, but the live
deployment has not been cut over.

Current limitations:

- The local `.env` does not activate `per_user_oauth`.
- A customer-controlled Google OAuth web client has not been configured.
- Real Open WebUI Google login has not been validated.
- Real Django Drive consent has not been validated.
- The indexed-ID visibility adapter has only fake/local validation.
- No live two-user allowed-versus-restricted Workspace test has been recorded.

## Remaining Work Before Activation

1. Create or select the customer-controlled Google Cloud project.
2. Configure separate OAuth clients for Open WebUI login and Django Drive
   metadata consent.
3. Have the Workspace administrator approve the Django OAuth client and the
   required `openid`, email, and `drive.metadata.readonly` scopes for the pilot
   users or organizational unit.
4. Configure the allowed domain, exact HTTPS callback, encrypted-token keyring,
   OAuth client secret, and per-user visibility limits.
5. Set `GOOGLE_PERMISSION_AUTHORITY=per_user_oauth` and restart Django, Celery
   worker, and Celery Beat.
6. Run `switch_drive_permission_authority` for the selected connection.
7. Run a service-account Drive metadata/content sync and allow graph extraction
   to complete.
8. Connect two pilot users with intentionally different Drive access.
9. Run per-user visibility synchronization.
10. Validate allowed, restricted, direct-share, inherited-folder, Google Group,
    nested-group, Shared Drive, access-removal, OAuth-disconnect, evidence-expiry,
    and SpiceDB-failure cases through the actual Open WebUI route.
11. Inspect sanitized logs and record only pass/fail evidence without real
    emails, Drive IDs, tokens, questions, or document text.

## Conclusion

The repository has moved from a design-level per-user OAuth decision to a
locally implemented and controlled cutover path. Domain-wide delegation is no
longer required for the POC content and employee-visibility architecture.

Phase 6 must remain open until the real Workspace login, Drive consent,
indexed-file checks, revocation behavior, and two-user permission-isolation
matrix pass through Open WebUI.
