# Backend API

## Authentication

Protected endpoints require:

```text
Authorization: Bearer <BACKEND_API_KEY>
```

## Health

```http
GET /health
```

Checks that the backend can connect to Neo4j.

## Ingest Local Files

```http
POST /ingest/local
```

Reads supported files from `INGESTION_ROOT`, extracts text, chunks content, and
stores the document graph in Neo4j.

## List Models

```http
GET /v1/models
```

OpenAI-compatible model list endpoint for Open WebUI.

## Chat Completion

```http
POST /v1/chat/completions
```

OpenAI-compatible chat endpoint. The backend retrieves context from Neo4j,
builds a grounded prompt, calls OpenRouter, and returns a normal chat completion
response.

Example:

```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer change-this-local-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "client-knowledge-graph",
    "messages": [
      {"role": "user", "content": "Who owns the technical implementation?"}
    ]
  }'
```
