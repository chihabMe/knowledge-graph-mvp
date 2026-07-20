# Phase 6: Open WebUI Integration

## Purpose

Expose the permission-safe backend through Open WebUI as the main user interface.

## Scope

- Open WebUI configuration.
- Google OAuth/OIDC configuration path.
- Admin-approved per-user Drive OAuth authorization.
- Fresh per-user Drive visibility synchronization into SpiceDB.
- Django OpenAI-compatible endpoint.
- User identity propagation.
- OpenRouter model routing.

## Out Of Scope

- Building a custom frontend.
- Replacing Open WebUI.

## Tasks

- [x] Decide Open WebUI integration pattern. Effort: High. (ADR-014 selects a
  thin Django `GET /v1/models` + `POST /v1/chat/completions` adapter over the
  existing `answer_query()` service. Open WebUI uses a separate service bearer
  key and short-lived signed identity JWT. No Pipeline/Function or separate
  Pipelines service is used for the primary retrieval path.)
- [x] Configure Open WebUI service settings. Effort: High. (Pinned Open WebUI
  0.10.2 uses the private Django `/v1` connection, one logical model, signed
  identity forwarding, and disabled direct/tool/file retrieval surfaces.)
- [x] Configure Google auth path. Effort: Extra High. (Compose and the operator
  environment contract are implemented. The identity-only Django callback and
  separate Drive callback are registered, and both real Workspace users passed
  Open WebUI login plus separate Drive consent.)
- [x] Pass authenticated user identity to backend. Effort: Extra High. (The
  service bearer and short-lived HS256 identity JWT are verified separately;
  plaintext and body identity are rejected.)
- [x] Route model calls through OpenRouter. Effort: High. (The adapter delegates
  only to the existing `answer_query()` boundary. A live signed-user adapter
  request passed through OpenRouter with exactly the two permitted citations on
  2026-07-18, and the configured DeepSeek route subsequently passed through the
  visible UI for both users.)
- [x] Test end-to-end chat flow. Effort: Extra High. (Local Open WebUI accepted
  a signed user request and returned the permission-safe synthetic Atlas answer
  with citation, including the buffered streaming path.)

### Admin-approved per-user OAuth completion

- [x] Accept the POC permission-authority decision and completion plan. Effort:
  Extra High. (ADR-015 keeps the service account for content and selects
  per-user OAuth for effective employee visibility.)
- [x] Add fail-closed per-user OAuth settings, encrypted refresh-token storage,
  authorization generations, and migrations. Effort: Extra High. (Versioned
  Fernet keyring, dedicated read-only secret mounts, additive authorization and
  per-user evidence/run models, safe representations, and migration `0009`
  passed 23 focused tests, Ruff, migration drift, Django, and Compose rendering
  on 2026-07-15.)
- [x] Add Django Drive connect, callback, status, reconnect, and disconnect
  flows with state, identity, domain, and scope verification. Effort: Extra
  High. (Authenticated session endpoints, hashed single-use state, exact Google
  claim/email/domain binding, narrow-scope enforcement, ciphertext-only token
  persistence, reconnect generation invalidation, local-first disconnect,
  throttling, and minimal no-secret responses passed 19 focused tests and the
  complete 367-test backend suite on 2026-07-15. A successful callback now
  queues the existing bounded refresh for only the authenticated user and
  reports either active synchronization or scheduled fallback. OAuth
  completion alone still creates no document grant.)
- [x] Add the distinct direct SpiceDB user/document relation and exact
  user-scoped reconciliation. Effort: Extra High. (`oauth_viewer` is filtered
  by one connection and opaque user ID, reconciles only active indexed rows,
  verifies at the causal revision, preserves other users, and blocks delegated
  sync in per-user mode. Eleven focused tests, the complete 378-test suite, and
  official Authzed schema validation with four isolation assertions passed on
  2026-07-15.)
- [x] Check only already-indexed file IDs as each connected user and persist
  fresh per-user visibility evidence. Effort: Extra High. (WP4's adapter is
  implemented and live-validated: it selects IDs only from PostgreSQL,
  uses only bounded Shared-Drive-compatible `files.get` metadata calls, and
  denies inaccessible, trashed, malformed, exhausted, or uncertain results.
  WP5's durable pre-invalidation, exact one-user reconciliation, causal
  evidence commits, scheduling, retries, and stale-run recovery are validated.
  Both pilot users completed OAuth; each live run considered three documents
  and produced exactly two verified-visible and one denied result.)
- [x] Make retrieval intersect fully consistent SpiceDB results with matching
  fresh per-user evidence; legacy tuples must not grant in this mode. Effort:
  Extra High. (The mode-aware path reads only direct `oauth_viewer` tuples,
  requires one exact current authorization and unexpired generation-matched
  evidence, preserves the delegated path without union/fallback, and rechecks
  evidence after Neo4j before context export. Twelve focused tests and the
  complete 408-test backend suite passed on 2026-07-15.)
- [x] Configure real Open WebUI Google login and bind its signed email to the
  separately authorized Drive identity. Effort: Extra High. (Both pilot users
  completed Google login and separate Drive authorization. The environment-
  authoritative single-model configuration now admits only the configured
  Workspace OAuth domain and automatically exposes only
  `client-knowledge-graph` to standard users.)
- [x] Pass live allowed/restricted, revocation, expiry, provider-route, and
  SpiceDB-failure acceptance through the actual UI. Effort: Extra High. (Both
  users received only their own private fact plus the shared source. User 1
  share removal/re-addition, User 2 disconnect/reconnect, evidence expiry,
  DeepSeek routing, and SpiceDB outage/recovery all failed closed or restored
  only the exact permitted sources on 2026-07-18.)
- [x] Live-validate that a new or reconnected Drive callback immediately
  queues and completes the user-specific refresh without waiting for the
  periodic scheduler. Effort: Medium. (User 2 refused safely immediately after
  disconnect, then reconnected and received only the expected private canary
  value, `User 2 private document`, and `Visible to both users` after the
  callback-triggered run. The 15-minute scheduler wait was not required.)
- [x] Add guided Drive onboarding from the first blocked Open WebUI chat.
  Effort: High. (ADR-018 adds a five-state readiness service and blocks the
  compatible chat path before retrieval unless fresh visibility evidence is
  ready. Connect/reconnect responses link through the existing identity
  bootstrap, the Drive callback polls authenticated status and automatically
  returns to validated `WEBUI_URL`, and only explicit Google `invalid_grant`
  wipes credentials and requires renewed consent. Implemented locally on
  2026-07-20 and deployed to the development stack; the guided first-connect
  browser matrix and User 2 disconnect/reconnect passed.)
- [x] Live-validate guided first connection and the three-user visibility
  matrix. Effort: High. (The admin received all three Google-verified sources;
  User 1 and User 2 each received only their own private source plus the shared
  source. PostgreSQL evidence and durable runs matched 3-visible/0-denied,
  2-visible/1-denied, and 2-visible/1-denied with zero unknown results. One
  HTTP-success OpenRouter response violated the strict answer schema and
  correctly failed closed; the boundary now retries that contract failure
  exactly once without relaxing validation.)
- [x] Live-validate guided disconnect and reconnect. Effort: High. (User 2's
  local disconnect wiped the encrypted credential, deleted all evidence, and
  reduced the permission-safe allowlist to zero. The next chat presented the
  guided connection flow; callback-triggered synchronization succeeded with
  two visible, one denied, and zero unknown documents before the UI again
  returned only User 2's private source and the shared source.)

Adapter implementation history: `docs/phase-6-implementation-plan.md`.

Active completion plan:
`docs/phase-6-pre-authorized-oauth-completion-plan.md`.

## Validation

- [x] User can log in with real Google OAuth/OIDC. (User 1 reached the pinned
  Open WebUI chat through the configured Google client on 2026-07-18.)
- [x] User can separately authorize Drive metadata access through Django.
- [x] Backend receives trusted signed user identity in local acceptance.
- [x] User can ask questions through the actual Open WebUI interface locally.
- [x] Backend returns permission-safe cited answers for locally authorized data.
- [x] Restricted facts remain hidden at the live backend authorization and
  fresh-evidence boundary and through the actual Open WebUI presentation.
- [x] Removing Drive access, disconnecting OAuth, or expiring evidence removes
  answer context and citations within the documented freshness bound. (All
  three cases passed through the actual UI on 2026-07-18.)

## Completion Status

Adapter code and OAuth WP1-WP6 are locally and live validated (updated
2026-07-18). The
two real OAuth clients and local
encryption/adapter secrets are installed outside the repository. The
organization's service-account-key policy remains enforced; keyless local ADC
now impersonates `knowledge-graph-ingestion`, and live Drive checks confirmed
pilot-folder discovery plus document export. Viewer-level ACL enumeration
returned Google's expected `403 insufficientFilePermissions`. The controlled
switch to `per_user_oauth` then invalidated legacy evidence and re-ingested all
three documents with content and graph extraction. The identity-only Django
Google OIDC callback and the separate PKCE-protected Drive callback passed for
both pilot users. Each user received exactly their private document and the
shared document through the final SpiceDB plus fresh-evidence allowlist, while
the other user's private document was denied. Focused tests, the complete
443-test backend suite, Ruff, formatting, migrations, Django
checks, Compose rendering, and a live local Open WebUI synthetic-data chat
passed. Real Open WebUI Google login, exact two-user cited retrieval, access
removal and restoration, OAuth disconnect and reconnect, stale-evidence
refusal, DeepSeek provider routing, and SpiceDB outage/recovery all passed
through the actual UI. The callback's bounded user-specific refresh also passed
a live disconnect/reconnect without waiting for the periodic scheduler. A
shared-only question returned the correct fact but cited an additional
permitted source, which is a relevance-quality follow-up rather than a leak.

Phase 6 is complete. The operator reviewed both untracked Phase 6 reports and
approved formal closeout on 2026-07-19, ruling the citation over-inclusion a
non-blocking relevance-quality follow-up tracked in GitHub issue #6. The full backend validation (449 tests, Ruff, migration drift) was
re-run green in Docker on the closeout date.

The 2026-07-20 ADR-018 usability follow-up does not reopen the permission-safe
Phase 6 boundary. Its automated tests, development deployment, three-user
first-connect matrix, and guided disconnect/reconnect acceptance are complete
on the isolated onboarding branch.
