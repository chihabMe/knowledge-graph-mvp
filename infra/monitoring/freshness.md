# Permission Freshness Monitoring Contract

Uptime Kuma is not part of this deployment. The POC uses this endpoint and
structured logs for manual operations. Before enabling the optional Phase 9
5-minute refresh/10-minute evidence-expiry target, select an external monitoring
service that can poll the private Compose network or the protected Django route
and deliver notifications independently of Celery.

Configure two 60-second HTTP checks against:

```text
http://django:8000/api/health/freshness/
```

Both checks must send the same untracked deployment secret configured for
Django:

```text
Authorization: Bearer <FRESHNESS_MONITOR_BEARER_KEY>
```

## Status-code notification

Treat HTTP 200 as healthy and any non-200 response as down. Warning and error
states deliberately return HTTP 503, so route this check to a softer
notification channel.

## Error paging

Inspect the compact JSON response body and page when it contains exactly:

```text
"status":"error"
```

Warning responses contain `"status":"warn"` and must not trigger this paging
check. Error responses cover expired targets or evidence, overdue content
synchronization, stale scheduler or worker heartbeat, and aggregation failures.

The response contains aggregate counts and worst-case ages only; it never
contains user or Drive identities. Keep the default 60-second check interval,
40% remaining-evidence warning threshold, and 180-second heartbeat maximum.
Do not tighten the evidence lifetime until the live stopped-scheduler drill in
`docs/runbooks/freshness-drill.md` proves both notifications arrive before
evidence expires.
