# Phase 5 Completion Report: Permission-Safe Retrieval

Date: 2026-07-13  
Branch: `codex/phase-5-permission-safe-retrieval`  
Status: Complete for backend development acceptance

## Executive Summary

Phase 5 established the backend security boundary that answers a question only
from Google Drive documents the authenticated user is permitted to see. The
implemented path authorizes with SpiceDB before embedding or Neo4j access,
retrieves only allowlisted and provenance-complete graph evidence, rechecks
fresh PostgreSQL permission evidence, bounds the context sent to OpenRouter,
and constructs citations on the server from the exact evidence used.

The implementation was validated against a real Google Drive PDF and a live
Neo4j/SpiceDB/OpenRouter stack. The final backend suite passed 271 tests. Phase
5 does not include Open WebUI or production Google user login; those are the
Phase 6 handoff.

## Product Promise Enforced

The Phase 5 boundary implements the project's central rule:

> If a user cannot see a source document in Google Drive, facts from that
> document cannot contribute to retrieval, model context, answers, or
> citations.

The rule is applied before retrieval rather than by filtering an unrestricted
result afterward.

## Implemented Request Flow

```text
Authenticated Django session
  -> normalize the server-side email
  -> SpiceDB fully consistent allowed-document lookup
  -> stop if authorization is empty or unavailable
  -> generate the question embedding when enabled
  -> Neo4j MATCH over only allowlisted, provenance-complete evidence
  -> keyword + vector + bounded one-hop graph retrieval
  -> fresh PostgreSQL permission-evidence intersection
  -> bounded JSONL context
  -> extractive or OpenRouter answer service
  -> server-owned permitted Drive citations
  -> answer or one controlled refusal
```

No unrestricted graph or document context is sent to the answer model.

## Delivered Components

### Authenticated query contract

- Added `POST /api/query/` through Django REST Framework.
- The request accepts only `question`, bounded to 2,000 characters.
- Unknown request fields are rejected.
- `user_email` supplied by a caller is therefore outside the contract and
  cannot select an authorization identity.
- The authenticated Django session user email is normalized and validated on
  the server.
- Missing authentication or an authenticated user without a usable email is
  denied before the query service runs.
- The view pins session authentication, authenticated-user permission, scoped
  throttling, and controlled responses explicitly.

### SpiceDB-first authorization

- Reuses the Phase 4 `allowed_source_document_ids()` boundary.
- Uses fully consistent SpiceDB `LookupResources` behavior.
- Runs the allowed-document lookup before embeddings, Neo4j retrieval, context
  assembly, or OpenRouter answer synthesis.
- Treats an empty allowlist, SpiceDB exception, or invalid lookup result as no
  context.
- PostgreSQL can only narrow the SpiceDB result; it never grants access.

### Fresh permission-evidence gate

- Retrieval evidence must resolve to an active `SourceDocument` whose
  permission state is verified and retrieval-eligible.
- The verified permissions version must still match the current source
  permissions version.
- Permission verification must be newer than
  `PERMISSION_VERIFICATION_MAX_AGE_SECONDS`.
- Expired evidence denies retrieval even if SpiceDB still contains an old
  grant.
- Successful permission synchronization refreshes verification evidence;
  repeated sync failures cannot preserve access forever.

### Permission-constrained Neo4j retrieval

- Added `Neo4jPermissionSafeRetriever` as the only Phase 5 graph retrieval
  path.
- Every Cypher path composes the per-request source-document allowlist and the
  provenance rules from `graph/guard.py`.
- Keyword retrieval returns only relevant allowlisted chunks.
- Vector retrieval first matches allowed and provenance-complete chunks, then
  calculates vector similarity inside that bounded set.
- The global Neo4j vector-index candidate procedure is not used as an
  authorization boundary because it cannot apply the request allowlist before
  candidate selection.
- Graph retrieval is limited to bounded one-hop facts whose chunk, document,
  endpoint nodes, and relationship all have consistent permitted provenance.
- Missing-provenance records and inconsistent or restricted records are
  discarded defensively after query return as a second fence.
- Unrestricted graph traversal was not introduced.

### Hybrid retrieval and embeddings

- Added an OpenRouter embedding adapter behind the existing embedding
  interface.
- Stored chunk embeddings and question embeddings use one deployment-selected
  model and validated dimensions.
- Provider results are reordered by provider index and rejected when counts,
  indices, values, or dimensions are malformed.
- Keyword and vector rankings are combined with reciprocal-rank fusion rather
  than comparing incompatible raw scores.
- Added an idempotent `graph_reindex_embeddings` management command that queues
  document IDs and content hashes rather than raw content.
- Permission-only changes do not trigger re-embedding.
- Embeddings remain independently configurable from graph extraction and answer
  synthesis.

### Bounded context and OpenRouter answer boundary

- Added a bounded JSONL context assembler.
- Each context record retains the exact source document and chunk evidence used
  to construct it.
- Source text is serialized as untrusted data, not instructions.
- The context has global and per-item character bounds.
- The OpenRouter service receives only the already-authorized assembled
  context.
- Answer generation uses a strict structured response containing only answer
  text and a support decision.
- Malformed, empty, unsupported, oversized, or failed provider responses cause
  the shared refusal.
- Enabling embeddings cannot silently enable remote answer synthesis.

### Server-owned citations and refusal

- Models do not provide Drive URLs or authoritative source identifiers.
- Citations are created by Django from the fresh PostgreSQL documents and the
  exact evidence that fit in the bounded model context.
- Citation fields include title, Drive file ID, Drive URL, and chunk ID.
- Duplicate citations are removed without widening the source set.
- Authorization failures, missing evidence, insufficient relevance, retrieval
  failures, embedding failures, and model failures use the same answer:

```text
I do not have enough accessible context to answer that.
```

- The refusal returns no citations and does not reveal whether a restricted
  document or fact exists.

### Development and operational support

- Added explicit OpenRouter embedding and answer configuration with safe
  defaults, timeouts, and validation.
- Added a writable owner-only OAuth token directory for local Drive development
  while keeping the client secret and service-account key read-only.
- Added PDF text extraction support for the real development document.
- Added an embedding reindex operator command and a direct authenticated
  development smoke-test procedure.
- Updated retrieval, permission, provenance, configuration, and contributor
  documentation.

The development OAuth path is a local validation aid. It is not the production
Google Workspace identity or ACL strategy.

## Fail-Closed Behavior Proven

| Condition | Result |
| --- | --- |
| Unauthenticated request | HTTP 403 before query orchestration |
| Request-body identity spoof | HTTP 400; supplied identity is not used |
| Authenticated user has no valid email | HTTP 403 |
| SpiceDB returns no documents | Controlled refusal; no downstream retrieval |
| SpiceDB fails | Controlled refusal; no context or citations |
| Permission evidence is inactive, ineligible, unverified, mismatched, or expired | Evidence excluded |
| Question embedding fails or has wrong dimensions | No Neo4j session opened |
| Neo4j retrieval fails | Controlled refusal; retrieved context discarded |
| Restricted fact touches visible nodes | Restricted fact excluded |
| Node or relationship lacks provenance | Record excluded |
| No relevant accessible evidence | Controlled refusal |
| Context assembly yields no safe data | Answer model is not called |
| Answer provider fails or rejects support | Controlled refusal; no citations |
| Retriever returns an unexpected document | Citation intersection removes it |

## Automated Validation

The final development-acceptance run recorded:

- 271 complete backend tests passed.
- Ruff lint passed.
- Ruff format validation passed.
- Django runtime system checks passed.
- Infrastructure, production application, and development Compose
  configurations rendered successfully.
- Django `check --deploy` returned no errors; it reported only the existing
  optional HSTS `includeSubDomains` and preload warnings.

Focused coverage includes:

- allowed versus restricted users;
- identity normalization and request-data spoofing;
- empty allowlists and SpiceDB failure;
- authorization-before-retrieval ordering;
- inactive, retrieval-ineligible, unverified, version-mismatched, and expired
  permission evidence;
- restricted facts connected to visible nodes;
- missing-provenance nodes and relationships;
- pre-filtered vector similarity and guarded rank fusion;
- embedding and Neo4j failures;
- bounded context and prompt-injection-shaped source text;
- restricted-context absence from OpenRouter payloads;
- unsupported or malformed model responses;
- citations limited to context evidence and allowed documents;
- controlled refusal throughout the pipeline.

## Live Development Acceptance

A real Google Drive PDF completed ingestion, extraction, permission sync,
embedding reindex, and query validation.

Recorded graph state:

- 1 document;
- 2 chunks;
- 19 entities;
- 44 total relationships;
- zero nodes missing required provenance;
- zero relationships missing required provenance;
- both chunks stored 1,536-dimension embeddings.

An allowed relevant query used keyword and vector evidence and returned an
OpenRouter-generated answer with two citations to the permitted PDF chunks.
The following live cases failed safely:

- unrelated question;
- restricted user;
- expired permission evidence;
- unauthenticated request;
- attempted request-body identity spoofing.

Denied cases produced no restricted context or citations and did not trigger
downstream providers when an earlier gate had already denied the request.

## Change Footprint

Before this report was added, the Phase 5 branch contained 37 logical commits
relative to `origin/main`, changing 51 tracked files with approximately 3,395
insertions and 130 deletions. The footprint includes the retrieval feature plus
the permission-expiry, development OAuth/PDF, embedding, configuration, test,
and documentation prerequisites needed for live acceptance.

The branch has not been pushed, merged, or turned into a pull request. No Phase
6 implementation code is included.

## Security Assumptions And Boundaries

- SpiceDB is the authorization authority. PostgreSQL stores and narrows
  synchronization evidence only.
- A source document is usable only while its permission evidence remains
  verified, version-matched, eligible, active, and fresh.
- Required graph provenance is `source_document_id`, `connection_id`,
  `drive_file_id`, and `source_permissions_version` on every returned graph
  element.
- Entities remain scoped per document; cross-document entity merging is not
  enabled.
- Vector scoring is performed only after the allowlist/provenance match.
- Server-generated citations are authoritative; model-generated citations are
  not accepted.
- Phase 5 trusts a Django-authenticated session user. Production Google/OIDC
  user provisioning is intentionally deferred to Phase 6.
- The live test proves the development path on one PDF. It does not claim
  production-scale performance or live delegated Workspace ACL/group behavior.

## Known External Gate

Phase 4 live validation of delegated Google Workspace ACL visibility and
nested Directory groups still requires client credentials. The Phase 5 code
fails closed without valid permission evidence, but full production permission
fidelity cannot be claimed until that external validation is completed.

## Phase 6 Handoff

Phase 6 must expose this completed backend through Open WebUI without weakening
the Phase 5 boundary. It must replace manual development session provisioning
with trusted Google/OIDC identity, keep `/api/query/` unchanged as the internal
contract, and prove the same allowed-versus-restricted behavior through the
actual chat interface.

The detailed execution plan is maintained separately in
`docs/phase-6-implementation-plan.md`.
