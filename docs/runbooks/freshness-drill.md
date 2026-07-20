# Runbook: Freshness Fail-Closed Drill

Purpose: prove, against the live stack, that a dead scheduler raises an alert
*before* permission evidence expires, that retrieval fails closed at expiry,
and that recovery needs no manual steps. Phase 7 WP1 does not close until this
drill passes, and the 5-minute/10-minute production target (WP3) must not be
enabled before that.

Evidence policy applies: record pass/fail, timestamps, and durations only —
never identities, Drive IDs, tokens, question text, or document text.

## Preconditions

- Full stack up (`make up`), Uptime Kuma running with **both** freshness
  monitors from `infra/uptime-kuma/monitors.md` (status-code monitor and the
  `"status":"error"` keyword paging monitor), each with a working
  notification channel.
- `FRESHNESS_MONITOR_BEARER_KEY` configured in Django and in both monitors.
- At least one connected pilot identity with verified-visible evidence and a
  question known to answer successfully in Open WebUI.
- Note the active values of `FRESHNESS_HEARTBEAT_MAX_AGE_SECONDS` (default
  180) and the mode's evidence max age (`GOOGLE_USER_VISIBILITY_MAX_AGE_SECONDS`
  or `PERMISSION_VERIFICATION_MAX_AGE_SECONDS`).

## Drill

1. **Baseline.** `GET /api/health/freshness/` with the bearer key returns 200
   with `"status":"ok"`; both Kuma monitors green. Record the time.
2. **Kill the scheduler.** `docker stop` the celery-beat container (leave the
   worker running — this simulates the silent failure mode). Record the time.
3. **Heartbeat alert.** Within `FRESHNESS_HEARTBEAT_MAX_AGE_SECONDS` plus one
   monitor interval, the endpoint must return 503 with `"status":"error"` and
   a growing `heartbeat_age_seconds`, and **both** Kuma monitors must alert.
   Record the alert arrival time. This must be well before the evidence max
   age elapses — that margin is the whole point.
4. **Evidence expiry still denies.** Wait until the evidence max age has
   passed since the last successful sync, then ask the known-good question in
   Open WebUI as the pilot identity. The answer must be a refusal, not stale
   content. Record pass/fail.
5. **Recovery.** `docker start` the beat container. Within one sync interval
   plus one monitor interval the endpoint must return to 200/`"status":"ok"`
   and chat must answer again, with no manual database or cache intervention.
   Record the recovery time.
6. **Optional worker variant.** Repeat steps 2–5 stopping the celery worker
   instead of beat: heartbeat also goes stale (the monitor task cannot run),
   and the same alerts and refusals must occur.

## Exit criteria

- Alert fired at least several minutes before evidence expiry (record actual
  margin).
- Chat refused after expiry during the outage.
- Recovery was automatic after restart.
- Results journalled (pass/fail + timings) in the daily report, and the
  Phase 7 tracker items marked `[~]` for live alert delivery flipped to `[x]`.

If any step fails, WP1 stays open: fix, then rerun the full drill from step 1.
