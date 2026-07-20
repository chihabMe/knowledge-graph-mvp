# Phase 7 Implementation Plan — Change Feed And Evaluation

Written 2026-07-19 after a full code assessment and vendor-documentation
research pass. This is the working plan for
`ai-context/phases/phase-7-change-feed-and-evaluation.md`; the tracker states
*what* must be true, this document states *how* to build it and in what order.

## 1. Entry State

- Phase 5 is merged to `main` as squash commit `366109f` (PR #4).
- Phase 6 is functionally complete and live-validated on the branch
  `codex/phase-6-open-webui-integration` (main merged back in at `38624ab`,
  449 tests green). Formal closeout, push, and the Phase 6 PR are gated on
  explicit operator approval and the citation over-inclusion ruling.
- Phase 7 must start from a merged Phase 6 on `main`.
- Open GitHub issues feeding this phase:
  - **#5** — gate retrieval context on the current extracted content version
    (triaged from the Codex review of PR #4; content-freshness, not an
    authorization leak).
  - **#2** — privacy-safe usage ledger (not Phase 7 scope; do not pull in).

## 2. What Already Exists (verified in code, 2026-07-19)

| Capability | Status | Where |
| --- | --- | --- |
| Detect changed content and re-index only changed files | Done (sweep-triggered) | `integrations/drive/sync.py` — `_needs_content_refresh` compares `modified_time`, re-exports, queues extraction with content hash; failed-extraction requeue exists |
| Atomic graph replacement, stale-version extraction skip | Done | `graph/pipeline.py`, `graph/writer.py` |
| Permission refresh without re-embedding | Done | Delegated: `authorization/sync.py`; per-user: `integrations/drive/user_visibility_sync.py` — neither touches embeddings |
| Change-triggered per-user refresh | One seam | Drive OAuth callback queues an immediate bounded visibility refresh (Phase 6) |
| Periodic reconciliation | Done | Celery beat: permission syncs, user visibility syncs (15 min), stale-run sweeps (15 min) |
| Freshness intervals as config | Done | `PERMISSION_SYNC_INTERVAL_SECONDS`, `GOOGLE_USER_VISIBILITY_SYNC_INTERVAL_SECONDS`, evidence max age; startup validator enforces expiry > interval |
| Run/evidence bookkeeping for monitoring | Data exists, no alarms | `DriveSyncRun`, `PermissionSyncRun`, user visibility runs, `UserDocumentVisibility` timestamps |
| Drive changes feed | **Missing** | No `changes.getStartPageToken` / `changes.list` / watch channels anywhere |
| Pre-expiry alerting | **Missing** | No alert consumer computes or pages on last-success age or expiry proximity |
| Content-currency gate at retrieval (issue #5) | **Missing** | `retrieval/services.py` never compares chunk provenance content version to `SourceDocument.content_hash` |
| Evaluation dataset | Scaffold only | `data/eval/` has README + example YAMLs (`questions`, `refusals`, `users`); no real pilot questions, no runner |
| Leak tests | Unit-level only | Extensive in-process tests; no black-box run against the live stack, nothing scheduled |

Conclusion: roughly half of Phase 7's substance exists because the
architecture is periodic-reconciliation-first. The new work is the event
layer, the alarm layer, the retrieval content-currency gate, and the
evaluation runner.

## 3. Research Findings (Google official docs, retrieved 2026-07-19)

Source pages: `developers.google.com/workspace/drive/api/guides/about-changes`,
`.../guides/push`, `.../guides/limits`, `.../reference/rest/v3/changes/*`.

1. **Feed coverage fits our setup.** A user's change log includes files
   shared directly with that user. Our ingestion service account (the pilot
   root is shared to it) therefore sees change entries for the pilot corpus
   in its own change log. Folder-scope pilots need no shared-drive log; a
   future `shared_drive` scope must poll the drive's own change log with
   `driveId` (a member's user log does NOT reliably cover drive items).
2. **Start page tokens do not expire.** Persist one token per connection;
   after downtime, resume and replay. No re-baselining logic needed.
3. **Change entries are state snapshots, not deltas.** Google's prescribed
   pattern is to persist prior state and compare — we already store
   `modified_time`, `drive_md5_checksum`, `content_hash` per document.
4. **Inherited permission changes emit NO per-child events.** A permission
   change on a folder appears only on that item; clients must fan out to
   descendants themselves. Our `parent_folder_ids` data supports this.
5. **No guarantee every permission-only change reaches every sharee's log**
   (docs hedge with "may see change events ... based on usage"), and our
   service account cannot read ACLs at all (live-confirmed
   `403 insufficientFilePermissions`). Therefore the feed can only ever be
   an accelerator; the periodic per-user visibility sweep remains the SLA
   guarantee. This is a hard design constraint, not a preference.
6. **Quota is trivial.** `changes.list` = 100 units/call (list action);
   5-minute polling ≈ 28,800 units/day/connection. Limits: 1M units/min per
   project, 325k/min per user, 400M/day billing threshold. Existing 5/10
   plan-doc math (files.get = 5 units, 14.4M/day ≈ 3.6%) matches the
   official per-method table. Note for Phase 8 handoff: Google states quota
   overage billing begins later in 2026.
7. **Push channels are production-only optional.** Webhook must be public
   HTTPS with a CA-signed cert (self-signed rejected); channels expire with
   no auto-renewal (re-create with overlap); the `changes` notification is
   an empty ping — `changes.list` is still required. Impossible on
   localhost; polling stays primary everywhere.

## 4. Design Principles (carry over from Phases 4–6, non-negotiable)

- SpiceDB before any LLM context; the change feed never becomes an
  authorization signal — it only schedules the existing verified pipelines.
- Fail closed: feed errors, token problems, or classification uncertainty
  degrade to the periodic reconciliation that already exists.
- Provenance or exclusion, including the new content-currency predicate:
  chunks without a matching current content version are excluded, never
  grandfathered.
- No secrets, real emails, Drive IDs, document text, or question text in
  logs, task results, or tracked evidence records (pass/fail only;
  synthetic canary values are referenced, not quoted).
- Every new periodic task gets a stale-run sweep or lock discipline
  consistent with the existing tasks in `integrations/tasks.py`.

## 5. Work Packages

### WP1 — Freshness Monitoring And Pre-Expiry Alerting (build first)

Goal: an operator learns about a failed or delayed synchronization *before*
evidence expires; tightening intervals becomes safe.

Design:
- New periodic Celery task (beat, ~60s) computes per active per-user
  authorization and per connection, from existing tables only:
  last successful visibility-run age, running/queued backlog, consecutive
  failures, unknown-result counts, and minimum remaining evidence lifetime.
- Thresholds from settings: warn when a user's evidence will expire within
  a configured fraction (default 40% remaining) with no fresh successful
  run; error when expired or when the scheduler heartbeat itself is stale.
- Expose two consumers:
  1. `GET /api/health/freshness/` (staff/monitoring authenticated, no
     identities in the body — counts and worst-case ages only) so a selected
     external monitoring service can poll and alert on non-200.
  2. Structured warning/error logs (class-name/count discipline as
     everywhere else).
- Persist a small heartbeat row per scheduler tick so "beat is dead" is
  itself detectable by the health endpoint (age of last tick).

Tasks:
1. Settings + validators for thresholds (warn fraction, heartbeat max age).
2. Aggregation service + tests (pure queryset logic, no new state besides
   the heartbeat row and its migration).
3. Health endpoint + authentication + tests (no identity leakage).
4. Beat wiring + stale-heartbeat coverage in the sweep tests.
5. Deliberate-failure test: stop beat/worker in a live check, assert the
   endpoint degrades and retrieval still fails closed at expiry.
6. Vendor-neutral monitor contract documented (infra README note).

Acceptance mapping: tracker "Monitor scheduler heartbeat ... alert before
the 10-minute fail-closed deadline" and validation bullet "deliberately
failed or delayed refresh raises an alert before evidence expires".
Effort: ~1 day.

### WP2 — Drive Change Feed Polling And Classification

Goal: content edits and sharing changes propagate in near-real-time instead
of waiting for the next full sweep; full sweeps remain the guarantee.

Design:
- New model: per-connection change cursor (`start_page_token`, last poll
  time, last result). Baseline via `changes.getStartPageToken` at enable
  time; never re-baseline implicitly.
- New periodic task (beat, configurable, default 60–120s) calls
  `changes.list` with the stored token as the ingestion identity (ADC/SA),
  `includeRemoved=true`, fields limited to id/fileId/removed/time + file
  `md5Checksum`, `modifiedTime`, `trashed`, `parents`.
- Classification per change entry, against stored `SourceDocument` state:
  - Unknown fileId (not indexed, not a known folder) → ignore (scope is
    fixed by the pilot root; discovery stays with the metadata sweep).
  - `removed`/`trashed` → existing deactivate/evidence-invalidate path.
  - Known file, `md5Checksum`/`modifiedTime` moved → queue the existing
    single-document content refresh + extraction (reuse the exact code path
    `sync_drive_metadata` uses, narrowed to one file).
  - Known file, metadata unchanged or indeterminate → treat as possible
    permission change: queue the existing bounded per-user visibility
    refresh for connected users on that document (per-user mode) and/or
    the connection permission sync (delegated mode).
  - Known folder → fan out: schedule visibility/permission refresh for all
    indexed descendants (walk stored `parent_folder_ids`), bounded by the
    existing user x document caps.
- Feed failure of any kind logs class-name only and leaves periodic
  reconciliation untouched. A poisoned/failing cursor marks the cursor row
  degraded and alerts via WP1 rather than blocking anything.
- Explicit non-goal: push channels (`changes.watch`). Documented as a
  production-only latency optimization behind the existing Traefik HTTPS
  boundary; not built in this phase unless the pilot demands it.

Tasks:
1. Cursor model + migration + admin-safe repr.
2. Client method for `getStartPageToken`/`changes.list` with the existing
   retry/backoff discipline and metadata-only fields.
3. Classification service, pure function over (change entry, stored state)
   → action set; exhaustive unit tests including tombstones, unknown IDs,
   Google-native files without md5 (fall back to `modifiedTime`), folders.
4. Poll task + beat wiring + per-connection lock + stale-run sweep.
5. Fan-out scheduling with cap enforcement and dedup (a burst of changes
   to one file must coalesce into one refresh).
6. Integration tests: planted change entries drive exactly the expected
   downstream task calls and nothing else; feed outage leaves periodic
   behavior identical.
7. Live validation with the pilot: edit a document → content re-extracted;
   share/unshare → allowlist updated on next visibility run without
   waiting for the 15-minute sweep.

Acceptance mapping: tracker "change feed polling", "change-triggered
synchronization", "separate content from permission-only changes",
"re-index changed content", "Shared Drive ... inherited folder permission
changes". Effort: ~2 days (fan-out and coalescing are the risky parts).

### WP3 — Tighten To The 5/10 Production Target

Goal: refresh connected users every 5 minutes, expire positive evidence at
10 minutes — only after WP1 alerts exist.

Tasks:
1. Flip `GOOGLE_USER_VISIBILITY_SYNC_INTERVAL_SECONDS` to 300 and the
   evidence max age to 600 in the runtime env (settings validator already
   enforces expiry > interval; confirm the WP1 thresholds scale).
2. Load check at pilot caps against the vendor quota table (expected ≈
   3.6% of the daily threshold at 10 users × 1,000 documents; pilot scale
   is far below).
3. Live soak (≥ 1 hour): record actual propagation times for share, unshare,
   and content edit; verify no 403/429 from Google.
4. Live fail-closed checks: scheduler stopped → alert fires (WP1) →
   evidence expires at 10 minutes → chat refuses; restart → recovery
   without manual steps.
5. Update the brief/plan docs to record the executed target; keep rollback
   documented (raise intervals — never extend already-issued evidence).

Acceptance mapping: tracker "Configure and load-test the bounded production
target" and the two freshness validation bullets. Effort: ~0.5 day + soak.

### WP4 — Content-Currency Retrieval Gate (closes issue #5, independent)

Goal: chunks from a superseded content version cannot reach answer context
even while re-extraction is pending or failed.

Design:
- Graph writes already stamp chunks/documents with provenance; extend the
  post-Neo4j recheck so context assembly requires the graph document's
  content version to equal the current `SourceDocument.content_hash`
  (single additional predicate beside `fresh_authorized_documents`).
- Prefer the PostgreSQL-side comparison (fetch candidate documents' current
  hashes and drop mismatches) over widening Cypher — keeps the guard in the
  same place as the permission recheck and stays fail-closed for chunks
  whose stored version is empty or missing.

Tasks:
1. Confirm the exact provenance field carrying content version on graph
   nodes (extraction writes it; verify name/coverage before designing the
   comparison).
2. Implement the predicate in `retrieval/services.py` context assembly.
3. Tests: stale-version chunks excluded; matching chunks pass; empty or
   missing version excluded; refusal (not error) when everything is stale.
4. Journal in the issue and close #5 on merge.

Effort: ~0.5 day. May be done first as a warm-up — no dependency on WP1–3.

### WP5 — Evaluation Dataset And Scheduled Runner (parallel track)

Goal: repeatable, scheduled proof of answer quality and leak safety.

Design:
- Fill `data/eval/` (already scaffolded) with the real pilot set: ~20
  positive questions with expected source titles, refusal counterparts
  keyed to the two pilot identities, users file mapping identities to
  expected visibility. **Requires the operator/client to author questions
  against the actual pilot documents** — code cannot invent these.
- Management command `run_evaluation`: iterates the set, calls
  `answer_query(question, user_email)` through the real path (no bypass),
  and scores: expected citation present; forbidden source absent from
  answer AND citations (leak = hard fail); refusal cases actually refuse.
- Persisted result rows keep pass/fail, counts, and timings only — no
  question text, no answer text, no identities (evidence policy).
- Scheduled Celery task runs the suite (default daily) and surfaces
  failures through the WP1 health endpoint (an eval-failed flag).

Tasks:
1. Loader + schema validation for the YAML set (reject unknown fields,
   consistent with serializer discipline).
2. Runner command + scoring + persistence model/migration.
3. Leak assertions as hard failures distinct from quality scores.
4. Beat wiring + result surfacing in the WP1 endpoint.
5. Operator step: author the real question set with the client; keep
   `*.example.yaml` tracked, real set ignored if it embeds document facts.

Acceptance mapping: tracker "evaluation question set", "answer quality
tests", "mandatory leak tests", "scheduled evaluation task", and the two
evaluation validation bullets. Effort: ~1 day code + operator authoring.

## 6. Sequencing

1. WP4 (warm-up, closes #5) — or fold into the WP2 branch if preferred.
2. WP1 monitoring → WP2 change feed → WP3 tighten. Strict order.
3. WP5 parallel to any of the above; its only gate is question authoring.

Each WP is its own branch/PR onto `main` (squash merges — linear history is
enforced). Suggested branches: `codex/phase-7-wp1-freshness-monitoring`,
`codex/phase-7-wp2-change-feed`, etc.

## 7. Risks

- **Feed does not carry a hoped-for event** (permission-only change
  invisible to the SA's log): accepted by design — the periodic sweep is
  the guarantee; WP3's soak must measure propagation with the feed
  disabled too, so the SLA claim never depends on the feed.
- **Fan-out burst** (folder reshared over a large tree): coalescing +
  existing caps bound the work; worst case equals one periodic sweep.
- **Google-native files have no md5**: classification falls back to
  `modifiedTime`; a moved timestamp with unchanged bytes causes one
  harmless re-export (hash comparison then short-circuits extraction).
- **Quota**: vendor-verified trivial at pilot scale; WP3 records real
  numbers; note 2026 overage billing in Phase 8 handoff docs.

## 8. Out Of Scope (Phase 7)

- Push notification channels (documented production option only).
- Enterprise dashboards; the WP1 endpoint plus external alert delivery is the
  POC bar.
- Usage/cost ledger (issue #2, later phase).
- Multi-connection or shared-drive-scope generalization beyond keeping the
  cursor model per-connection.

## 9. Working Rules For The Implementing Session

- Read `AGENTS.md` first; manual audit only; daily report before the final
  commit of each session (`docs/daily-reports/YYYY-MM-DD.md`).
- Local `.venv` is broken and `uv` is absent: run all validation in Docker:
  `docker run --rm -v <repo>:/repo -w /repo/apps/backend -e DJANGO_DEBUG=true
  -e DJANGO_SECRET_KEY=$(python3 -c "import secrets;
  print(secrets.token_urlsafe(64))") -e RUFF_CACHE_DIR=/tmp/ruff
  knowledge-graph-infra-django:latest sh -c "python -m pytest -q && ruff
  check . && python manage.py makemigrations --check --dry-run"`.
  Baseline: 449 passed on the Phase 6 branch.
- Pre-commit hook: strict two-stage gate. Known false positive: the secret
  scanner matches the `CSRF_TOKEN` documentation placeholder (the literal
  `<matching-csrf-token>` example value) in `docs/drive-delegation-setup.md`
  whenever that content appears in a staged diff; the operator has approved
  overriding exactly that finding (document the override in the commit
  message when used).
- Evidence records: pass/fail only; never real emails, Drive IDs, tokens,
  question text, or document text.
