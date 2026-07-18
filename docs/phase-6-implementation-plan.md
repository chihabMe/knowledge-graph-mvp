# Phase 6 Implementation Plan: Open WebUI Integration

Prepared: 2026-07-13
Implementation status: Code complete; local Open WebUI acceptance passed
Acceptance status: Two-user backend authorization passed; real Open WebUI Google chat pending
Predecessor: Phase 5 complete for backend development acceptance

> **Completion-plan update (2026-07-14):** ADR-015 selects admin-approved
> per-user Drive OAuth as the POC permission authority. The active remaining
> plan is `docs/phase-6-pre-authorized-oauth-completion-plan.md`. This document
> remains the implementation record for the completed Open WebUI adapter and
> signed-identity boundary; where permission-authority steps conflict, ADR-015
> and the new completion plan win.

## Goal

Expose the completed permission-safe query backend as the knowledge-graph model
inside Open WebUI. Users must authenticate with Google, and Django must receive
a cryptographically verifiable identity that maps to the same email used by
Google Drive and SpiceDB.

Phase 6 is complete only when an allowed and a restricted Google user can ask
the same question through the real Open WebUI interface and the restricted user
cannot receive restricted context, facts, or citations.

## Progress Snapshot (2026-07-14)

Work Packages 1-6 and the local portion of Work Package 8 are implemented. The
pinned UI required two compatibility additions discovered through real local
requests: a bounded ignored `tools` inventory and buffered `stream=true`
Server-Sent Events. A local password-bootstrap user successfully queried
synthetic Atlas data through Open WebUI and received the permitted citation.

The completion gate is still open. The two OAuth clients, keyless content
identity, selected Drive root, content ingestion, and identity-only Django
session bootstrap are configured and live-validated. Both Workspace users also
completed the separate Drive consent and passed the exact indexed-document
allow/deny matrix at the final SpiceDB plus freshness boundary. Work Packages 7
and 8 still need the real Open WebUI Google login/chat route, revocation,
evidence-expiry, and restricted-citation checks. The local smoke configuration
also deliberately disabled OpenRouter, so the production provider route must
be repeated through the UI during live acceptance.

## Accepted Architecture

Phase 6 will use an OpenAI-compatible adapter implemented in Django.

```text
Browser
  -> Google OAuth/OIDC
  -> Open WebUI authenticated session
  -> server-side OpenAI-compatible connection
       Authorization: Bearer <service key>
       X-OpenWebUI-User-Jwt: <short-lived signed identity JWT>
  -> Django /v1/chat/completions
  -> verify service key and identity JWT
  -> existing answer_query(question, verified_email)
  -> SpiceDB before retrieval
  -> allowed/provenance-complete Neo4j evidence only
  -> bounded context
  -> OpenRouter behind the existing answer boundary
  -> server-owned citations
  -> OpenAI-compatible response
  -> Open WebUI chat
```

The existing `POST /api/query/` contract remains unchanged and continues to use
Django session authentication for direct backend development testing.

An Open WebUI Pipeline/Function will not be used for the primary retrieval
path. A separate Pipelines service will not be added. Open WebUI Direct
Connections will not be used because browser-to-backend calls would weaken the
server-to-server trust boundary.

## Why This Pattern Was Chosen

- Permission-sensitive behavior stays in the tested Django backend.
- The Phase 5 `answer_query()` service remains the only retrieval
  implementation.
- Open WebUI natively supports OpenAI-compatible model providers.
- The adapter can be covered by normal Django unit, API, leak, and integration
  tests.
- No arbitrary Python Function must be stored and executed inside Open WebUI.
- No additional Pipelines container or release lifecycle is introduced.
- The endpoint remains reusable by other trusted OpenAI-compatible clients.
- Open WebUI 0.10.x supports short-lived signed user-info JWT forwarding,
  avoiding trust in plaintext email headers.

Canonical decision: ADR-014 in `ai-context/04-decisions.md`.

Official Open WebUI references to recheck against the pinned version before
implementation:

- Environment and signed identity forwarding:
  <https://docs.openwebui.com/reference/env-configuration/>
- Google OAuth/OIDC configuration:
  <https://docs.openwebui.com/features/authentication-access/auth/sso/>
- OpenAI-compatible provider connection:
  <https://docs.openwebui.com/getting-started/quick-start/connect-a-provider/starting-with-openai-compatible/>

The repository pins `ghcr.io/open-webui/open-webui:0.10.2`. Configuration must
be tested against that exact image rather than assuming newer documentation is
automatically compatible.

## Non-Negotiable Security Rules

1. Google/OIDC authentication happens before Open WebUI can send a knowledge
   question.
2. Django authenticates Open WebUI as a service separately from authenticating
   the individual user assertion.
3. Plain `X-OpenWebUI-User-Email` and request-body email fields are never
   authorization evidence.
4. The signed identity JWT must be verified before any SpiceDB lookup.
5. The JWT algorithm is fixed server-side; an algorithm supplied by the token
   is never selected dynamically.
6. The signature, issuer, subject, issued-at time, expiry time, and email must
   be valid.
7. Missing, expired, malformed, incorrectly signed, or incomplete identity
   assertions deny the request before retrieval.
8. The service bearer key and identity-signing secret must be independent and
   must not reuse `WEBUI_SECRET_KEY`.
9. `/v1/chat/completions` delegates to `answer_query()`; it must not reproduce
   or bypass SpiceDB/Neo4j authorization logic.
10. Chat history supplied by Open WebUI is not trusted retrieval context. The
    first adapter extracts only a bounded user question.
11. No unrestricted chat, document, or graph context is forwarded directly to
    OpenRouter.
12. Citation URLs remain server-owned and may reference only evidence returned
    by the Phase 5 permission boundary.
13. The knowledge-graph OpenAI connection must be restricted to one logical
    model and must not enable catch-all upstream API passthrough.
14. Authentication and integration logs must not contain tokens, emails,
    questions, context, document content, Drive IDs, or provider payloads.
15. Any authentication, SpiceDB, permission-evidence, retrieval, context,
    citation, or provider uncertainty fails closed.

## Target Interfaces

### `GET /v1/models`

Purpose: allow Open WebUI to discover the single knowledge-graph model.

Authentication:

- Requires the Open WebUI-to-Django service bearer key.
- Does not require an individual user JWT because Open WebUI may verify a
  connection outside a user chat request.

Target response shape:

```json
{
  "object": "list",
  "data": [
    {
      "id": "client-knowledge-graph",
      "object": "model",
      "created": 0,
      "owned_by": "knowledge-graph-mvp"
    }
  ]
}
```

The endpoint returns no graph, Drive, permission, user, or provider data.

### `POST /v1/chat/completions`

Purpose: adapt an OpenAI-compatible chat request to the existing Phase 5 query
service.

First-slice accepted request surface:

- `model`: must equal the configured logical model ID.
- `messages`: bounded list of role/content objects.
- `stream`: boolean; `false` is implemented first.

The adapter selects the last non-empty string `user` message as the question,
applies the Phase 5 question bound, and ignores no identity supplied in the
message payload. System and assistant messages are not sent to `answer_query()`
or used as permission-filtered context.

Before finalizing serializers, capture one real request from the pinned Open
WebUI instance and explicitly document any additional compatibility fields it
sends. Accept only fields required for compatibility, place strict size/count
bounds on them, and reject identity-bearing extensions.

Target non-streaming response:

```json
{
  "id": "chatcmpl-<opaque-id>",
  "object": "chat.completion",
  "created": 0,
  "model": "client-knowledge-graph",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Permission-safe answer\n\nSources:\n1. [Document](https://drive.google.com/...)"
      },
      "finish_reason": "stop"
    }
  ]
}
```

The answer and source list are rendered by Django. Drive links come only from
the server-owned Phase 5 citation objects. A refusal renders the existing
generic refusal and no source section.

A structured top-level extension may preserve the existing citation/refusal
metadata if the pinned Open WebUI version passes it through safely, but the
standard `message.content` must remain sufficient for the user to see the
answer and permitted sources. This behavior must be verified rather than
assumed.

### Streaming compatibility

The first safe vertical slice implements and tests `stream=false`. After the
non-streaming boundary is proven, inspect the pinned Open WebUI request and add
buffered Server-Sent Events only if required for the real chat path.

Buffered streaming means Django completes the full permission-safe query first
and only then emits compatible deltas and `[DONE]`. It must not stream raw
OpenRouter output or any partial response before authorization, retrieval,
support, and citation decisions have completed.

## Authentication Design

### Service authentication

- Add a dedicated environment secret such as
  `OPEN_WEBUI_BACKEND_API_KEY`.
- Open WebUI sends it as `Authorization: Bearer ...` on the server-side OpenAI
  connection.
- Django compares it in constant time.
- Missing, malformed, or incorrect credentials return a controlled 401.
- The key authenticates the Open WebUI service only; it never selects a user.
- The key is inference-scoped and not an OpenRouter management key.

### User identity assertion

- Enable Open WebUI signed user-info forwarding.
- Use a separate `OPEN_WEBUI_IDENTITY_JWT_SECRET` on both services.
- Keep the official default header or configure it explicitly as
  `X-OpenWebUI-User-Jwt`.
- Use a short lifetime, initially 60-300 seconds depending on pinned-version
  behavior and clock skew.
- Django accepts only HS256 and validates at least:
  - signature;
  - `iss == "open-webui"`;
  - non-empty `sub`;
  - numeric and valid `iat`;
  - numeric and unexpired `exp`;
  - a bounded lifetime;
  - a syntactically valid, normalized email.
- Add only a small explicit clock-skew allowance.
- Do not auto-create Django database users for compatible endpoint requests.
  Use an immutable authenticated principal carrying the verified email.
- Reuse `trusted_user_email()` or a shared normalization primitive after the
  cryptographic verification step.

`PyJWT` currently appears only as a transitive lockfile dependency. Phase 6
must declare the JWT library directly if Django imports it.

### Authentication separation by endpoint

- `/api/query/`: keep the Phase 5 Django session authentication unchanged.
- `/v1/models`: service bearer authentication only.
- `/v1/chat/completions`: service bearer authentication plus verified signed
  user identity.

## Open WebUI Configuration Plan

### Provider connection

Configure the server-side OpenAI-compatible connection through deployment
settings rather than a browser Direct Connection:

- `ENABLE_OPENAI_API=true`
- `OPENAI_API_BASE_URL=http://django:8000/v1`
- `OPENAI_API_KEY=<Open WebUI-to-Django service key>`
- `OPENAI_API_CONFIGS` restricted to `client-knowledge-graph`
- `BYPASS_MODEL_ACCESS_CONTROL=true` only because the connection allowlists
  that one Django-owned logical model; this lets new tenant users discover it
  without per-user Open WebUI grants
- keep `ENABLE_OPENAI_API_PASSTHROUGH=false`

Verify how Open WebUI's persistent `ConfigVar` behavior interacts with the
existing named volume. Prefer environment-authoritative per-client deployment
configuration and document any one-time reset/migration needed for an existing
volume. Never delete the volume automatically.

### Signed identity forwarding

- `ENABLE_FORWARD_USER_INFO_HEADERS=true`
- `FORWARD_USER_INFO_HEADER_JWT_SECRET=<separate signing secret>`
- `FORWARD_USER_INFO_HEADER_JWT=X-OpenWebUI-User-Jwt`
- `FORWARD_USER_INFO_HEADER_JWT_EXPIRES_SECONDS=<short lifetime>`

Do not fall back to accepting the legacy plaintext user-info headers.

### Google login

Expected Open WebUI configuration includes:

- `WEBUI_URL=<public Open WebUI URL>` set before OAuth is enabled;
- `GOOGLE_CLIENT_ID`;
- `GOOGLE_CLIENT_SECRET`;
- `OPENID_PROVIDER_URL=https://accounts.google.com/.well-known/openid-configuration`;
- `ENABLE_OAUTH_SIGNUP=true`;
- `OAUTH_ALLOWED_DOMAINS=<the same Workspace domain accepted by Django>`;
- `ENABLE_SIGNUP=false` for local signup;
- `OAUTH_MERGE_ACCOUNTS_BY_EMAIL=false`;
- `ENABLE_OAUTH_ID_TOKEN_COOKIE=false` unless the pinned image proves it is
  required;
- `ENABLE_PROFILE_IMAGE_URL_FORWARDING=false` unless the client explicitly
  accepts the external avatar privacy trade-off;
- local password authentication and password-change UI disabled after a safe
  administrator bootstrap/recovery path is documented.

The Google OAuth client must allow the exact Open WebUI callback URI, normally:

```text
https://<open-webui-host>/oauth/google/callback
```

Use the real client domain and callback during live acceptance. Do not document
or commit real client secrets.

## Implementation Work Packages

### Work Package 0: Safe branch and baseline

- Confirm the Phase 5 branch is clean and contains the completion report and
  this plan.
- Create `codex/phase-6-open-webui-integration` from the Phase 5 branch HEAD if
  Phase 5 is still unmerged. This is a temporary stacked branch.
- Do not switch to or modify `main`.
- Do not push, create a pull request, merge, or rebase unless the user
  explicitly requests it.
- Read `AGENTS.md`, the required context files, this plan, ADR-014, and the
  Phase 6 tracker.
- Query the backend Graphify graph before broad code searches.
- Record the baseline branch, worktree state, and current tests without
  discarding unrelated user changes.

Exit condition: Phase 6 work is isolated on its own branch and the Phase 5
baseline is understood.

### Work Package 1: Pin the compatibility contract

- Verify the official Open WebUI documentation against pinned version 0.10.2.
- Start the existing Open WebUI container without changing customer state.
- Capture a sanitized model-discovery and non-streaming chat request.
- Confirm header names and signed JWT claim shape without logging real tokens.
- Finalize request limits, response shape, error shape, model ID, citation
  rendering, and streaming behavior.
- Replace stale prototype API documentation with current-versus-planned
  interfaces.

Exit condition: tests can be written against an explicit contract rather than
guessed Open WebUI behavior.

### Work Package 2: Implement the service and identity boundary

- Add a direct JWT dependency.
- Add validated Django settings for:
  - service API key;
  - identity JWT secret;
  - identity header name;
  - expected issuer;
  - maximum lifetime and clock skew;
  - logical model ID.
- Production startup must fail closed if the compatible endpoint is enabled
  with missing/default secrets.
- Implement service bearer parsing and constant-time verification.
- Implement signed identity verification behind a small authentication module.
- Return an authenticated immutable principal without database provisioning.
- Log only controlled exception class/category information.

Exit condition: invalid service or identity credentials cannot reach
`answer_query()`, SpiceDB, embeddings, Neo4j, or OpenRouter.

### Work Package 3: Add the model-discovery endpoint

- Add the `/v1/` URL namespace.
- Implement `GET /v1/models` with explicit service authentication, permission,
  throttle, and response serializer.
- Expose exactly one configured knowledge-graph model.
- Return no provider inventory and no application data.

Exit condition: Open WebUI can verify and list only the intended logical model.

### Work Package 4: Add non-streaming chat completion

- Add bounded OpenAI-compatible request serializers.
- Reject missing/unknown models, malformed messages, non-string content in the
  first slice, oversized conversations, empty questions, and identity fields.
- Extract only the bounded last user question.
- Require both service authentication and signed user identity.
- Call the existing `answer_query(question, verified_email)` service.
- Translate successful answers and controlled refusals into the compatible
  envelope.
- Render permitted Drive citations server-side.
- Preserve existing throttling and add a separate compatible-endpoint scope if
  useful for observability.

Exit condition: a signed test user can receive the same permission-safe result
through `/v1/chat/completions` as through `/api/query/`.

### Work Package 5: Add compatibility and leak tests

Add focused tests before configuring a live UI:

- correct service key with valid signed identity;
- missing, malformed, and incorrect service key;
- missing JWT;
- `alg=none` and wrong-algorithm tokens;
- incorrect signature;
- wrong issuer;
- missing or blank subject;
- expired token;
- unreasonably long token lifetime;
- future issued-at time outside allowed skew;
- missing, malformed, mixed-case, and whitespace-padded email;
- spoofed plaintext email header;
- spoofed email or user identity in JSON/messages;
- model discovery returns one model and no sensitive data;
- invalid model and malformed/oversized message lists;
- system/assistant messages never become retrieval context;
- allowed versus restricted signed users;
- restricted facts connected to visible nodes;
- missing provenance;
- inactive/ineligible/unverified/expired permission evidence;
- empty allowed-document list;
- SpiceDB failure;
- Neo4j, embedding, context, and OpenRouter failure;
- permitted citations only;
- refusal contains no source list and does not reveal restricted existence;
- OpenRouter is never called before all existing safety gates pass.

Exit condition: the compatible adapter adds no path around the Phase 5 leak
tests.

### Work Package 6: Configure Open WebUI and Compose

- Add documented environment variables with non-secret placeholders.
- Wire the separate service key and identity-signing secret into Django and
  Open WebUI.
- Configure the internal Django `/v1` base URL and one-model allowlist.
- Enable signed identity forwarding.
- Keep catch-all passthrough and browser Direct Connections disabled.
- Add Google OAuth/OIDC configuration variables.
- Keep production secrets outside the repository.
- Validate infrastructure, app, and development Compose rendering.
- Verify existing Open WebUI persistent-volume behavior without deleting it.

Exit condition: Open WebUI reaches Django over the private Compose network and
cannot select a direct OpenRouter model for the knowledge-graph path.

### Work Package 7: Google OAuth/OIDC and login hardening

- Configure a Google web OAuth client and exact callback URL when credentials
  are available.
- Confirm Open WebUI receives a Google email and establishes the expected user
  account.
- Confirm the signed JWT email matches that Google identity.
- Prevent unsafe email-based account merging.
- Disable local signup and production password authentication after
  administrator recovery is documented.
- Decide whether to restrict login to the client Workspace domain; treat an
  `hd` hint as UI guidance, not authorization by itself.
- Ensure a user outside the intended deployment cannot gain document access;
  SpiceDB remains the final authorization boundary even if login is permitted.

Exit condition: real Google login produces the exact verified email used by
SpiceDB permission lookup.

### Work Package 8: End-to-end chat and streaming

- Ask a relevant question through Open WebUI as an allowed user.
- Verify an answer and clickable citation to only the allowed Drive document.
- Ask the same question as a restricted user.
- Verify the generic refusal, no citations, and no indication that hidden facts
  exist.
- Exercise expired permission evidence and SpiceDB failure through the UI
  route.
- Confirm chat history does not bypass current question/context bounds.
- Add buffered OpenAI-compatible streaming only if the pinned UI requires it,
  then repeat all identity and leak tests for `stream=true`.
- Verify logs contain no identity, token, question, context, or Drive payload.

Exit condition: every Phase 6 validation item passes through the actual UI.

### Work Package 9: Acceptance, operations, and handoff

- Run focused tests throughout implementation.
- Run Ruff lint and format checks.
- Run Django runtime and deployment checks.
- Render all Compose configurations.
- Run the complete backend suite.
- Perform the documented live two-user acceptance.
- Update the canonical brief first for any changed fact.
- Update ADRs, API/operations docs, Phase 6 tracker, README/AGENTS summaries,
  and the daily report only for work genuinely completed.
- Do not execute or claim the ADR-015 per-user permission work from this
  historical adapter plan; use the active completion plan and its evidence.
- Do not push, create a pull request, merge, or switch to `main` without the
  user's explicit request.

Exit condition: the Phase 6 tracker has evidence for every checked task and
validation item.

## Test Commands And Validation Strategy

Use the repository's existing commands and container workflow. The exact
targeted Django labels should be added as Phase 6 test modules are created.

Expected progression:

1. Identity unit tests after each authentication change.
2. `/v1/models` API tests.
3. `/v1/chat/completions` contract and denial tests.
4. Adapter leak tests using the existing Phase 5 service doubles.
5. Open WebUI/Compose configuration validation.
6. Live allowed/restricted Google user acceptance.
7. Complete backend suite and final lint/config checks.

Do not run repository audit commands unless the user explicitly requests an
audit. Existing commit hooks may run their own staged checks; report their
output without silently changing or dismissing findings.

## Planned Logical Commit Structure

Keep changes small and reviewable. The implementation should target at least 20
meaningful commits when the work naturally supports them; do not create empty
or timestamp-only commits merely to reach a number.

Suggested sequence:

1. Record the pinned Open WebUI request contract.
2. Declare the JWT dependency.
3. Add compatible-endpoint settings validation.
4. Add service bearer authentication.
5. Test service bearer denial behavior.
6. Add signed identity JWT verification.
7. Test signed identity claim and spoofing behavior.
8. Add the immutable Open WebUI principal.
9. Add compatible request serializers.
10. Test bounded message extraction.
11. Add the models endpoint.
12. Test model discovery and information minimization.
13. Add the non-streaming chat endpoint.
14. Map controlled refusals into chat completions.
15. Render server-owned citations in chat responses.
16. Add compatible-endpoint permission leak tests.
17. Add provider and dependency failure tests.
18. Configure the private Open WebUI backend connection.
19. Configure signed identity forwarding.
20. Add Google OAuth/OIDC deployment settings.
21. Harden local-login and passthrough settings.
22. Add the Open WebUI operator smoke test.
23. Add buffered streaming if the pinned UI requires it.
24. Document live two-user acceptance.
25. Complete the tracker and session report.

Commits may be combined when two items cannot be tested independently, but a
large cross-layer bulk commit should be avoided.

## Dependencies And External Inputs

Implementation and automated tests can begin without client credentials.

Live completion requires:

- separate Google OAuth web clients for Open WebUI identity and Django Drive
  metadata authorization;
- Workspace admin approval for the restricted Drive metadata scope and pilot
  users/organizational unit;
- the exact public Open WebUI and Django callback URLs;
- at least two test Google identities with different Drive visibility;
- fresh successful per-user visibility synchronization for the live documents;
- OpenRouter configuration already proven in Phase 5.

Domain-wide-delegated ACL and Directory group validation is no longer a POC
completion dependency. It remains an optional legacy/future mode and must not
be automatically combined with per-user relationships.

## Phase 6 Definition Of Done

Implementation tasks:

- [x] OpenAI-compatible endpoint selected as the integration pattern.
- [x] Open WebUI service settings configured and reproducible.
- [ ] Google OAuth/OIDC login configured.
- [ ] Authenticated Google identity reaches Django as a verified signed JWT.
- [x] Knowledge-graph model requests route through the existing safe OpenRouter
  boundary.
- [x] End-to-end Open WebUI chat flow tested locally with synthetic data.

Validation:

- [ ] User can log in with Google.
- [x] Backend receives a cryptographically trusted signed identity locally.
- [x] User can ask a question through Open WebUI locally.
- [x] Backend returns a permission-safe answer with permitted citations locally.
- [ ] Restricted facts remain hidden through the actual UI route.

## Exact First Implementation Slice

Historical status: complete. Do not restart this slice; continue with WP1 in
`docs/phase-6-pre-authorized-oauth-completion-plan.md`.

After creating the Phase 6 branch, implement the server-to-server trust and
non-streaming compatibility boundary in this order:

1. Add direct JWT dependency and validated settings.
2. Implement service bearer verification.
3. Implement signed identity JWT verification.
4. Add `/v1/models`.
5. Add a bounded non-streaming `/v1/chat/completions` adapter over
   `answer_query()`.
6. Add identity-spoofing, authentication-order, allowed/restricted, refusal,
   and citation tests.
7. Run targeted tests before changing Open WebUI configuration.

This proves that Open WebUI cannot create a second, weaker retrieval path
before live Google login or UI configuration begins.

## Compaction Handoff Prompt

Historical status: superseded. Future work must use the ADR-015 completion plan
rather than the prompt below.

Use the following prompt after compacting the conversation:

```text
Continue the Knowledge Graph MVP from the completed Phase 5 handoff and begin
Phase 6 Open WebUI Integration.

Workspace:
/home/user/Documents/projects/knowledge-graph-mvp

Before making changes:
1. Read AGENTS.md completely.
2. Read docs/phase-5-completion-report.md completely.
3. Read docs/phase-6-implementation-plan.md completely and follow it as the
   execution source for this phase.
4. Read ai-context/00-project-overview.md,
   ai-context/01-architecture.md, ai-context/03-implementation-rules.md,
   ai-context/04-decisions.md (especially ADR-014),
   ai-context/05-test-and-acceptance.md,
   ai-context/07-ai-coding-security-rules.md, and
   ai-context/phases/phase-6-open-webui-integration.md.
5. Read the relevant Phase 6 and public-interface sections of
   AGENT_PROJECT_BRIEF.md.
6. Query apps/backend/graphify-out before broad backend code searches.
7. Inspect the branch, commits, worktree, and existing user changes. Preserve
   all unrelated work. Do not run repository audit commands.

Branch rules:
- Phase 5 is complete on codex/phase-5-permission-safe-retrieval but may still
  be unpushed and unmerged.
- Do not implement Phase 6 on the Phase 5 branch.
- If Phase 5 is still unmerged, create
  codex/phase-6-open-webui-integration from the current Phase 5 HEAD as a
  stacked branch. Do not switch to or modify main.
- Do not push, open a pull request, merge, or rebase unless I explicitly ask.
- Make small, meaningful, test-backed commits. Aim for 20+ logical commits over
  Phase 6 when the work naturally supports them; never create empty commits or
  wait between commits just to manipulate timestamps.

Accepted architecture:
- Use a thin OpenAI-compatible Django adapter, not an Open WebUI
  Pipeline/Function or separate Pipelines service.
- Keep POST /api/query/ and answer_query() as the existing internal
  permission-safe retrieval implementation.
- Add GET /v1/models and POST /v1/chat/completions for the server-side Open
  WebUI connection.
- Authenticate Open WebUI with a separate service bearer key.
- Accept user identity only from a short-lived HS256-signed
  X-OpenWebUI-User-Jwt assertion. Verify the fixed algorithm, signature,
  issuer, subject, issued/expiry times, bounded lifetime, and normalized email.
- Never trust request JSON identity or plaintext forwarded email headers.
- Google OAuth/OIDC is performed by Open WebUI; local production password login
  will be disabled after a safe admin bootstrap path exists.
- OpenRouter remains behind answer_query() and receives only context that has
  already passed SpiceDB, fresh permission evidence, and every Neo4j provenance
  guard.
- Direct browser connections and OpenAI catch-all passthrough stay disabled.

Start with the plan's Exact First Implementation Slice. Do not stop after
planning: inspect, implement the first safe vertical slice, add focused API and
leak tests, run targeted validation, document completed work, and commit it in
small logical commits. Do not mark later Phase 6 tasks complete until their
live or automated acceptance evidence exists.

When you finish the slice, report:
- implementation and commits;
- tests and configuration checks;
- security assumptions;
- external credential blockers;
- exact next Phase 6 task.
```
