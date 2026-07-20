# Phase 7: POC Freshness And Evaluation

## Purpose

Keep the bounded POC corpus current with simple periodic reconciliation, make
staleness visible, and provide repeatable operator-run leak evaluation without
adding production-only infrastructure.

## Scope

- Periodic full Drive content reconciliation every 15 minutes.
- Existing periodic per-user permission reconciliation every 15 minutes with
  30-minute fail-closed positive-evidence expiry.
- Incremental re-indexing inside each sweep and a retrieval content-currency
  gate while extraction is pending or failed.
- Authenticated identity-free freshness health plus structured logs.
- Operator-run evaluation from ignored private YAML fixtures.
- One live fail-closed drill before closeout.

## Out Of Scope

- Uptime Kuma or another embedded monitoring container.
- External alert delivery; select and validate this before production.
- Drive change-feed polling, push/webhook channels, and event coalescing.
- The 5-minute refresh/10-minute evidence-expiry production target.
- Scheduled evaluation, evaluation APIs, and persisted evaluation records.
- Shared Drive change-log fan-out. Periodic per-user checks remain authoritative.

The deferred production items are tracked in
`phase-9-production-hardening.md`; they are not Phase 7 blockers.

## Tasks

- [x] Re-index changed content and skip unchanged content during a full Drive
  sweep. Effort: High.
- [x] Refresh permissions without re-embedding. Effort: Extra High.
- [x] Gate retrieval on the current extracted content version. Effort: Extra
  High.
- [x] Schedule safe Drive content reconciliation every 15 minutes, with a
  per-connection lock, durable run reuse, retry handling, and stale-run
  recovery. Effort: High. (Live Beat dispatch and a three-document sweep
  passed.)
- [x] Preserve the per-user coarse content gate for unchanged, successfully
  extracted content while closing it for changed or indeterminate content.
  Effort: Extra High. (All three unchanged extracted documents remained
  eligible and no extraction job was queued in the live sweep.)
- [x] Skip non-authoritative `permissions.list` calls during per-user content
  ingestion. Effort: High. (Implemented, tested, and used by the live sweep.)
- [x] Report content-sync age, failures, and overdue state through the existing
  identity-free freshness endpoint and structured logs. Effort: High.
  (Live stale-to-healthy and outage/recovery transitions passed.)
- [x] Add a strict operator-run evaluation command for ignored private YAML
  fixtures, with positive-answer/citation checks and mandatory allowed/denied
  leak cases. Effort: High. (Installed in the live container and covered by
  synthetic privacy/output tests; real client fixtures remain operator-owned.)

## Validation

- [x] Django, Celery worker, and Celery Beat are healthy after deployment.
- [x] `/api/health/freshness/` returns 200/`status: ok` with the content-sync
  fields present.
- [x] A scheduled content sweep completes and unchanged live content remains
  retrievable without unnecessary extraction.
- [x] The manual fail-closed drill passes and records timings; no external
  alert receiver is required for this POC drill.
- [x] The operator evaluation command produces useful privacy-safe pass/fail
  output against synthetic fixtures; the ignored private client fixture was
  not present and is an operator handoff input, not a code blocker.

## Completion Status

Complete (2026-07-20). The live three-document content sweep succeeded without
unnecessary extraction. During the Beat outage, freshness changed to error 192
seconds after shutdown, evidence expired fail-closed, retrieval returned a
controlled refusal with zero citations, and Beat restart restored health and
retrieval automatically. Production-only optimizations remain deferred.
