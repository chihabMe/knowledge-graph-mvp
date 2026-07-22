# Phase 8: Deployment Handoff

## Purpose

Make the POC understandable, maintainable, recoverable, and reusable for future client implementations.

## Scope

- Deployment docs.
- Backup docs.
- Restore docs.
- Maintenance checklist.
- Permission-freshness SLA and synchronization runbook.
- Chat-history deletion and retention policy.
- Demo script.
- Client handoff notes.

## Out Of Scope

- Building paid SaaS billing.
- Multi-tenant management console.

## Tasks

- [ ] Write deployment guide. Effort: Medium.
- [ ] Document only the supported POC onboarding: the administrator selects the
  company root, then every pilot user connects Google once. Do not include
  delegated ACL/domain-wide delegation as an operator option. Effort: Low.
- [ ] Write backup guide. Effort: High.
- [ ] Write restore guide. Effort: High.
- [ ] Write maintenance checklist. Effort: Medium.
- [ ] Document and demonstrate the deployed 15-minute refresh/30-minute
  evidence-expiry POC behavior, freshness inspection, incident response, and
  safe recovery. Keep 5/10 timing and external alert delivery in Phase 9.
  Effort: High.
- [ ] Agree and document a client-approved chat-history deletion/retention
  policy, including user/admin deletion, account removal, backups, and the fact
  that Drive revocation blocks future retrieval but does not retract previously
  delivered chat text. Start from a configurable 30-day pilot recommendation,
  subject to client/legal requirements. Effort: High.
- [ ] Write demo script. Effort: Medium.
- [ ] Add troubleshooting guide. Effort: Medium.
- [ ] Verify clean-server setup path. Effort: High.
- [ ] Run `run_evaluation` against operator-owned real client questions and
  allowed/denied leak cases. Keep fixtures ignored and do not persist output.
  Effort: High.
- [ ] Run a live document-edit drill: confirm the changed document is refused
  while re-extraction is pending, then is available again only after success.
  Effort: High.
- [ ] Run a controlled Celery-worker crash/recovery drill for a running graph
  extraction and confirm stale-run recovery cannot leave it stuck. Effort: High.
- [ ] Run a several-hour scheduled-sync soak test and record queue, retry,
  freshness, and resource observations. Effort: Medium.
- [ ] Produce a concise Phase 7 evidence report covering tests, live timings,
  known limits, and the four assurance checks above. Effort: Medium.

## Coolify Deployment Build Plan (sequenced)

Chosen delivery model: **repeatable, isolated per-client instances on Coolify**
(tool decision made 2026-07-22 after a full Coolify-vs-Dokploy comparison),
one published image deployed N times, differing only by env/secrets/volumes/
domains. Pilots may co-tenant on one host; production leans one-VM-per-client
(cap ~8-10/host for blast radius). The app is already deployment-shaped
(non-root `prod` image, env-driven public URLs, fail-closed `settings_validators`,
CI quality gate); the work below is the delivery pipeline and per-client
scaffolding around it, not app-internal changes.

**Guiding principle - authoritative core, Coolify as convenience layer.** The
deployment's source of truth is the **Compose file + a `provision-client` script
+ a per-client `clients/<slug>.env`** - Coolify only *calls* that. This keeps the
tool choice reversible (switch tools, or drop to pure CLI for a client who owns
their own deployment under Scenario C), keeps `deploy.resources.limits` in the
YAML rather than Coolify's buggy dashboard panel, and makes onboarding a new
client a single parameterized command. Replicability - a new client in minutes
from one script plus one guided Google checklist - is a first-class goal, not a
side effect.

Order matters: M0 first, then M1->M6. Where an item overlaps an existing task
above (backup/restore guide, clean-server verify, `run_evaluation`), this plan is
the technical build and that task is its written deliverable.

### M0 - De-risk (do first)

- [ ] Confirm Coolify honors `deploy.resources.limits.memory` when set in the
  **compose YAML** (not the dashboard panel - that path is a known no-op, Coolify
  issue #10676). Research indicates the YAML path is reliable, so this is a quick
  verification rather than an open blocker. The caps (Neo4j 2G, Celery 1.5G,
  Django 1G) are what stop one client's extraction from OOM-ing a shared host; if
  they do not stick, co-tenancy is unsafe -> use one-VM-per-client mode.
  Effort: Low.

### M1 - Publishable image (foundation)

- [ ] Add a GHCR publish workflow separate from `ci.yml`: build the existing
  `prod` target, scan (Trivy, fail on high/critical), tag `:sha-<gitsha>` +
  `:vX.Y.Z`, push. `ci.yml` stays the quality gate. Effort: Medium.
- [ ] Digest-pin the third-party images (neo4j, postgres, spicedb, open-webui,
  redis, traefik) so a re-pull cannot silently change a base under a client.
  Effort: Low.

### M2 - Deploy-time execution

- [ ] Add a release/entrypoint mechanism that runs the ordered init as a Coolify
  pre-deploy/release command (there is no `make` under Coolify): Postgres healthy
  -> `spicedb datastore migrate head` -> `spicedb_schema_apply` -> `django
  migrate --noinput` -> app start. `django migrate` currently exists only in the
  Makefile. Effort: Medium.
- [ ] Author a Coolify compose overlay that drops the app's own Traefik + Dozzle
  (they collide with Coolify's proxy on :80/:443), exposes Open WebUI at
  `clientN.<domain>` and Django at `api.clientN.<domain>` via Coolify domains,
  and keeps `kg-private` internal. The dev/prod compose stays intact for local
  use. Effort: Medium.
- [ ] Build the `provision-client <slug>` script - the single entry point the
  rest of the plan plugs into: read `clients/<slug>.env`, generate secrets (M3),
  create/update the Coolify project (via its API) from the pinned image digest +
  overlay, and trigger the ordered release (M2 above). This is the automation
  spine that makes onboarding one command. Effort: Medium.

### M3 - Per-client provisioning

- [ ] Build a per-client secret generator: unique values for every secret, with
  the token-encryption key guaranteed distinct from the Django, Open WebUI,
  identity-JWT, and service-bearer secrets. Effort: Medium.
- [ ] Audit and extend `settings_validators` so startup fails closed on ANY
  remaining placeholder secret or any secret duplicated across roles (not just
  the SpiceDB/onboarding cases already covered). Effort: Low.
- [ ] Define the `clients/<slug>.env` convention as the single source of
  per-client truth (the only file that varies between clients): the two domains,
  both OAuth redirect URIs (must match `api.clientN.<domain>`), Workspace domain,
  GCP project, and OpenRouter key. Generated secrets are derived, never
  hand-edited here. Effort: Low.

### M4 - Backups and recovery (S3)

- [ ] Build the scheduled backup job -> S3-compatible bucket (prefer Hetzner
  Object Storage or Cloudflare R2 over AWS for restore-egress): one `pg_dump`
  (covers both Django tables and SpiceDB relationships - same database) +
  `neo4j-admin database dump` (Community is offline-only: brief nightly quiesce
  window) + Open WebUI volume. Encrypt before upload; set retention. This is the
  build behind the "Write backup guide" task. Effort: High.
- [ ] Write and test the restore path (a backup never restored is a guess). This
  is the build behind the "Write restore guide" task. Effort: High.

### M5 - Observability

- [ ] Wire `/api/health/` and the freshness endpoint into Coolify's healthcheck
  plus an external Uptime Kuma; alert before the evidence-expiry deadline, not
  after access has failed closed. Effort: Medium.

### M6 - Provisioning runbook and Definition of Done

- [ ] Write the end-to-end new-client provisioning runbook: DNS -> client Google
  setup (consent + service account + admin app approval + redirect URIs) ->
  generate secrets -> Coolify project from pinned digest + overlay -> ordered
  release -> Drive-root selection + per-user onboarding -> enable backup +
  monitoring. Effort: Medium.
- [ ] Handoff gate: run the `run_evaluation` leak fixtures against each
  provisioned client stack before handoff - proof the permission-safety
  invariants survived the new deploy path. The one thing that must never
  regress. Effort: High.

## Validation

- [ ] A fresh VM can follow the docs.
- [ ] Backups include PostgreSQL, Neo4j, SpiceDB datastore, Open WebUI config, and environment config guidance.
- [ ] Restore path is documented.
- [ ] Operators can inspect a failed or delayed permission/content sync and
  follow the documented recovery procedure.
- [ ] The approved chat-history retention period and deletion responsibilities
  are configured, tested, and included in client handoff.
- [ ] Demo script shows permission-safe retrieval behavior.
- [ ] Clean-server startup rejects delegated ACL configuration and exposes only
  per-user OAuth onboarding/visibility workflows.
- [ ] The client handoff includes the Phase 7 evidence report and the results
  of the real-fixture, edit, worker-recovery, and soak checks.

## Completion Status

Active — planning locked. Delivery model chosen (Coolify per-client instances)
and the sequenced build plan (M0–M6) is defined above. No implementation started;
M0 (verify Coolify honors resource limits) is the gating first step.
