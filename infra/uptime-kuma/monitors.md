# Uptime Kuma Monitor Targets

Create these monitors after the services are running.

## HTTP Monitors

- Open WebUI: `http://open-webui:8080`
- Django health: `http://django:8000/health`
- Dozzle: `http://dozzle:8080`
- Traefik dashboard: `http://traefik:8080/dashboard/`

## TCP Monitors

- PostgreSQL: `postgres:5432`
- Redis: `redis:6379`
- Neo4j Bolt: `neo4j:7687`
- SpiceDB gRPC: `spicedb:50051`

## Push Monitors

Add later:

- Celery worker heartbeat
- Drive sync job heartbeat
- Permission sync job heartbeat
- Evaluation job heartbeat

