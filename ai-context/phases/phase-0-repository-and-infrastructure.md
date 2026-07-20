# Phase 0: Repository And Infrastructure

## Purpose

Create a clean repository baseline, persistent AI-agent documentation, and infrastructure scaffold before application code begins.

## Scope

- Git repository.
- GitHub repository.
- AI-agent documentation.
- Docker Compose infrastructure scaffold.
- Traefik, Dozzle, PostgreSQL, Redis, Neo4j, SpiceDB, and Open WebUI service definitions.

## Out Of Scope

- Django application code.
- Google Drive ingestion.
- Permission sync.
- Retrieval logic.

## Tasks

- [x] Create clean project directory. Effort: Medium.
- [x] Initialize git repository. Effort: Medium.
- [x] Create GitHub repository. Effort: Medium.
- [x] Create root `AGENTS.md`. Effort: Medium.
- [x] Create `ai-context/` documentation set. Effort: Medium.
- [x] Create infrastructure Compose files. Effort: High.
- [x] Validate infrastructure Compose config. Effort: Medium.
- [x] Remove old FastAPI prototype. Effort: Medium.
- [x] Reinitialize clean git history. Effort: Medium.

## Validation

- [x] `docker compose -f infra/compose.infrastructure.yml config`
- [x] `git status --short --branch`
- [x] GitHub remote configured.

## Completion Status

Complete.
