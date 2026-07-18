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
- [ ] Write backup guide. Effort: High.
- [ ] Write restore guide. Effort: High.
- [ ] Write maintenance checklist. Effort: Medium.
- [ ] Document and demonstrate the 5-minute refresh/10-minute evidence-expiry
  SLA, monitoring thresholds, incident response, and safe rollback to a longer
  refresh interval without extending stale-evidence authorization. Effort:
  High.
- [ ] Agree and document a client-approved chat-history deletion/retention
  policy, including user/admin deletion, account removal, backups, and the fact
  that Drive revocation blocks future retrieval but does not retract previously
  delivered chat text. Start from a configurable 30-day pilot recommendation,
  subject to client/legal requirements. Effort: High.
- [ ] Write demo script. Effort: Medium.
- [ ] Add troubleshooting guide. Effort: Medium.
- [ ] Verify clean-server setup path. Effort: High.

## Validation

- [ ] A fresh VM can follow the docs.
- [ ] Backups include PostgreSQL, Neo4j, SpiceDB datastore, Open WebUI config, and environment config guidance.
- [ ] Restore path is documented.
- [ ] Operators can detect and respond to a failed or delayed permission sync
  before the 10-minute evidence-expiry boundary.
- [ ] The approved chat-history retention period and deletion responsibilities
  are configured, tested, and included in client handoff.
- [ ] Demo script shows permission-safe retrieval behavior.

## Completion Status

Not started.
