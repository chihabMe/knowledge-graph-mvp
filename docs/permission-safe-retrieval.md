# Permission-Safe Retrieval Operations

Phase 5 provides `POST /api/query/` for authenticated backend users. The
request body accepts only:

```json
{"question": "What are the project phases?"}
```

Identity always comes from the authenticated Django session. Supplying
`user_email` or any other unexpected field returns HTTP 400.

## Security Order

Every request follows this order:

1. Resolve the authenticated server-side email.
2. Ask SpiceDB for allowed source-document IDs.
3. Stop with the shared refusal when the allowlist is empty or unavailable.
4. Generate the question embedding when embeddings are enabled.
5. Query Neo4j only through Cypher paths that include the allowlist and full
   provenance guard.
6. Recheck active, retrieval-eligible, version-matched, unexpired PostgreSQL
   permission evidence.
7. Assemble bounded JSONL context from the remaining evidence.
8. Call the configured answer generator.
9. Construct citations on the server from only the evidence included in that
   context.

Neo4j 5's global vector-index candidate procedure is not used because it
cannot apply the per-request Drive allowlist before candidate selection. The
Phase 5 path first matches allowed, provenance-complete chunks and then
computes vector similarity inside that set.

## Provider Configuration

The deterministic extractive path remains the safe default. Enable production
embeddings and OpenRouter answers explicitly in the deployment `.env`:

```dotenv
OPENROUTER_API_KEY=replace-me
OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
OPENROUTER_MODEL=openai/gpt-4.1-mini
OPENROUTER_EMBEDDING_MODEL=openai/text-embedding-3-small
OPENROUTER_REQUEST_TIMEOUT_SECONDS=60

GRAPH_EMBEDDING_PROVIDER=openrouter
GRAPH_CHUNK_EMBEDDING_DIMENSIONS=1536
GRAPH_EMBEDDING_BATCH_SIZE=64

QUERY_ANSWER_PROVIDER=openrouter
QUERY_CONTEXT_MAX_CHARS=12000
QUERY_ANSWER_MAX_TOKENS=800
QUERY_RETRIEVAL_LIMIT=5
QUERY_VECTOR_MIN_SCORE=0.45
```

The stored chunk and question embedding model/dimensions must match. Changing
either requires reindexing existing chunks. Permission-only changes never
trigger re-embedding.

## Reindex Existing Chunks

After enabling or changing embeddings, queue all current stored documents:

```bash
docker compose --env-file .env \
  -f infra/compose.infrastructure.yml \
  -f infra/compose.app.yml \
  -f infra/compose.dev.yml \
  exec -T django python manage.py graph_reindex_embeddings --all
```

Or queue selected documents by PostgreSQL ID:

```bash
docker compose --env-file .env \
  -f infra/compose.infrastructure.yml \
  -f infra/compose.app.yml \
  -f infra/compose.dev.yml \
  exec -T django python manage.py graph_reindex_embeddings \
  --source-document-id 1
```

The command sends only document IDs and content hashes through Celery. It
skips missing content, stale content versions, and already-running extraction
jobs.

## Development Query Smoke Test

Until Phase 6 provisions trusted Google/OIDC users through Open WebUI, use the
Django test client inside the running container:

```bash
docker compose --env-file .env \
  -f infra/compose.infrastructure.yml \
  -f infra/compose.app.yml \
  -f infra/compose.dev.yml \
  exec -T django python manage.py shell -c \
  "from django.contrib.auth import get_user_model; \
from rest_framework.test import APIClient; \
user=get_user_model().objects.exclude(email='').first(); \
client=APIClient(); client.force_login(user); \
response=client.post('/api/query/', \
{'question':'What are the project phases?'}, \
format='json', HTTP_HOST='127.0.0.1'); \
print('status=', response.status_code); print('response=', response.json())"
```

A user without permitted context receives HTTP 200 with the shared refusal,
empty citations, and `reason=insufficient_accessible_context`. Authentication
failures remain HTTP 403, and unexpected request fields remain HTTP 400.
