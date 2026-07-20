# Uptime Kuma Monitor Targets

Create these monitors after the services are running.

## HTTP Monitors

- Open WebUI: `http://open-webui:8080`
- Django health: `http://django:8000/api/health/`
- Dozzle: `http://dozzle:8080`
- Traefik dashboard: `http://traefik:8080/dashboard/`

### Permission freshness

Create a separate HTTP monitor for
`http://django:8000/api/health/freshness/` with a 60-second heartbeat interval.
Add this request header using Uptime Kuma's header configuration:

```text
Authorization: Bearer <FRESHNESS_MONITOR_BEARER_KEY>
```

Use the same untracked secret configured for Django. A healthy response is
HTTP 200. Warning and error states deliberately return HTTP 503 so Uptime Kuma
alerts on approaching evidence expiry, failed or unknown synchronization,
expired evidence, or a stale Celery scheduler/worker heartbeat. The response
contains aggregate counts and worst-case ages only; it never contains user or
Drive identities.

This status-code monitor cannot tell warn from error — both are 503. Treat it
as the softer channel (notify, do not page) and add a second monitor for
paging:

#### Permission freshness — error paging

Create an additional monitor of type "HTTP(s) - Keyword" on the same URL with
the same bearer header and 60-second interval. Set the keyword to exactly
`"status":"error"` (DRF renders the JSON body without spaces) and configure
"monitor goes down when the keyword **is** found". Route this monitor to the
paging notification channel: it fires only on expired targets or evidence, a
stale scheduler heartbeat, or an aggregation failure (whose fail-closed body
also renders as `"status":"error"`). Warn states render as `"status":"warn"`
and never contain the keyword, so only the softer monitor reacts to them.

For the future 5-minute refresh/10-minute evidence-expiry target, keep the
default 60-second monitor interval, 40% remaining-evidence warning threshold,
and 180-second heartbeat maximum. Do not tighten the evidence lifetime until a
deliberate stopped-scheduler check proves this monitor alerts first.

## TCP Monitors

- PostgreSQL: `postgres:5432`
- Redis: `redis:6379`
- Neo4j Bolt: `neo4j:7687`
- SpiceDB gRPC: `spicedb:50051`

## Push Monitors

Add later:

- Celery worker heartbeat
- Drive sync job heartbeat
- Evaluation job heartbeat

The permission freshness HTTP monitor above replaces the previously planned
permission-sync push heartbeat: it remains observable when Celery Beat or the
worker is dead and cannot send a push.
