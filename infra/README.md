# Infrastructure

This folder contains deployment and operations scaffolding.

## Files

- `compose.infrastructure.yml`: Runnable infrastructure services that do not require application code.
- `compose.app.example.yml`: Template for the future Django/Celery application services.
- `traefik/`: Traefik static and dynamic config.
- `uptime-kuma/`: Notes for uptime monitor targets.

## Current Status

The final Django backend has not been created yet. Use the infrastructure compose file to start supporting services first.

```bash
docker compose -f infra/compose.infrastructure.yml up -d
```

Do not treat the old FastAPI prototype as the final backend.

