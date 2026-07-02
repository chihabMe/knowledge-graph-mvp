# Infrastructure

This folder contains deployment and operations scaffolding.

## Files

- `compose.infrastructure.yml`: Runnable infrastructure services that do not require application code.
- `compose.app.yml`: Django and Celery application services.
- `traefik/`: Traefik static and dynamic config.
- `uptime-kuma/`: Notes for uptime monitor targets.

## Current Status

Use the infrastructure compose file to start supporting services first.

```bash
docker compose -f infra/compose.infrastructure.yml up -d
```

Internal databases bind to localhost-only alternate ports by default to avoid collisions with other local projects:

- PostgreSQL: `15432 -> 5432`
- Redis: `16379 -> 6379`
- Neo4j HTTP: `17474 -> 7474`
- Neo4j Bolt: `17687 -> 7687`
- SpiceDB gRPC: `15051 -> 50051`
- SpiceDB HTTP: `18443 -> 8443`

Network boundaries:

- `kg-public`: ingress-facing services such as Traefik, Open WebUI, and Django.
- `kg-private`: internal data plane for PostgreSQL, Redis, Neo4j, and SpiceDB. This network is marked `internal: true`.
- `kg-egress`: outbound internet access for application containers that need external APIs such as Google Drive or OpenRouter.

Use both compose files when running the application services:

```bash
docker compose -f infra/compose.infrastructure.yml -f infra/compose.app.yml up -d
```

Celery beat is behind the `scheduler` profile until scheduled jobs are needed:

```bash
docker compose -f infra/compose.infrastructure.yml -f infra/compose.app.yml --profile scheduler up -d
```
