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

Use both compose files when running the application services:

```bash
docker compose -f infra/compose.infrastructure.yml -f infra/compose.app.yml up -d
```

Celery beat is behind the `scheduler` profile until scheduled jobs are needed:

```bash
docker compose -f infra/compose.infrastructure.yml -f infra/compose.app.yml --profile scheduler up -d
```
