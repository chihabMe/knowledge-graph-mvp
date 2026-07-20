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

Not started.
