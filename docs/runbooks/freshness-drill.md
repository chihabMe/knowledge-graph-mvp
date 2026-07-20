# Runbook: Freshness Fail-Closed Drill

Purpose: prove, against the live stack, that a dead scheduler becomes visible
through the authenticated freshness endpoint before permission evidence
expires, that retrieval fails closed at expiry, and that recovery needs no
manual database or cache intervention. External alert delivery is deferred to
Phase 9 and is not part of this POC drill.

Evidence policy applies: record pass/fail, timestamps, and durations only —
never identities, Drive IDs, tokens, question text, or document text.

## Preconditions

- Full stack up (`make up`).
- `FRESHNESS_MONITOR_BEARER_KEY` configured in Django for manual endpoint
  polling.
- At least one connected pilot identity with verified-visible evidence and a
  question known to answer successfully in Open WebUI.
- Note the active values of `FRESHNESS_HEARTBEAT_MAX_AGE_SECONDS` (default
  180) and the mode's evidence max age (`GOOGLE_USER_VISIBILITY_MAX_AGE_SECONDS`
  or `PERMISSION_VERIFICATION_MAX_AGE_SECONDS`).

## Drill

1. **Baseline.** `GET /api/health/freshness/` with the bearer key returns 200
   with `"status":"ok"`. Record the time.
2. **Kill the scheduler.** Stop the Celery Beat container (leave the worker
   running — this simulates the silent failure mode). Record the time.
3. **Heartbeat failure becomes visible.** Poll manually. Within
   `FRESHNESS_HEARTBEAT_MAX_AGE_SECONDS` plus one monitor interval, the endpoint
   must return 503 with `"status":"error"` and a growing
   `heartbeat_age_seconds`. Record the transition time and its margin before
   evidence expiry.
4. **Evidence expiry still denies.** Wait until the evidence max age has passed
   since the last successful sync, then ask the known-good question in Open
   WebUI as the pilot identity. The answer must be a refusal, not stale content.
   Record pass/fail.
5. **Recovery.** Start the Beat container. Within one sync interval plus one
   monitor interval the endpoint must return to 200/`"status":"ok"` and chat
   must answer again, with no manual database or cache intervention. Record the
   recovery time.
6. **Optional worker variant.** Repeat steps 2–5 stopping the Celery worker
   instead of Beat: heartbeat also goes stale, and the same endpoint transition
   and refusals must occur.

## Exit Criteria

- The endpoint exposed scheduler failure before evidence expiry; record the
  actual margin.
- Chat refused after expiry during the outage.
- Recovery was automatic after restart.
- Results are journalled as pass/fail plus timings in the daily report, and the
  Phase 7 live-validation items are marked complete.

If any step fails, Phase 7 stays open: fix, then rerun the full drill from step
1.
