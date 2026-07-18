# Phase 6 Completion Plan: Admin-Approved Per-User Drive OAuth

Prepared: 2026-07-14
Status: Implementation in progress; WP1-WP3 locally complete
Canonical decision: ADR-015 in `ai-context/04-decisions.md`
Replaces as POC default: domain-wide-delegated ACL and Directory group sync

## 1. Goal

Complete Phase 6 without requiring domain-wide delegation. Keep the per-client
service account for content ingestion from the selected folder or Shared Drive,
and use each pilot employee's Google OAuth authorization to prove which already
indexed documents that employee can currently access.

Phase 6 is complete only when two real Workspace users with different Drive
visibility ask the same question through Open WebUI and:

- the allowed user receives an OpenRouter answer with only permitted citations;
- the restricted user receives no hidden fact, context, or citation;
- removing access or disconnecting OAuth causes retrieval to fail closed within
  the documented freshness window;
- no token, email, Drive ID, question, context, or document content appears in
  integration logs.

## 2. Terminology

"Pre-authorized OAuth" in this plan means **admin-approved per-user OAuth**:

1. The Workspace administrator configures the Django Drive OAuth client in API
   Controls for the pilot users or organizational unit and approves only the
   required Google data.
2. Each pilot user still signs in and grants consent once.
3. The resulting user credential has only that user's effective Drive access.

Admin approval is not domain-wide delegation, does not impersonate employees,
and does not grant file access before the user authorizes the application.

## 3. Current Baseline

Already complete:

- service-account content ingestion from a selected Drive root;
- provenance-rich Neo4j graph writes;
- SpiceDB-before-Neo4j retrieval and fresh-evidence gating;
- server-owned citations and OpenRouter answer synthesis;
- Open WebUI 0.10.2 single-model connection;
- service bearer authentication and signed Open WebUI identity JWT;
- bounded non-streaming and buffered streaming chat compatibility;
- local synthetic-data Open WebUI acceptance.

Existing but no longer the POC default:

- delegated Workspace subject configuration;
- full Drive ACL snapshots;
- Directory API group and nested-group resolution;
- folder/role tuple reconciliation.

These legacy capabilities remain until the new mode passes cutover and rollback
tests. They must not be unioned with per-user grants.

## 4. Target Architecture

```text
Content authority
-----------------
Admin shares selected folder/Shared Drive with client service account
  -> service account lists and exports content
  -> PostgreSQL SourceDocument + content state
  -> Neo4j graph/chunks with provenance

Identity authority
------------------
Employee signs into Open WebUI with Google identity scopes
  -> Open WebUI sends short-lived signed identity JWT to Django
  -> Django verifies service key + JWT + normalized email

Drive visibility authority
--------------------------
Workspace admin approves the separate Django Drive OAuth client
  -> employee completes Django Drive consent once
  -> Django verifies Google identity and stores encrypted refresh credential
  -> Celery checks only indexed Drive file IDs as that employee
  -> fresh per-user visibility evidence in PostgreSQL
  -> direct verified user/document relationships in SpiceDB

Question path
-------------
Verified Open WebUI email
  -> active matching Drive authorization
  -> fully consistent SpiceDB document lookup
  -> intersect with fresh per-user PostgreSQL evidence
  -> permission/provenance-filtered Neo4j retrieval
  -> bounded context to OpenRouter
  -> server-owned citations to Open WebUI
```

The Drive API is never called synchronously from the chat request. Chat remains
available only from the most recently verified, unexpired visibility snapshot.

## 5. Non-Negotiable Security Rules

1. Open WebUI login and Django Drive consent are separate trust boundaries.
2. The normalized Google email from Django's OAuth callback must later equal
   the independently signed Open WebUI email exactly.
3. The Google ID token must have the expected issuer and audience, a verified
   email, valid times, and an allowed Workspace domain.
4. An `hd` authorize hint is never authorization evidence.
5. OAuth state is random, short-lived, session-bound, and single use.
6. The Drive client requests only `openid`, `email`, and
   `https://www.googleapis.com/auth/drive.metadata.readonly`.
7. The worker calls `files.get` only for active file IDs already in
   `SourceDocument`; it never uses user input as a file ID and never calls
   `files.list` across the user's Drive.
8. User OAuth credentials are not used for content export or graph ingestion.
9. Refresh tokens are encrypted at rest with a dedicated rotatable deployment
   key. Plaintext exists only in process memory for the minimum required time.
10. Tokens never enter logs, exceptions, API responses, admin displays, Celery
    arguments, Redis, Neo4j, SpiceDB, or Open WebUI.
11. Celery tasks receive only authorization/run primary keys.
12. A visibility run pre-invalidates the user's candidate evidence before any
    remote check. Unknown or partial results cannot preserve a fresh grant.
13. SpiceDB remains mandatory. PostgreSQL evidence narrows a SpiceDB result and
    can deny it, but can never grant by itself.
14. A SpiceDB tuple without matching fresh per-user evidence is denied.
15. No matching OAuth authorization, missing required scopes, token refresh
    failure, identity mismatch, stale evidence, Drive uncertainty, or SpiceDB
    failure returns no retrieval context.
16. Switching permission modes never unions legacy and per-user relationships.

## 6. Google And Workspace Configuration

Use a customer-controlled Google Cloud project for the pilot when possible.
Create two web OAuth clients in that project:

- **Open WebUI login client:** `openid email profile` only, callbacks
  `https://<open-webui-host>/oauth/google/callback` and
  `https://<api-host>/api/session/google/callback`. The second callback creates
  a Django session but stores no Google token.
- **Django Drive visibility client:** `openid email` plus
  `drive.metadata.readonly`, callback
  `https://<api-host>/api/drive/oauth/callback`.

Keeping separate clients prevents the chat UI from receiving the restricted
Drive scope or becoming a token broker. Each callback URI must match exactly.

Workspace administrator steps:

1. Open Security -> Access and data control -> API Controls.
2. Add the Django Drive OAuth client ID to Manage App Access.
3. Select the narrowest available access policy, preferably Specific Google
   data, for only the required Drive metadata scope.
4. Apply it first to the pilot organizational unit or selected users.
5. Record whether the app is customer-internal or an explicitly configured
   third-party app.
6. Confirm the client's policies allow the restricted Drive metadata scope.
7. Do not enable domain-wide delegation or Directory API scopes for this mode.

`drive.metadata.readonly` is a restricted Google scope. Admin configuration can
make a customer pilot possible without treating this as a public application,
but public distribution requires a separate Google verification and security-
assessment decision. This plan does not claim a universal exemption.

Official references to recheck during implementation:

- <https://support.google.com/a/answer/7281227>
- <https://developers.google.com/workspace/drive/api/guides/api-specific-auth>
- <https://developers.google.com/identity/protocols/oauth2/web-server>
- <https://developers.google.com/identity/protocols/oauth2/resources/best-practices>
- <https://developers.google.com/workspace/drive/api/reference/rest/v3/files/get>

## 7. Configuration Contract

Add non-secret operator settings with fail-closed validation:

- `GOOGLE_PERMISSION_AUTHORITY=per_user_oauth|delegated_acl`
- `GOOGLE_USER_OAUTH_CLIENT_ID`
- `GOOGLE_USER_OAUTH_CLIENT_SECRET_FILE`
- `GOOGLE_USER_OAUTH_REDIRECT_URI`
- `GOOGLE_USER_OAUTH_ALLOWED_DOMAIN`
- `GOOGLE_USER_TOKEN_ENCRYPTION_KEY_FILE`
- `GOOGLE_USER_VISIBILITY_SYNC_INTERVAL_SECONDS`
- `GOOGLE_USER_VISIBILITY_MAX_AGE_SECONDS`
- `GOOGLE_USER_VISIBILITY_MAX_USERS`
- `GOOGLE_USER_VISIBILITY_MAX_DOCUMENTS`
- `GOOGLE_USER_VISIBILITY_BATCH_SIZE`

Rules:

- Per-user mode refuses startup when the OAuth client, callback, allowed domain,
  encryption key, or freshness limits are missing or unsafe.
- The encryption key is independent from `DJANGO_SECRET_KEY`,
  `WEBUI_SECRET_KEY`, the Open WebUI service key, and the signed-identity key.
- Client secrets and encryption keys are mounted files, never values committed
  to `.env.example`.
- Visibility maximum age is longer than the scheduled refresh interval and has
  a hard upper bound appropriate for the pilot.
- Configured user/document caps must make the worst-case API call volume clear
  before onboarding begins.

## 8. Data Model Plan

### `DriveConnection`

Add:

- `permission_authority`: `per_user_oauth` or `delegated_acl`;
- `authorization_generation`: an opaque generation changed whenever root,
  account authority, or permission mode changes;
- optional cutover timestamp and controlled status fields.

Changing the root or authority starts a transaction that invalidates every
affected per-user evidence row before the new configuration can grant access.

### `GoogleDriveAuthorization`

One row per connection and Google subject:

- connection foreign key;
- Google issuer and stable subject;
- normalized verified email and Workspace domain;
- encrypted refresh credential and encryption-key version;
- exact granted-scope set;
- authorization generation;
- status: active, scope_missing, refresh_failed, revoked, disconnected;
- connected, last-refreshed, last-successful-visibility-sync, and disconnected
  timestamps.

Do not store access tokens persistently or store full Google credential JSON.
The model string/admin representation exposes status only.

### `UserDocumentVisibility`

One row per authorization and `SourceDocument`:

- authorization and document foreign keys;
- authorization/root generation;
- state: verified_visible or denied/unknown;
- checked timestamp;
- visibility version/sync marker;
- SpiceDB revision and verification timestamp;
- controlled reason code only.

Only `verified_visible` rows with a current generation and unexpired verified
timestamp can narrow a SpiceDB result into the retrieval allowlist.

### `UserVisibilitySyncRun`

Durable audit state:

- authorization and connection foreign keys;
- queued/running/succeeded/partial/failed status;
- documents considered, verified visible, denied, unknown, relationships
  touched/deleted, and controlled error code;
- start/finish timestamps;
- no email, Drive ID, token, remote payload, or exception text in status APIs.

### Existing `SourceDocument` evidence

In per-user mode:

- `active_in_scope` and `retrieval_eligible` remain coarse global content/scope
  deny gates, not proof that any user may read the document;
- global eligibility is restored only when supported content and required graph
  provenance are ready. `permission_metadata_incomplete`, unsupported copied
  ACLs, and unresolved Directory groups stop being global exclusion reasons in
  this mode;
- cutover includes a bounded migration/resync for documents excluded only by
  legacy ACL-read failures. It must not require one user's grant to make a
  document globally content-ready;
- global `spicedb_verified_at` and `spicedb_permissions_version` from delegated
  mode must not grant per-user access;
- `source_permissions_version` remains a non-empty provenance generation tied
  deterministically to the connection, selected-root generation, permission
  authority, and file ID. Regular user visibility changes do not change it or
  trigger re-embedding; actual user freshness lives only in
  `UserDocumentVisibility`;
- mode-aware query helpers must make it impossible to accidentally use the old
  global evidence path in per-user mode.

## 9. SpiceDB Plan

Add a distinct direct relation, for example:

```zed
definition kgm/document {
    relation oauth_viewer: kgm/user
    # Existing delegated relations remain for legacy mode.
    permission view = oauth_viewer + reader + commenter + writer +
                      file_organizer + organizer + owner + parent->view
}
```

The exact schema expression must remain valid Authzed syntax and be covered by
schema lifecycle tests. In active per-user mode:

- only `oauth_viewer` relationships are managed for authorization;
- folder and group relations are not required to grant access;
- object IDs continue to use the existing connection-scoped opaque hashes;
- reconciliation reads and mutates only the current user's managed tuples;
- a mode switch deletes or namespaces old relationships before enabling the new
  authority;
- exact post-write verification records the final ZedToken on matching fresh
  visibility rows.

Do not reuse `reader` for OAuth visibility: that would falsely claim Google
reported a specific Drive ACL role.

## 10. Django OAuth And User Onboarding

Planned endpoints:

- `GET /api/drive/oauth/start`
- `GET /api/drive/oauth/callback`
- `GET /api/drive/oauth/status`
- `POST /api/drive/oauth/disconnect`
- `POST /api/drive/visibility/sync`

The start endpoint creates single-use state in a short-lived secure Django
session and redirects to Google with offline access. The callback:

1. consumes and verifies state before exchanging the code;
2. validates the ID token issuer, audience, times, subject, verified email, and
   allowed Workspace domain;
3. verifies that every required scope was actually granted;
4. requires a refresh token on first connection and never overwrites a valid
   stored refresh token with an empty response;
5. encrypts the refresh credential before database persistence;
6. creates or rotates the authorization generation;
7. invalidates previous visibility evidence for a changed account;
8. queues the first visibility sync by authorization primary key;
9. returns only a minimal success/status page.

This small Django page is an integration screen, not a replacement chat
frontend. Open WebUI can expose its URL in the model description or deployment
welcome text. A user who connects a different Google account receives no grants
when the signed Open WebUI email does not match it.

Disconnect behavior:

1. mark the authorization disconnected and invalidate its evidence locally;
2. remove its managed SpiceDB relationships and verify removal;
3. delete the encrypted refresh credential;
4. attempt Google's token-revocation endpoint;
5. return success even if remote revocation is unavailable, because local denial
   has already completed.

## 11. Visibility Synchronization Service

Create a service boundary separate from the existing delegated ACL synchronizer.
The Celery task receives only a `UserVisibilitySyncRun` primary key.

For one authorization:

1. Acquire a per-authorization distributed lock.
2. Confirm connection mode, generation, token status, required scopes, and
   selected root.
3. Load the active indexed `SourceDocument` rows from PostgreSQL; never accept
   candidate IDs from the task payload or API request.
4. Enforce configured document/user caps before remote calls.
5. Pre-invalidate candidate visibility evidence for this run.
6. Decrypt and refresh the Google credential in memory.
7. For each candidate call `files.get(fileId=..., supportsAllDrives=true,
   fields="id,trashed")` using bounded batches and retry policy.
8. Treat a successful matching non-trashed file as visible.
9. Treat inaccessible, trashed, missing, malformed, rate-limited-after-retries,
   or otherwise uncertain results as no grant for that document.
10. Build the desired direct `oauth_viewer` tuple set for this user.
11. Reconcile only that user's current managed tuples.
12. Verify the exact result at least as fresh as the final write.
13. Commit fresh positive evidence with the final revision in one database
    transaction guarded by the connection and authorization generations.
14. Leave every unknown/failed candidate stale or denied.
15. Store only controlled counts and error categories on the run.

Scheduled behavior:

- refresh connected users before evidence expiry;
- re-dispatch safe queued runs and sweep stale running jobs;
- use bounded concurrency, exponential backoff, and Drive quota awareness;
- never keep an old grant fresh merely because a refresh failed;
- refresh visibility without exporting content or re-running embeddings.

## 12. Retrieval Changes

Make `allowed_source_document_ids()` explicitly mode-aware.

Per-user mode order:

1. Normalize the already verified Open WebUI email.
2. Find one active `GoogleDriveAuthorization` for that connection and exact
   email/domain.
3. Reject missing, duplicate, disconnected, scope-deficient, stale, or wrong-
   generation authorization state.
4. Perform a fully consistent SpiceDB document lookup for the existing opaque
   user object ID.
5. Intersect those resources with active globally eligible documents and
   fresh, matching `UserDocumentVisibility` evidence.
6. Return only the intersection.

Do not call Google or attempt token refresh inside the request. If evidence is
stale, return no context and queue a refresh through a deduplicated background
path. The existing Neo4j retrieval, bounded context, OpenRouter call, refusal,
and citation construction remain unchanged.

The UI may show a controlled "Connect or refresh Google Drive access" message
when the user's own authorization is absent or stale. That message must not
name any document or reveal whether restricted evidence exists.

## 13. Cutover And Rollback

The two permission authorities are mutually exclusive per `DriveConnection`.

Cutover to per-user OAuth:

1. Apply additive database and SpiceDB migrations.
2. Keep `delegated_acl` active while automated tests run.
3. Configure the customer OAuth client, encryption key, allowed domain, caps,
   and callbacks.
4. Connect the two acceptance users and run visibility snapshots.
5. Put the connection into a controlled maintenance/deny state.
6. Invalidate global delegated evidence and remove legacy managed grant tuples.
7. Switch `permission_authority` and rotate `authorization_generation`.
8. Re-run both user snapshots and verify exact direct tuples.
9. Enable chat only after fresh evidence exists.

Rollback:

- deny first;
- invalidate all per-user evidence and remove direct tuples;
- explicitly switch the authority and rotate generation;
- perform a complete delegated reconciliation before any legacy grant returns.

Never fall back automatically from per-user OAuth to delegated ACLs or combine
both sets after an error.

## 14. Work Packages

### WP0 — Documentation and contract

- Accept ADR-015 and update the canonical brief, security rules, architecture,
  backlog, Phase 6 tracker, and operator plan.
- Pin scope, callback, admin-policy, freshness, and size-limit assumptions.

Exit: no source document describes domain-wide delegation as the POC blocker.

### WP1 — Settings and encrypted credential foundation

- Add fail-closed settings validation and mounted secret files.
- Add encryption/decryption service with key versioning and no plaintext
  representation.
- Add data models and migrations.

Exit: tokens can be persisted only as ciphertext and unsafe startup fails.

Status: Complete locally (2026-07-15). The dedicated versioned Fernet keyring,
fail-closed setting and pilot-bound validation, additive models/migration, safe
representations, direct dependency lock, and read-only Compose secret mounts
passed 23 focused tests, Ruff, migration drift, Django, and Compose rendering.

### WP2 — OAuth authorization-code flow

- Add state/session handling, Google callback verification, scope checks,
  account/domain binding, status, reconnect, and disconnect.
- Add minimal integration templates and throttling.

Exit: a pilot user can connect once, but no document grant exists yet.

Status: Complete and live-validated (2026-07-18). Authenticated Django-session endpoints
now create hashed, short-lived, single-use state; request offline consent;
verify the Google issuer, audience, subject, verified email, hosted domain, and
exact session email; require the narrow Drive metadata scope; reject broader
Drive scopes; use PKCE for the authorization-code exchange; encrypt first-use refresh credentials; preserve only decryptable
ciphertext on reconnect; rotate authorization generations and evidence; and
deny locally before best-effort remote revocation. The minimal response page
and status API expose no identity, scope, file, or credential material. The
19-test OAuth/API slice and complete 367-test backend suite passed with Ruff,
migration drift, Django, and whitespace validation. No document grant is
created by this work package.

### WP3 — Direct SpiceDB relationship

- Extend the schema with `oauth_viewer`.
- Add user-scoped tuple read/reconcile/delete helpers and exact verification.

Exit: direct test tuples grant only the intended user/document pair.

Status: Complete locally (2026-07-15). The additive `oauth_viewer` relation is
managed through one-user/one-connection server-side filters, opaque existing
document IDs, bounded exact reconciliation, causal post-write reads, and exact
delete verification. The helper rechecks the current database authority and
generation before SpiceDB access; delegated scheduling, APIs, and reconciliation
refuse per-user mode. Eleven focused tests and the complete 378-test backend
suite passed. Official Authzed `zed validate` v1.1.1 (verified release checksum)
loaded two direct relationships and passed four allowed-versus-other-user
schema assertions.

### WP4 — Visibility check adapter

- Add a Drive metadata adapter that accepts only server-selected indexed IDs.
- Implement Shared Drive support, result classification, bounds, and retries.

Exit: fakes and live smoke tests prove allowed/denied/unknown classification.

Status: Implementation and real-Google smoke complete (2026-07-18). The
adapter accepts only an authorization primary
key, reloads its current authority/generation/scopes, selects active indexed
documents from PostgreSQL, enforces the configured document cap before remote
calls, refreshes only the encrypted user credential in memory, and exposes only
`files.get(fileId=..., supportsAllDrives=true, fields="id,trashed")`. It has no
list, export, download, or request-supplied-ID path. Bounded retries classify
visible, denied, and every malformed/rate-limited/uncertain outcome fail-closed.
Six focused tests and the complete backend suite pass. Both authorized pilot
accounts checked the same three indexed IDs and each produced exactly two
verified-visible and one denied result.

### WP5 — Visibility synchronization and scheduling

- Add durable runs, locking, pre-invalidation, reconciliation, verification,
  evidence commits, Beat schedule, retries, and stale-run sweep.

Exit: access removal and remote uncertainty both remove fresh retrieval evidence.

Status: Complete and live-validated (2026-07-18); revocation and expiry remain
part of WP8 acceptance. Durable per-authorization runs pre-invalidate
positive evidence before Drive checks, select only active indexed IDs, enforce
the pilot caps, reconcile one user's exact direct tuples, verify causally, and
commit fresh positive evidence only while both generations still match. Celery
adds one-user locking, bounded retries, scheduled refresh, queued-run recovery,
and stale-run invalidation. The authenticated manual endpoint rejects every
request-supplied identifier. Ten focused synchronization tests, 38 combined
OAuth/visibility tests, and the complete 395-test backend suite passed.

### WP6 — Mode-aware retrieval

- Replace the per-user path's global document evidence gate with fresh
  `UserDocumentVisibility` intersection after fully consistent SpiceDB lookup.
- Preserve the existing `answer_query()` and Neo4j provenance boundary.

Exit: old delegated tuples, stale per-user tuples, or PostgreSQL rows alone
cannot grant access.

Status: Complete locally (2026-07-15). Retrieval selects only the globally
configured authority and never combines connection modes. Per-user mode binds
one exact active authorization to the signed email/domain, scopes, credential,
user cap, and current generations; reads only direct `oauth_viewer` tuples at
full consistency; and intersects them with active, globally content-eligible
documents and fresh matching positive evidence. The PostgreSQL evidence gate
runs again after Neo4j, so expiry or generation changes discard context before
the answer provider. Twelve new focused tests, the complete 408-test backend
suite, Ruff, formatting, Django, migration drift, and Compose rendering passed.

### WP6A — Content readiness and authority cutover

- Allow selected-root content ingestion in per-user mode without requiring the
  service account to read file ACLs.
- Use a deterministic connection/root-authority generation for source
  provenance and require successful graph extraction before the coarse global
  content-ready gate becomes true.
- Add a deny-first operator switch that rotates generations, invalidates local
  evidence, exactly deletes every connection-scoped SpiceDB relationship, and
  activates the target authority only after cleanup succeeds.

Exit: an ACL-read failure cannot block per-user content ingestion, and no
cutover failure can leave a mixed or partially active permission authority.

Status: Complete locally (2026-07-17). Delegated mode retains its ACL-read
fail-closed behavior. Per-user mode stores unreadable ACL snapshots only as
diagnostics, generates non-empty scope provenance, and waits for graph
extraction. `switch_drive_permission_authority` performs the controlled
operator cutover; disconnect also attempts direct-tuple cleanup after immediate
local denial. Focused ingestion, extraction, cutover, and disconnect tests pass.

### WP7 — Open WebUI Google login and onboarding

- Configure the real Open WebUI Google client and exact callback.
- Use the identity-only Google OIDC session bootstrap before the separate Drive
  authorization flow; verify one-time state, nonce, PKCE, issuer, audience,
  verified email, and Workspace hosted domain without persisting login tokens.
- Disable local signup/password login after recovery is documented.
- Expose the Django Drive-connect URL without adding a custom chat frontend.
- Verify signed Open WebUI email equals the connected Drive identity.

Exit: a real Workspace login reaches Django and is bound to its own Drive
authorization.

Status: Django session bootstrap and separate Drive onboarding are
live-validated for both pilot users (2026-07-18), with 441 backend tests. The
remaining WP7 gate is real Google login through Open WebUI and verification
that its signed identity JWT matches the already connected Drive identity.

### WP8 — Live two-user and revocation acceptance

- Ingest one selected-root corpus with the service account.
- Connect an allowed and restricted Workspace user.
- Ask the same question through Open WebUI with production OpenRouter enabled.
- Test direct, folder-inherited, group, nested-group, and Shared Drive access.
- Remove access, revoke/disconnect OAuth, expire evidence, and simulate SpiceDB
  failure.
- Inspect sanitized logs.

Exit: every Phase 6 validation item has real UI evidence and no leak.

Status: Backend two-user matrix partially complete (2026-07-18). User 1's
final allowlist contains only `User 1 private document` and `Visible to both
users`; user 2's contains only `User 2 private document` and `Visible to both
users`. Each user's evidence explicitly denies the other private document.
Actual Open WebUI questions/citations, access removal, disconnect/revocation,
evidence expiry, provider routing, and SpiceDB-unavailable behavior remain.

### WP9 — Handoff and completion

- Run focused and complete tests, Ruff, Django runtime/deploy checks, migrations,
  SpiceDB schema checks, and every Compose rendering.
- Document admin approval, user connect/disconnect, token rotation, quota limits,
  recovery, and rollback.
- Update canonical status and tracker only after live evidence passes.

Exit: Phase 6 can be marked complete without a delegated Workspace credential.

## 15. Required Automated Tests

OAuth and token tests:

- state missing, incorrect, expired, and replayed;
- authorization code exchange failure;
- wrong issuer/audience, expired ID token, unverified email, wrong domain;
- missing Drive scope or refresh token;
- reconnect preserves an existing refresh token when Google omits a new one;
- ciphertext never contains the token and wrong encryption key fails closed;
- status/admin/API/log serialization never includes credential material;
- disconnect invalidates locally even when Google revocation fails.

Visibility tests:

- only active indexed IDs are requested;
- request-supplied IDs cannot widen the candidate set;
- no `files.list`, export, download, or unrestricted corpus call occurs;
- direct, inherited, group, nested-group, and Shared Drive effective access;
- trashed, inaccessible, malformed, quota-exhausted, and transient-failure
  results deny;
- pre-invalidation prevents an interrupted run from leaving fresh evidence;
- generation changes prevent stale job commits;
- one user's reconciliation never changes another user's tuples;
- exact tuple verification is mandatory before evidence becomes fresh;
- content and embeddings are untouched by visibility-only changes.

Retrieval/leak tests:

- signed Open WebUI email and Drive OAuth email must match;
- no connected authorization returns no context;
- stale/unknown/disconnected/revoked authorization returns no context;
- fresh PostgreSQL evidence without a SpiceDB tuple cannot grant;
- a SpiceDB tuple without fresh matching PostgreSQL evidence cannot grant;
- legacy delegated tuples cannot grant in per-user mode;
- mode/root/account generation changes deny immediately;
- allowed User A receives the fact and permitted citation;
- restricted User B receives no fact, graph path, embedding evidence, or citation;
- OpenRouter is never called before every new gate passes;
- streaming waits for the complete permission-safe answer as it does today.

## 16. Live Acceptance Matrix

Prepare at least two users and these indexed documents:

| Case | User A | User B | Expected |
| --- | --- | --- | --- |
| Direct share | visible | hidden | A answers; B refuses |
| Folder inheritance | visible | hidden | A answers; B refuses |
| Google Group | member | non-member | A answers; B refuses |
| Nested group | effective member | non-member | A answers; B refuses |
| Shared Drive | member | non-member | A answers; B refuses |
| Access removed | formerly visible | hidden | both refuse after refresh |
| OAuth disconnected | disconnected | connected | A refuses; B follows own access |
| Evidence expired | stale | fresh | A refuses; B follows own access |
| SpiceDB unavailable | any | any | both refuse; no provider call |

For each case record only controlled user labels, expected result, answer/refusal,
citation count, and pass/fail. Do not place real emails, Drive IDs, questions,
document text, tokens, or provider payloads in logs or committed reports.

## 17. External Inputs And Blockers

Required before live acceptance:

- customer-controlled Google Cloud project or an explicitly approved pilot app;
- two web OAuth clients and exact HTTPS callbacks;
- Workspace admin access to API Controls;
- decision on pilot organizational unit/users and allowed domain;
- two Workspace users with intentionally different file visibility;
- a selected folder/Shared Drive shared with the service account;
- configured OpenRouter key/model and network access;
- documented token-encryption-key custody and rotation owner;
- agreed pilot caps for connected users and indexed documents.

The plan can be implemented and unit-tested without these values, but Phase 6
cannot be declared complete without them.

## 18. Out Of Scope

- Public multi-tenant OAuth application rollout.
- Automatic fallback to domain-wide delegation.
- Browsing or ingesting each employee's entire Drive.
- Using user tokens for content export.
- Building a custom chat frontend.
- Replacing SpiceDB with PostgreSQL checks.
- Keeping unrestricted access alive when OAuth or permission evidence is stale.
- Deleting the legacy delegated implementation before cutover is proven.

## 19. Recommended Implementation Order

1. WP1 settings, encryption, models, and migrations.
2. WP2 OAuth connect/status/disconnect flow.
3. WP3 additive SpiceDB relation and helpers.
4. WP4 indexed-ID visibility adapter.
5. WP5 synchronization, scheduling, and freshness evidence.
6. WP6 retrieval integration and leak tests.
7. WP7 real Open WebUI Google login and onboarding.
8. WP8 live acceptance and revocation testing.
9. WP9 operations documentation and tracker completion.

Do not begin with live UI wiring. Prove encrypted credentials, user isolation,
generation invalidation, exact SpiceDB reconciliation, and fail-closed retrieval
through automated tests before connecting real pilot users.

## 20. New Session Bootstrap Prompt

Copy this prompt into a new Codex session to resume implementation without the
conversation history:

```text
Continue Phase 6 implementation in this repository:
/mnt/c/Users/lenovo/Documents/projects/knowledge-graph-mvp

The active branch should be codex/phase-6-open-webui-integration. Before making
changes, inspect the current branch and git status and preserve every existing
tracked or untracked change. Do not reset, discard, overwrite, stage, or commit
existing work. Do not commit anything unless I explicitly ask in this new
session.

Read AGENTS.md completely and follow it. Then read these files before coding:
- AGENT_PROJECT_BRIEF.md
- ai-context/00-project-overview.md
- ai-context/03-implementation-rules.md
- ai-context/04-decisions.md, especially ADR-015
- ai-context/05-test-and-acceptance.md
- ai-context/07-ai-coding-security-rules.md
- ai-context/phases/phase-6-open-webui-integration.md
- docs/phase-6-pre-authorized-oauth-completion-plan.md

The active source of truth for the remaining work is
docs/phase-6-pre-authorized-oauth-completion-plan.md. The older
docs/phase-6-implementation-plan.md is the implementation record for the
already-completed Open WebUI adapter, not the active permission plan.

Objective: fully complete Phase 6 using ADR-015 admin-approved per-user Google
Drive OAuth. Keep the service account only for content ingestion from the
admin-selected root. Use a separate Django authorization-code flow for each
pilot user's Drive metadata access. Check only already-indexed Drive file IDs,
store refresh credentials encrypted, reconcile a distinct direct per-user
SpiceDB relationship, require matching fresh PostgreSQL visibility evidence,
and keep retrieval fail-closed. Never enumerate a user's whole Drive, use user
tokens for content ingestion, union or automatically fall back to delegated
ACL grants, or send unrestricted context to OpenRouter.

Start with WP1 from the active plan: fail-closed settings, dedicated encryption
key handling, data models, migrations, admin-safe representations, and focused
tests. Inspect the existing models, settings, authorization services, and tests
before designing changes. Implement the smallest safe vertical slices in plan
order, validate each slice, and continue while useful work remains. Do not stop
after producing another plan. Use placeholders/fakes for external Google values
until real credentials are required; never print or expose .env secret values.

Run validation proportional to each change, including focused tests, then the
complete backend suite, Ruff, Django checks, migrations, SpiceDB schema checks,
and Compose rendering when those areas are touched. Do not run repository audit
commands unless I explicitly request an audit. Update the phase tracker and
daily report only with work actually completed. Do not mark Phase 6 complete
until real Google login, separate Drive consent, OpenRouter through Open WebUI,
and allowed-versus-restricted two-user live acceptance—including revocation,
expiry, and SpiceDB-failure cases—have passed.

At the end, report what was implemented, files changed, validation results,
security assumptions, external credential blockers, and the exact next work
package. Do not commit unless I explicitly tell you to.
```
