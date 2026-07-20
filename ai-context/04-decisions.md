# Architecture Decisions

## ADR-001: Use Django Instead Of FastAPI For The Main Backend

Decision: Use Django + Django REST Framework for the target backend.

Reason:

- The project needs admin screens, app metadata, job records, evaluation records, and user/config management.
- Django gives a stronger foundation for a business application than a minimal API-only framework.
- Celery and PostgreSQL integration patterns are mature.

Status: Accepted.

## ADR-002: Keep Neo4j As The Knowledge Graph Database

Decision: Use Neo4j for graph data and vector retrieval.

Reason:

- The core product depends on graph relationships.
- Neo4j supports graph traversal and vector indexes in one store.
- Adding a separate vector DB in v1 increases permission-filtering complexity.

Status: Accepted.

## ADR-003: Keep SpiceDB For Authorization

Decision: Use SpiceDB for permission checks.

Reason:

- The product depends on Google Drive-like relationship permissions.
- SpiceDB is designed for relationship-based access control.
- Custom permission logic is too risky for permission-safe retrieval.

Status: Accepted.

## ADR-004: Use Docker Compose For First Deployments

Decision: Use Docker Compose on a single-customer VM.

Reason:

- The first product is implementation-led, not multi-tenant SaaS.
- Each customer gets an isolated deployment.
- Docker Compose is simpler than Kubernetes for early deployments.

Status: Accepted.

## ADR-005: Use Traefik For Routing And TLS

Decision: Use Traefik as reverse proxy.

Reason:

- User prefers Traefik.
- It handles Docker service routing well.
- It can route Open WebUI, Django, Dozzle, and Uptime Kuma cleanly.

Status: Accepted.

## ADR-006: FastAPI Prototype Is Not The Target Backend

Decision: The old FastAPI/local-file prototype is not the target implementation. Django + DRF is the canonical backend.

Reason:

- It was useful for proving the first local-file concept.
- The target stack is now Django + DRF + Celery.

Status: Accepted.

## ADR-006B: Google Drive As Primary Ingestion Source; Notion Second; Obsidian Optional

Decision: Build Google Drive ingestion first. Notion is a likely second source later. Obsidian is not a required source — it stays an optional power-user feature.

Reason:

- The client's target buyers are non-technical organizations, not the DIY/power-user crowd Obsidian setup assumes.
- Google Drive and Notion are the sources most target users already have their organizational knowledge in.

Status: Accepted (2026-06-23).

## ADR-006C: OpenRouter As The Model Gateway

Decision: Use OpenRouter for AI model access rather than calling a single model provider directly.

Reason:

- Lets the client (and their customers) swap models without code changes, including cost-efficient or zero-data-retention providers.
- Avoids hard-coding the product to one vendor while cloud-vs-local-model tradeoffs are still unsettled for this market.

Status: Accepted (2026-06-30).

## ADR-006D: Open WebUI As The Only Chat Front End For V1

Decision: Do not build a custom frontend. Open WebUI is the chat interface for the proof of concept.

Reason:

- It is off-the-shelf, supports Google OAuth/OIDC SSO, and has a Pipeline/Function slot for custom retrieval middleware.
- Building a custom UI is explicitly out of scope for the POC (see `docs/project-plan.md`, "What The POC Should Not Include") and would trade build speed for polish the client does not need yet.

Status: Accepted (2026-06-30).

## ADR-007: Treat Graphify As A Helper, Not The Core Runtime

Decision: Graphify can be evaluated as an extraction or AI-navigation helper, but it should not own the whole ingestion, permission, or retrieval architecture.

Reason:

- The hard part of this project is permission-safe Drive ingestion, provenance, re-indexing, and retrieval filtering.
- Graphify may help create or inspect graph structure, but the backend must control Drive syncing, source provenance, SpiceDB checks, and Neo4j writes.
- Keeping extraction behind an adapter lets the project compare `neo4j-graphrag`, Graphify, and Graphiti without locking the architecture too early.

Status: Accepted.

## ADR-008: Single-Tenant Deployment — One Self-Contained Compose Stack Per Client

Decision: The product ships as one docker compose stack per client. Each
deployment holds exactly one client's Postgres, Neo4j, SpiceDB, Redis, and
Open WebUI. There is no shared multi-tenant instance.

Reason:

- Isolation *is* the product promise: one client's documents, graph, and
  permission tuples never share a database or network with another client's.
  Infrastructure-level isolation is stronger than any in-app namespacing.
- It keeps each OAuth app and token store client-specific. A customer-admin-
  configured pilot may qualify for Google's admin-trusted/internal exceptions,
  while any future public multi-tenant distribution must separately complete
  the restricted-scope verification and security-assessment analysis.
- Per-deployment `.env` + mounted secrets become the intended configuration
  surface, not a shortcut.
- Cost: ops effort grows linearly with clients (upgrades, monitoring,
  backups). Phase 3+ still keys all graph/permission data by connection id so
  consolidation into a shared control plane stays possible later.

Status: Accepted (2026-07-08).

## ADR-009: Drive Access Via Per-Client Service Account, Provisioned By Us; Dynamic "Share To Connect" Folder Selection

Historical note: ADR-015 supersedes the permission-authority portion of this
decision. The service account and selected-root flow remain active for content
ingestion; the POC now uses per-user OAuth for employee visibility.

Decision: Each client deployment gets its own Google service account,
created by us in our GCP project (the pilot deployment may instead use a
service account in a client-owned project). Clients never touch GCP.
Connecting Drive = the client shares
a folder with the service account's email as Viewer — the same action as
sharing with a person. The current Drive-ingestion work must include an admin
connection/settings flow that lists folders shared with the service account
("shared with me"), lets the admin choose the ingestion root, and writes the
chosen folder/shared-drive scope into `DriveConnection`. No per-user OAuth
tokens.

Reason:

- Zero technical work for non-technical clients; revocation is equally
  non-technical (unshare the folder).
- One SA per client bounds the blast radius of a leaked key to that client.
  A single global SA for all clients was rejected for exactly this reason.
- Per-user OAuth is not the default: tokens die with the employee who granted
  them, grant broader access than the picked folder, and public-app
  verification is expensive.
- This resolves the previously open "domain-wide delegation vs. per-user
  OAuth" question. Delegation remains the documented fallback for Workspace
  domains that block external sharing or restrict permission-list reads.

**ACL visibility under folder-level sharing — resolved 2026-07-08 by live
test.** Sharing a folder with the service account (tested at Editor role
against a temporary folder in a personal Google account) lets the service
account list and read files inside it, but
`permissions.list()` on those files returns `403 insufficientFilePermissions`
— folder-level sharing does not grant "manage permissions" rights on the
files inside it. This is a Drive API access-control property, not specific
to personal vs. Workspace accounts, so it is expected to reproduce for a
real pilot folder too. Practical effect: under the default
"share to connect" model, per-file permission metadata will generally be
unreadable, and Phase 2 now fails those documents closed
(`exclusion_reason = permission_metadata_incomplete`, `retrieval_eligible =
False`) instead of crashing the sync or guessing. **Historical conclusion,
superseded by ADR-015:** domain-wide delegation was then selected as the
expected path to obtain full per-file ACL metadata. The POC now avoids that
requirement by checking already-indexed IDs as each authorized user.

Rule for root changes: changing the root folder/shared drive is a re-scope
operation — documents outside the new root must lose retrieval eligibility and
their graph/SpiceDB footprint, otherwise switching roots silently widens what
is answerable.

Status: Partially superseded by ADR-015 (2026-07-14). The per-client service
account and dynamic share-to-connect root remain accepted for content
ingestion. Domain-wide delegation and copied ACL/group relationships are no
longer the default POC employee-authorization path; they remain an optional
legacy/future mode. Originally accepted (2026-07-08). Updated 2026-07-08: dynamic folder/shared-drive
selection is no longer deferred; it is the next Phase 2 product path before
asking the client to provide manual root IDs. Updated 2026-07-08 (live
validation): ACL-visibility question resolved — see above.

## ADR-010: neo4j-graphrag As The Extraction Engine; Per-Document Entity Scoping

Decision: Use the official `neo4j-graphrag` Python package (Apache-2.0,
maintained by Neo4j) as the LLM extraction engine, wrapped behind the
`ExtractionAdapter` boundary in `graph/extraction.py`. Only its extraction
components are used — chunk/entity/relationship writing stays in our own
fail-closed writers (`graph/writer.py`), and its entity-resolution component
is deliberately **not** used.

Reason:

- Evaluation (2026-07-09) of `neo4j-graphrag`, Graphiti, Microsoft GraphRAG,
  LightRAG/cognee, and LlamaIndex PropertyGraphIndex against our constraints
  (Neo4j-native, closed ontology, fact-level provenance, self-hostable LLM):
  - `neo4j-graphrag` links extracted entities back to their source chunk and
    chunks to documents natively, supports schema-grounded extraction with
    strict enforcement, has swappable components, and accepts any
    OpenAI-compatible LLM (OpenRouter).
  - Graphiti has strong episode provenance but is shaped for bi-temporal
    agent memory and manages the graph its own way — it would fight our
    provenance/guard model.
  - Microsoft GraphRAG is batch/Parquet-based and not Neo4j-native;
    LightRAG/cognee trade provenance rigor for cost; LlamaIndex is a viable
    runner-up but drags in a full framework for what the Neo4j package does
    natively.
- **Resolves the open fact-level vs. document-level provenance question:
  fact-level provenance is supported and adopted.** Every extracted entity
  and relationship carries `source_document_id` + chunk linkage, so Phase 5
  retrieval can filter at fact level rather than defaulting to strict
  document-level visibility.
- **Entity nodes are scoped per document** (identity =
  `source_document_id` + type + normalized name), not merged across
  documents. Cross-document entity resolution is a permission hazard: a
  merged node derived from a restricted and an unrestricted document would
  put restricted facts one hop from unrestricted context ("a fact one
  graph-hop away from a restricted file is still restricted"). Resolution
  can be revisited after SpiceDB enforcement exists (Phase 4+), never
  before.
- Extraction output is strictly validated against the declared ontology
  (`validate_extraction_result`) and rejected loudly on violation — the
  engine's own schema grounding is a second fence, not the enforcement
  point.

Status: Accepted (2026-07-09). LLM-backed extraction was smoke-validated
live on 2026-07-09 (see the Phase 3 tracker); what remains is a production
OpenRouter configuration (real key and model). The deterministic
`ParagraphChunkExtractor` remains the default engine until then.

## ADR-011: Single-Document Provenance Contract; Audit Metadata Deferred

Decision: The canonical provenance contract (brief section 7) is the shape
Phase 3 implemented: every Neo4j node, relationship, and chunk carries
`source_document_id`, `connection_id`, `drive_file_id`, and
`source_permissions_version`; chunk-level attribution is structural
(`mentions` edges for entities, `chunk_index` on extracted relationship
edges); `Document.content_hash` identifies the extracted content version.
The brief's original `source_documents` / `source_chunk_ids` /
`extraction_run_id` / `confidence` / per-element timestamp fields are not
required — the first two are subsumed by the implemented shape, the rest are
deferred audit metadata.

Reason:

- Under ADR-010's per-document entity scoping, every graph element derives
  from exactly one source document, so a plural `source_documents` list
  always holds one value; the singular identity triple is equivalent,
  simpler, and is what the retrieval guard (`graph/guard.py`) filters on.
  The brief's strict-default rule ("all connected source documents must be
  visible") is thereby enforced by construction.
- `confidence` would be fabricated: neither the deterministic extractor nor
  the LLM engine emits a calibrated score. Storing a fake value invites
  someone to trust it later.
- `extraction_run_id` and per-element timestamps add audit value only;
  re-extraction fully replaces a document's derived data, so version and age
  follow from `Document.content_hash`. They may be added later without
  changing the permission model, and must never substitute for the required
  identity fields.

Status: Accepted (2026-07-11). Docs-only resolution — brief section 7 and
`ai-context/07-ai-coding-security-rules.md` updated to match the
implementation; no graph or writer changes.

## ADR-012: Drive Roles And Recursive Groups In A Fail-Closed SpiceDB Model

Decision: Phase 4 uses the valid `kgm/` namespace prefix (`kg/` is too short
for SpiceDB's namespace grammar) and models each supported Google Drive role as a distinct
SpiceDB relationship and composes them into `view`; folders inherit through
explicit parent relationships, and Google Groups use recursive subject sets.
Only ACL-referenced groups are resolved through the read-only Admin SDK.
Public/anyone and domain principals are deliberately absent from the schema.
All subject and Drive resource identifiers are connection-scoped and opaque.

Permission synchronization is evidence-gated: candidate documents are made
ineligible before relationship changes and become eligible only after the
complete desired tuple set is verified at least as fresh as the final write.
Stale resources are revoked only following a complete scan. Authorization
lookups use fully consistent SpiceDB `LookupResources`; PostgreSQL can reject
version-mismatched, unverified, or expired results but can never grant access
by itself. A successful full verification refreshes the evidence timestamp;
query-time filtering denies evidence older than the configured maximum age, so
failed reconciliation cannot preserve an old grant indefinitely.

Reason:

- Preserving Drive roles avoids destroying source semantics while retaining a
  single Phase 5 `view` decision.
- Explicit parents and recursive subject sets match Drive inheritance and
  nested Workspace groups without application-side allow decisions.
- Omitting wildcard/domain subjects prevents accidental public visibility.
- The pre-invalidation, verification, version-CAS, maximum evidence age, and
  fully consistent read rules make incomplete or stale Google/SpiceDB state
  fail closed.

Status: Accepted for the optional `delegated_acl` mode (2026-07-11). ADR-015
supersedes it as the active POC permission authority while reusing its
fully-consistent lookup, opaque-ID, verification, and freshness principles.

## ADR-013: Pre-Filter Vector Similarity And Server-Owned Answer Citations

Decision: Phase 5 uses one deployment-configured OpenRouter embedding adapter
for both stored Chunk vectors and question vectors. Retrieval fuses bounded
keyword chunks, vector-similar chunks, and one-hop graph facts, but every Neo4j
path first applies the SpiceDB-derived source-document allowlist and the full
provenance guard.

Neo4j 5's vector-index procedure is deliberately not used by the permission
boundary because it selects global nearest-neighbor candidates before an
arbitrary per-request document predicate can be applied. The safe path matches
allowed, provenance-complete Chunk/Document records first and computes
`vector.similarity.cosine()` or `vector.similarity.euclidean()` only within
that bounded set. The existing Chunk vector index stays provisioned for a
future pre-filter-capable or safely partitioned strategy; retrieving global
candidates and filtering them afterward remains forbidden.

OpenRouter answer synthesis is independently opt-in. It receives only bounded
JSONL context assembled after SpiceDB, Neo4j provenance, and fresh PostgreSQL
evidence checks. Source text is labeled untrusted data. The model returns only
an answer and a support boolean through a strict schema; Drive URLs and chunk
citations are always constructed by the server from the exact context evidence.

Reason:

- Permission enforcement must happen before vector scoring/candidate return,
  not as post-retrieval cleanup.
- Using the same embedding model and dimensions for indexing and questions
  prevents incompatible vector spaces.
- Reciprocal-rank fusion combines vector and keyword order without pretending
  their raw scores are comparable.
- Keeping citations outside model output prevents invented or restricted Drive
  links from entering the citation contract.
- Independent provider switches ensure enabling extraction or embeddings does
  not silently start sending retrieval context to an answer model.

Status: Accepted (2026-07-13). Live-validated on Neo4j 5.26 with the development
OAuth Drive PDF and OpenRouter.

## ADR-014: Django OpenAI-Compatible Adapter With Signed Open WebUI Identity

Decision: Phase 6 will connect Open WebUI to thin OpenAI-compatible endpoints
implemented in the existing Django backend. Django will expose one logical
knowledge-graph model through `GET /v1/models` and adapt
`POST /v1/chat/completions` to the existing `answer_query()` service. The
internal `/api/query/` contract remains unchanged. An Open WebUI
Pipeline/Function and a separate Pipelines service are not part of the primary
retrieval path.

Open WebUI will authenticate users through Google OAuth/OIDC. Its server-side
OpenAI-compatible connection will use a least-privilege service bearer key and
forward the current user in a short-lived HS256-signed identity JWT. Django
must validate the service key plus the JWT signature, fixed algorithm, issuer,
issued/expiry times, subject, and normalized email before any SpiceDB lookup.
Plain identity headers, chat payload fields, and browser-provided email values
are not trusted. Direct browser-to-backend model connections are disabled.

Reason:

- The permission-sensitive orchestration, refusal, and citations already live
  in a tested Django service and should not be duplicated inside Open WebUI.
- Open WebUI natively consumes the standard models/chat-completions protocol,
  avoiding arbitrary in-process Function code or another Pipelines service.
- Open WebUI 0.10.x supports signing forwarded user identity, giving Django a
  verifiable server-to-server assertion instead of a spoofable email header.
- A standard endpoint is easier to unit test, reuse, observe, and maintain than
  an Open WebUI-version-coupled plugin.
- Separate service and identity credentials distinguish the trusted calling
  service from the individual authorization subject.
- Keeping OpenRouter behind `answer_query()` prevents the UI from bypassing
  SpiceDB, provenance, and fresh-evidence checks.

Trade-off: Django must implement and test the small OpenAI-compatible envelope,
including model discovery, message validation, response/citation mapping, and
eventual streaming compatibility. This extra adapter work is accepted in
exchange for the clearer security and maintenance boundary.

Status: Accepted (2026-07-13).

## ADR-015: Admin-Approved Per-User Drive OAuth As The POC Permission Authority

Decision: Complete the POC with admin-approved per-user Google OAuth instead of
requiring domain-wide delegation. The per-client service account remains the
content-ingestion identity for the explicitly shared folder or Shared Drive.
Each pilot employee separately authorizes a Django web OAuth client with
`openid`, `email`, and `drive.metadata.readonly`. Workspace admin app approval
allows that OAuth client and its selected scopes; it does not impersonate users
or grant file access before each user authorizes it.

Open WebUI continues to own interactive chat login using basic Google identity
scopes. Django owns a separate Drive authorization-code flow because the signed
Open WebUI identity JWT intentionally contains identity only, not a reusable
Drive access or refresh token. The Django callback verifies OAuth state, token
issuer/audience, email verification, configured Workspace domain, required
scopes, and the normalized Google email. At query time that email must exactly
match the independently verified email in Open WebUI's signed JWT.

The visibility worker uses the employee's credential only to call Drive
`files.get` for active `SourceDocument.drive_file_id` values already ingested
from the selected root. It does not list the employee's whole Drive, ingest new
content, download file content, or accept file IDs from a request. Google thus
resolves direct, inherited, Shared Drive, Google Group, and nested-group access
for the actual employee. Positive checks produce a distinct direct
user-to-document relationship in SpiceDB plus per-user freshness evidence in
PostgreSQL. They do not masquerade as copied `reader`/`writer` ACL roles.

Refresh tokens are encrypted at rest with a dedicated deployment key and never
enter logs, API responses, Celery arguments, Open WebUI, Neo4j, or SpiceDB.
Visibility rows are authorization evidence, not an authorization engine:
retrieval still requires a fully consistent SpiceDB lookup and then intersects
it with matching fresh per-user PostgreSQL evidence. Missing authorization,
identity mismatch, scope loss, token revocation, unknown Drive results, stale
evidence, partial synchronization, or SpiceDB failure returns no context.

Changing the selected root, OAuth account, authorization generation, or active
permission mode invalidates affected evidence and managed relationships before
the new state can grant access. Disconnecting locally denies immediately and
attempts Google token revocation without depending on the remote revocation
call for local safety.

Reason:

- It avoids tenant-wide impersonation for the POC and is easier for a client to
  approve for a small pilot group.
- Google evaluates the real user's effective access, so the POC does not need
  Directory API group synchronization or its own nested-group expansion.
- Checking only indexed IDs prevents the OAuth credential from widening the
  ingestion scope or copying the employee's full Drive inventory.
- The existing SpiceDB-before-Neo4j boundary, opaque identifiers, exact
  relationship verification, and evidence-expiry defenses remain intact.
- A separate Drive consent flow keeps Open WebUI's login scope minimal and
  prevents the chat UI from becoming a token broker.

Trade-offs:

- Every pilot user must consent once and can lose access when their refresh
  token is revoked or expires.
- Visibility checks scale with connected users times indexed documents; the POC
  needs bounded batches, quotas, retries, and a documented size limit.
- `drive.metadata.readonly` is a Google restricted scope. The pilot should use a
  customer-controlled internal or explicitly admin-configured OAuth app and
  selected users/organizational units. Public distribution requires a separate
  Google verification and security-assessment decision.
- This supersedes ADR-009's delegated ACL metadata as the default permission
  authority and ADR-012's ACL/group reconciliation as the active POC path, but
  does not delete those implementations until cutover and rollback tests pass.

Status: Accepted (2026-07-14). Implementation is underway; WP1-WP3 settings,
versioned credential encryption, additive models/migration, mounted-secret
wiring, and the session-bound Drive OAuth connect/status/reconnect/disconnect
flow, and exact direct `oauth_viewer` SpiceDB relation lifecycle passed focused,
complete-backend, and official Authzed schema validation on 2026-07-15. OAuth
onboarding still creates no document grant; visibility synchronization is not
implemented yet. WP4's indexed-ID-only Drive adapter is fake-validated but its
real-Google smoke remains an external acceptance gate.

## ADR-016: Keyless ADC For The Content-Ingestion Service Account

Decision: Keep the dedicated `knowledge-graph-ingestion` service account as
the content identity, but prefer Application Default Credentials (ADC) instead
of a long-lived service-account JSON key. Local development uses short-lived
service-account impersonation through the Google Cloud CLI. A Compute Engine
deployment attaches the same service account to the VM and lets ADC obtain
short-lived credentials from the metadata server. The existing mounted JSON-key
loader remains only for explicit legacy deployments.

The ADC mode must request only Drive read-only scope, must reject delegated
Workspace impersonation, and must normalize credential-discovery failures into
controlled errors. Docker Compose may mount a local ADC file read-only for
development, but production on Google Cloud should leave the explicit ADC-file
path unset so the metadata server can be used.

Reason:

- The Workspace organization enforces
  `iam.managed.disableServiceAccountKeyCreation`.
- Short-lived impersonated or metadata-server credentials remove custody and
  rotation of a reusable private key.
- The Drive folder already grants the dedicated service account Viewer access;
  changing credential delivery does not change its Drive authorization.
- One ADC code path supports local testing and a Compose deployment on a Google
  Compute Engine VM without embedding environment-specific credential logic.

Trade-offs:

- Local developers need Google Cloud CLI authentication and
  `roles/iam.serviceAccountTokenCreator` on the dedicated service account.
- A non-Google production VM needs Workload Identity Federation or another ADC
  provider instead of the Compute Engine metadata server.
- ADC credential discovery is an authentication mechanism only; SpiceDB and
  per-user OAuth remain the authorization boundaries.

Status: Accepted (2026-07-18) after organization policy blocked long-lived key
creation. The ADC loader, Compose mount/metadata-server fallback, and local
impersonated credential are implemented. Live validation confirmed folder
discovery and document export. A Viewer-level `permissions.list()` call still
returns `403 insufficientFilePermissions`, as expected; ADR-015's per-user
OAuth path remains the employee-visibility authority.

## ADR-017: Identity-Only Google OIDC Bootstrap For Django Sessions

Decision: Create the Django browser session through a minimal Google OIDC
authorization-code flow that reuses the existing Open WebUI login OAuth client.
The client remains identity-only: Django requests `openid` and email, stores no
Google access or refresh token, and redirects a successfully authenticated user
directly into the separate Drive OAuth flow from ADR-015.

The bootstrap uses one-time session-bound state, OIDC nonce, and PKCE. Its
callback verifies the Google ID-token signature, issuer, exact client audience,
subject, verified email, and exact Workspace hosted domain before creating or
loading a non-staff Django user with an unusable password. Provider payloads
and tokens never enter logs, responses, or persistent application storage.

Reason:

- Open WebUI's signed identity JWT authenticates compatible API calls but does
  not create the Django browser session required by the Drive OAuth endpoints.
- Reusing the identity-only login client avoids creating another credential and
  requires only one additional exact callback URI.
- Keeping login and Drive authorization as separate OAuth clients preserves
  least privilege and prevents either Open WebUI or the session bootstrap from
  becoming a Drive-token broker.
- A server-verified hosted-domain claim is required; Google's `hd` request hint
  alone is not an authorization control.

Trade-offs:

- The Open WebUI login client must register both the Open WebUI callback and the
  Django session callback exactly.
- A user completes two consecutive Google flows on first connection: identity
  bootstrap, then explicit Drive metadata consent.
- Production still requires HTTPS, secure session cookies, disabled password
  login, and exact callback configuration.

Status: Accepted and implemented (2026-07-18). The exact callback registration
and live two-user acceptance remain.

## ADR-018: Chat-Guided Drive Onboarding Without An Open WebUI Patch

Decision: Keep Open WebUI's identity-only login and Django's Drive OAuth flow
as separate trust boundaries, but guide the user between them from the existing
OpenAI-compatible chat response. Before retrieval, Django resolves the signed
user to one controlled state: `not_connected`, `syncing`, `ready`,
`reauthorization_required`, or `temporarily_unavailable`. Only `ready` may call
the existing query service. A disconnected or terminal user receives a
server-built Markdown link to the public `/api/session/google/start` endpoint;
syncing and transient failures receive bounded retry guidance.

The connect URL is derived only from the already validated public origin of
`GOOGLE_SESSION_OAUTH_REDIRECT_URI`. The identity bootstrap continues to force
account selection and then redirects directly into separate Drive consent. The
successful Drive callback polls the authenticated additive status response
every two seconds and returns to the validated existing `WEBUI_URL` when fresh
visibility evidence is ready. Polling stops after two minutes and retains a
safe manual return link. No callback query parameter, handoff token, Open WebUI
patch, or additional OAuth redirect setting is introduced.

Only an explicit structured Google OAuth `invalid_grant` response is terminal:
the backend marks the authorization `refresh_failed`, wipes its encrypted
credential, rotates the authorization generation, deletes visibility evidence,
and removes managed SpiceDB relationships best-effort. Other refresh, network,
and provider failures remain retryable and surface as temporary unavailability.
Separately, an HTTP-success answer response that violates the strict
`answer`/`supported` JSON contract is retried exactly once with the same
already-filtered context. Validation is not relaxed, and all other provider
failures remain single-attempt and fail closed.

Reason:

- Pinned Open WebUI does not create Django's browser session or provide a
  supported post-login callback hook, while standard Markdown links work in
  both buffered streaming and non-streaming responses.
- The chat gate gives non-technical users one clear action without joining the
  identity and Drive-token trust boundaries.
- Reusing validated public origins prevents request-host injection and avoids
  proliferating redirect configuration.
- Gating before the query service guarantees incomplete onboarding cannot
  reach SpiceDB, Neo4j, embeddings, or OpenRouter.

Trade-offs:

- First-time users click once in chat and still complete two Google screens:
  identity selection followed by explicit Drive consent.
- Automatic return depends on the browser retaining the authenticated Django
  session; after two minutes the user uses the manual return link.
- Temporary synchronization failures block chat until fresh evidence is
  restored, preserving fail-closed behavior at the cost of availability.

Status: Accepted, implemented, and deployed to the development stack
(2026-07-20). First-connect acceptance passed for the admin and both pilot
users with exact 3/3, 2/3, and 2/3 visibility respectively and no cross-user
source leakage. Guided User 2 disconnect/reconnect also passed: local denial
immediately reduced the allowlist to zero, and reconnection restored exactly
two visible and one denied document only after the callback-triggered refresh.

## ADR-013: Pre-Filter Vector Similarity And Server-Owned Answer Citations

Decision: Phase 5 uses one deployment-configured OpenRouter embedding adapter
for both stored Chunk vectors and question vectors. Retrieval fuses bounded
keyword chunks, vector-similar chunks, and one-hop graph facts, but every Neo4j
path first applies the SpiceDB-derived source-document allowlist and the full
provenance guard.

Neo4j 5's vector-index procedure is deliberately not used by the permission
boundary because it selects global nearest-neighbor candidates before an
arbitrary per-request document predicate can be applied. The safe path matches
allowed, provenance-complete Chunk/Document records first and computes
`vector.similarity.cosine()` or `vector.similarity.euclidean()` only within
that bounded set. The existing Chunk vector index stays provisioned for a
future pre-filter-capable or safely partitioned strategy; retrieving global
candidates and filtering them afterward remains forbidden.

OpenRouter answer synthesis is independently opt-in. It receives only bounded
JSONL context assembled after SpiceDB, Neo4j provenance, and fresh PostgreSQL
evidence checks. Source text is labeled untrusted data. The model returns only
an answer and a support boolean through a strict schema; Drive URLs and chunk
citations are always constructed by the server from the exact context evidence.

Reason:

- Permission enforcement must happen before vector scoring/candidate return,
  not as post-retrieval cleanup.
- Using the same embedding model and dimensions for indexing and questions
  prevents incompatible vector spaces.
- Reciprocal-rank fusion combines vector and keyword order without pretending
  their raw scores are comparable.
- Keeping citations outside model output prevents invented or restricted Drive
  links from entering the citation contract.
- Independent provider switches ensure enabling extraction or embeddings does
  not silently start sending retrieval context to an answer model.

Status: Accepted (2026-07-13). Live-validated on Neo4j 5.26 with the development
OAuth Drive PDF and OpenRouter.

## Open / Needs Explicit Confirmation

Not yet decisions — flagged so they don't get silently locked in by omission:
- **Freshness/recency scoring** (timestamp, importance, last-updated metadata influencing retrieval priority) — the client's own idea from 2026-05-02, not scoped into any current milestone or work package. Candidate for backlog, not part of this POC unless the client asks for it explicitly.
