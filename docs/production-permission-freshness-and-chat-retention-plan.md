# Production Permission Freshness And Chat Retention Plan

**Status:** Approved implementation target; not yet enabled

**Current POC configuration:** refresh every 15 minutes; evidence expires after
30 minutes

**Recommended bounded production-pilot target:** refresh every 5 minutes;
evidence expires after 10 minutes

## Executive Decision

The 5-minute refresh and 10-minute evidence-expiry values are appropriate for
the current single-client pilot caps, provided synchronization monitoring is
implemented before the tighter values are enabled.

This target means:

- A normal permission addition or removal should affect new chat retrieval in
  0-5 minutes.
- One missed scheduled refresh still leaves another opportunity to recover.
- After 10 minutes without fresh positive evidence, retrieval fails closed.
- Revocation prevents future retrieval; it does not erase answers already
  delivered into Open WebUI chat history.

Ten minutes is intentionally a security boundary, not a promise that every
refresh will take ten minutes. It is also the minimum sensible two-cycle buffer
for a five-minute schedule. Alerting must therefore happen before the deadline.

## Capacity And Quota Validation

The present bounded configuration permits at most 10 connected users and 1,000
already-indexed documents per complete visibility sweep:

| Measure | Worst-case calculation | Result |
| --- | ---: | ---: |
| Metadata checks per sweep | 10 users x 1,000 documents | 10,000 |
| Sweeps per day | 24 x 60 / 5 | 288 |
| Metadata checks per day | 10,000 x 288 | 2,880,000 |
| `files.get` quota units per day | 2,880,000 x 5 | 14,400,000 |
| Share of published daily threshold | 14.4M / 400M | 3.6% |
| Average request rate during a continuously spread sweep | 10,000 / 300 sec | 33.3/sec |

Google currently documents `files.get` as five quota units and a project-level
daily threshold of 400 million quota units. The calculated worst case is below
that daily threshold, but production must still use bounded concurrency,
jitter, exponential backoff, and actual quota monitoring. Batch execution can
create short rate spikes even when the daily total is safe.

This validation applies only to the current caps and access-check design. The
timing must be recalculated before increasing the number of users, indexed
documents, tenants, or Drive API operations.

## Synchronization Design

### Periodic reconciliation

Run the complete bounded visibility refresh every five minutes. This remains
the authoritative repair mechanism and must:

1. Check only already-indexed document IDs as the connected user.
2. Reconcile direct per-user SpiceDB relationships.
3. Commit positive evidence only after causal verification succeeds.
4. Deny unknown, errored, missing, or expired evidence.
5. Avoid re-embedding when only permissions changed.

### Change-triggered acceleration

Add faster refreshes from Google Drive change-feed and push-notification signals
where possible. Treat these as acceleration signals, never as authorization
proof. After a signal, retrieve the authoritative Drive state and refresh only
the affected users/documents when that scope can be established safely.

Periodic reconciliation remains required because:

- Push notification channels expire and must be renewed.
- Notifications indicate that something changed but do not carry the complete
  authoritative state.
- Complete visibility can require both the user's change log and Shared Drive
  change logs.
- A removed change entry can mean deletion or loss of access.
- An inherited permission change on a parent does not necessarily produce a
  separate child change entry, so descendants may require reconciliation.

## Monitoring And Alerts

Before changing the runtime from 15/30 to 5/10, expose and alert on:

- Celery Beat/scheduler heartbeat.
- Age of the last successful visibility run per connected user.
- Run duration and queue wait time.
- Pending/backlogged users and documents.
- Success, denial, unknown, retry, and error counts.
- Drive quota/rate-limit responses and retry exhaustion.
- Evidence due to expire within the next refresh interval.
- Push-channel expiration/renewal and missed-change recovery.

Recommended alert thresholds:

| Condition | Response |
| --- | --- |
| No successful user refresh for 6 minutes | Warning and investigate |
| No successful user refresh for 8 minutes | Critical; evidence is near expiry |
| Evidence reaches 10 minutes | Expected fail-closed denial; page operator |
| Scheduler heartbeat missing for more than one interval | Critical |
| Refresh backlog cannot drain within one interval | Critical |

The application must never extend or reuse stale positive evidence merely to
avoid a user-visible refusal.

## Chat-History Deletion And Retention

Authorization freshness and chat retention solve different problems. When a
Drive share is removed, the backend prevents the next retrieval from using that
document after synchronization or evidence expiry. Text already returned in an
earlier chat may remain visible until the chat is deleted under the retention
policy.

Recommended pilot policy to take to the client:

- Default retention: 30 days, configurable per deployment and subject to the
  client's legal, security, and records requirements.
- Let users delete their own chats and let authorized administrators delete a
  user's chats for support, offboarding, or incident response.
- On account disablement, revoke active sessions immediately and apply the
  agreed delete-or-anonymize rule to chat history.
- Include chat data in backup retention and deletion schedules; deleting the
  primary row alone is insufficient if backups keep it indefinitely.
- Do not store OAuth tokens, document contents, or unrestricted prompts in
  analytics records.
- Clearly state that revoking Drive access does not retroactively retract text
  already delivered to a user.
- Do not promise automatic removal of every historical answer derived from one
  document unless a future answer-to-source deletion index is designed,
  implemented, and tested.

The client must approve the final period and deletion responsibilities before
production handoff. Thirty days is a starting recommendation, not a legal
conclusion.

## Phase Ownership

### Phase 7: Change Feed And Evaluation

- Implement change-triggered synchronization and periodic reconciliation.
- Add 5-minute refresh and 10-minute evidence-expiry configuration.
- Add failed/delayed synchronization metrics and alerts.
- Load-test the bounded limits and run addition/removal/failure acceptance
  tests.

### Phase 8: Deployment Handoff

- Document the permission-freshness SLA and operator incident runbook.
- Agree, configure, test, and hand off the chat-history retention/deletion
  policy.
- Demonstrate monitoring, evidence expiry, backup retention, and account
  offboarding behavior.

## Production Acceptance Criteria

- Permission additions/removals normally affect new retrieval within five
  minutes.
- Positive evidence older than ten minutes is always rejected.
- Scheduler, worker, Drive API, and notification failures fail closed.
- An operator is alerted before the ten-minute boundary.
- Missed or expired push signals are repaired by periodic reconciliation.
- Permission-only changes do not re-embed document content.
- Chat deletion, retention, account removal, and backup behavior match the
  client-approved policy.
- Acceptance tests prove that a revoked document cannot appear in new context,
  answers, citations, embeddings, or graph paths.

## References

- [Google Drive API usage limits](https://developers.google.com/workspace/drive/api/guides/limits)
- [Google Drive push notifications](https://developers.google.com/workspace/drive/api/guides/push)
- [Track changes for users and shared drives](https://developers.google.com/workspace/drive/api/guides/about-changes)
- [Retrieve changes](https://developers.google.com/workspace/drive/api/guides/manage-changes)
