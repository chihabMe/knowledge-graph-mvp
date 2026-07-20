# Phase 7: Change Feed And Evaluation

## Purpose

Keep graph data and permissions current, then prove answer quality and leak safety with repeatable tests.

## Scope

- Google Drive change feed.
- Incremental content re-indexing.
- Permission-only sync.
- Production permission-freshness controls.
- Failed and delayed synchronization monitoring.
- Evaluation dataset.
- Leak tests.
- Scheduled evaluation jobs.

## Out Of Scope

- Enterprise monitoring dashboards.
- Multi-customer shared deployment.

## Tasks

- [ ] Implement Drive change feed polling. Effort: High.
- [ ] Add change-triggered synchronization using Drive change-feed and push
  signals where possible, while retaining periodic reconciliation. Effort:
  Extra High.
- [ ] Separate content changes from permission-only changes. Effort: Extra High.
- [ ] Re-index changed content. Effort: High.
- [ ] Refresh permissions without re-embedding. Effort: Extra High.
- [ ] Configure and load-test the bounded production target: refresh connected
  users every 5 minutes and expire positive evidence after 10 minutes. Keep the
  current 15-minute/30-minute POC values until monitoring is ready. Effort:
  High.
- [~] Monitor scheduler heartbeat, last-success age, run duration, backlog,
  errors, unknown results, and evidence approaching expiry; alert before the
  10-minute fail-closed deadline. Effort: High. (Code and offline failure
  simulation complete on the WP1 branch; live Uptime Kuma alert delivery with
  a deliberately stopped scheduler remains.)
- [ ] Reconcile Shared Drive logs and inherited folder permission changes that
  do not map cleanly to one child change event. Effort: Extra High.
- [ ] Create evaluation question set. Effort: Medium.
- [ ] Add answer quality tests. Effort: High.
- [ ] Add mandatory leak tests. Effort: Extra High.
- [ ] Add scheduled evaluation task. Effort: High.

## Validation

- [ ] Edited files update graph content.
- [ ] Permission changes update SpiceDB.
- [ ] Permission additions/removals normally affect new chat retrieval within
  5 minutes under the configured pilot caps.
- [ ] Stale positive evidence is unusable after 10 minutes, including during
  scheduler, Drive API, or worker failure.
- [~] A deliberately failed or delayed refresh raises an alert before evidence
  expires. (Offline stale-heartbeat/expired-evidence coverage passes; live
  alert delivery remains.)
- [ ] Push/change-triggered synchronization reduces normal propagation time,
  and periodic reconciliation repairs missed or expired notifications.
- [ ] Restricted answers fail leak tests.
- [ ] Evaluation runner produces useful pass/fail output.

## Completion Status

In progress. WP4's content-currency gate is complete. WP1 freshness-monitoring
code and offline tests are complete; live alert delivery remains before WP1
closeout. The 5-minute refresh/10-minute evidence-expiry target has not been
enabled.
