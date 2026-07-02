# Client-Owned AI Knowledge Graph MVP

This project is a first working version of a client-owned AI knowledge system.
It connects business documents to a Neo4j knowledge graph and exposes an
OpenAI-compatible chat endpoint that Open WebUI can use as its main interface.

The goal is not to build a full SaaS product. The goal is to create a repeatable
implementation package that can be deployed for one client at a time.

## What It Does

- Runs Open WebUI as the client-facing chat interface.
- Runs Neo4j as the knowledge graph database.
- Runs a Python/FastAPI backend that ingests files and answers chat requests.
- Stores documents, chunks, extracted terms, and relationships in Neo4j.
- Retrieves relevant graph context before calling OpenRouter.
- Lets Open WebUI talk to the backend through `/v1/chat/completions`.

## First MVP Boundary

The first ingestion path is a mounted folder at `data/import`. This makes the
MVP testable before Google Drive OAuth is added. The Google Drive connector is
the next milestone and should feed the same ingestion service.

## Quick Start

1. Copy the example env file:

```bash
cp .env.example .env
```

2. Edit `.env` and set:

```bash
OPENROUTER_API_KEY=your-key
NEO4J_PASSWORD=your-password
NEO4J_AUTH=neo4j/your-password
BACKEND_API_KEY=your-local-backend-secret
WEBUI_SECRET_KEY=your-webui-secret
```

3. Add test files into:

```bash
data/import
```

4. Start the stack:

```bash
docker compose up --build
```

5. Ingest local files:

```bash
curl -X POST http://localhost:8080/ingest/local \
  -H "Authorization: Bearer change-this-local-secret"
```

6. Open the UI:

```text
http://localhost:3000
```

Open WebUI is configured to use the backend as an OpenAI-compatible endpoint.

## Service URLs

- Open WebUI: `http://localhost:3000`
- Backend API: `http://localhost:8080`
- Neo4j Browser: `http://localhost:7474`

## Target Architecture Status

The target backend stack is Django + Django REST Framework + Celery + Redis.
The existing `backend/` folder still contains an earlier FastAPI prototype.
Do not treat that prototype as the final backend.

Infrastructure scaffolding lives in `infra/`.

Run supporting services with:

```bash
docker compose -f infra/compose.infrastructure.yml up -d
```

## Supported Local File Types

- `.txt`
- `.md`
- `.pdf`
- `.docx`
- `.csv`

## Intended Production Direction

For a real client deployment:

- Put the stack behind Caddy or Traefik for SSL.
- Use a client-specific subdomain.
- Replace local folder ingestion with Google Drive OAuth ingestion.
- Add backups for Neo4j and Open WebUI data volumes.
- Store secrets outside the repo.

See [AGENT_PROJECT_BRIEF.md](AGENT_PROJECT_BRIEF.md) for the canonical project
brief future AI agents should read before building features.

Future AI agents should also start with [AGENTS.md](AGENTS.md), then read the
files in `ai-context/`.

See [docs/project-plan.md](docs/project-plan.md) for the shorter build plan.
