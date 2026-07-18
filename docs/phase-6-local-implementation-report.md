# Phase 6 ADR-015 Local Implementation Report

Date: 2026-07-15

Branch: `codex/phase-6-open-webui-integration`

## Outcome

ADR-015 work packages WP1 through WP6 are implemented and locally validated.
Phase 6 is not complete: WP4 still needs its real-Google smoke, and WP7-WP9
still require real Workspace login/onboarding, two-user live security
acceptance, operational handoff, and completion evidence.

The selected authority remains admin-approved per-user Google Drive OAuth. The
service account remains the selected-root content reader and is never used as
proof of employee visibility.

## 2026-07-17 Cutover Hardening

- Integrated the reviewed Phase 6 implementation into the active branch while
  preserving the previous uncommitted Open WebUI slice in a named Git stash.
- Made per-user ingestion independent of service-account ACL visibility while
  preserving delegated mode's existing fail-closed behavior.
- Added deterministic connection-generation provenance and made successful
  graph extraction the per-user mode's coarse global content-ready gate.
- Added `switch_drive_permission_authority`, which disables first, rotates the
  connection generation, invalidates local evidence, exactly removes all
  connection-scoped SpiceDB relationships, and activates only after cleanup.
- Added best-effort direct-tuple deletion after local OAuth disconnect denial.
- Complete backend validation passed with 416 tests, Ruff, formatting,
  migration drift, Django checks, and production/development Compose rendering.

Real Workspace credentials and the WP7/WP8 browser acceptance matrix are still
required before the deployment can be switched or Phase 6 marked complete.

## Implemented Work

### WP1 — Fail-closed foundation

- Added strict per-user OAuth startup validation, bounded pilot settings, and
  dedicated mounted-secret requirements.
- Added a versioned Fernet keyring boundary that stores refresh credentials
  only as ciphertext and never falls back to `DJANGO_SECRET_KEY` or another
  application secret.
- Added authorization, per-user visibility, and durable sync-run models with
  safe string/admin representations and additive migration `0009`.

### WP2 — Separate Drive authorization

- Added session-bound start, callback, status, reconnect, and disconnect APIs.
- Added single-use expiring OAuth state, exact issuer/audience/email/domain
  checks, narrow-scope enforcement, and rejection of broader Drive scopes.
- Added local-first disconnect and generation rotation so remote revocation
  failure cannot preserve a local grant.

### WP3 — Direct SpiceDB relationship

- Added the distinct `oauth_viewer` document relation.
- Added exact one-user/one-connection read, reconcile, delete, and causal
  verification helpers using opaque existing identifiers.
- Kept delegated reconciliation and APIs unavailable in per-user mode.

### WP4 — Indexed-ID visibility adapter

- Added a user-token adapter that accepts only an authorization primary key.
- Candidate file IDs are loaded from active indexed PostgreSQL rows and capped
  before any remote call.
- The adapter performs only bounded
  `files.get(fileId=..., supportsAllDrives=true, fields="id,trashed")` checks.
  It has no listing, export, download, or request-supplied-ID path.
- Visible, denied, and uncertain outcomes are classified fail-closed. The
  real-Google smoke remains pending.

### WP5 — Visibility synchronization

- Added durable per-authorization runs, pre-invalidation, locking, bounded
  retries, scheduled refresh, queued-run recovery, and stale-run sweeping.
- Reconciliation changes only the target user's direct relationships.
- Fresh positive PostgreSQL evidence is committed only after exact causal
  SpiceDB verification and only while both captured generations still match.
- Added an authenticated manual refresh endpoint that rejects all request
  payload identifiers.

### WP6 — Mode-aware retrieval

- Delegated and per-user authorities are mutually exclusive and are never
  combined or used as fallbacks.
- Per-user retrieval requires one exact active authorization, matching domain,
  required scopes, encrypted credential state, pilot caps, and current
  connection/authorization generations.
- It reads only direct `oauth_viewer` tuples at full consistency; it never
  evaluates the schema's combined legacy `view` permission.
- The direct resources are intersected with active globally content-eligible
  documents and fresh matching `UserDocumentVisibility` evidence.
- PostgreSQL evidence is rechecked after Neo4j returns, so expiry or generation
  changes discard all context and citations before OpenRouter is called.

## Validation Evidence

- WP5 synchronization tests: 10 passed; combined OAuth/visibility slice: 38
  passed.
- New WP6 security tests: 12 passed; focused authorization/retrieval slice: 29
  passed.
- Complete backend suite: 408 passed.
- Full Ruff check and format check: passed across 121 files.
- Django system check: passed.
- Migration drift: no changes detected.
- Production and development Compose rendering: passed.
- Live local runtime after recreating Django and Celery:
  - Django and PostgreSQL, Redis, Neo4j, and SpiceDB health: passed;
  - Open WebUI health: passed;
  - Celery worker health and live ping: passed.
- The development Compose overlay now mounts `authorization/`, preventing a
  new retrieval module from loading against stale authorization code baked
  into the image.

## Security Assumptions Preserved

- User OAuth credentials never ingest, export, or embed content.
- Only already-indexed IDs are checked for visibility.
- PostgreSQL evidence only narrows a direct SpiceDB grant; it never grants.
- Legacy delegated tuples cannot grant in per-user mode.
- Remote uncertainty, stale evidence, generation mismatch, disconnect, and
  SpiceDB failure all deny retrieval before provider context export.
- No real secrets, emails, Drive IDs, document text, provider payloads, or
  unrestricted context were added to tracked reports.

## External Blockers

- Google Workspace account review and administrator access.
- A customer-controlled Google Cloud project with two separate web OAuth
  clients and exact HTTPS callbacks.
- Admin approval for the Django client's narrow Drive metadata scope.
- Two pilot Workspace users with intentionally different effective access.
- A selected-root corpus readable by the service account.
- Real OpenRouter-through-Open-WebUI validation.

## Exact Next Work Package

WP7 starts with the safe browser onboarding boundary between the Open WebUI
Google identity and Django's separate Drive-consent session. It must expose a
controlled Drive-connect entry point, bind the verified Drive identity to the
signed Open WebUI email, and keep local password recovery disabled after a
documented operator recovery path exists. Once real credentials are available,
run WP4's Drive smoke and then the complete WP8 allowed-versus-restricted,
revocation, evidence-expiry, and SpiceDB-failure matrix.
