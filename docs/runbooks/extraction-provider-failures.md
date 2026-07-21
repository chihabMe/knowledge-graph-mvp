# Runbook: Graph Extraction Provider Failures

Status: verified live on 2026-07-21 (see `docs/daily-reports/2026-07-21.md`).

## The two failure modes, root-caused

### 1. `MalformedModelOutputError` (was: raw `TypeError`)

**Symptom:** extraction task logs `MalformedModelOutputError('TypeError')`; before
2026-07-21 this surfaced as `TypeError: 'NoneType' object is not subscriptable`
from `neo4j_graphrag/llm/openai_llm.py` and marked the document FAILED on the
first occurrence.

**Root cause (captured live, not guessed):** OpenRouter returns **HTTP 200**
with an error payload in the body when the upstream provider dies mid-response:

```json
{"id": null, "choices": null, ...,
 "error": {"message": "JSON error injected into SSE stream", "code": 502}}
```

The OpenAI SDK only raises on HTTP error *statuses*, so this parses into a
response with `choices=None` and the library crashes on `choices[0]`. This is
an infrastructure fault at the provider, not model quality and not our
pipeline. OpenRouter's own docs confirm 200-with-error-body responses do NOT
trigger their automatic failover — client-side handling is required
(https://openrouter.ai/docs/api_reference/errors-and-debugging,
https://openrouter.ai/blog/insights/reliability-failover/).

On 2026-07-21 the `deepseek/deepseek-v3.2` upstream failed 6 of 12 probe
requests this way while `google/gemini-2.5-flash` and `openai/gpt-4o-mini`
were 12/12 clean under the identical probe.

### 2. `UnknownRelationshipTypeError` / `UnknownEntityTypeError`

**Symptom:** extraction retries logging e.g.
`UnknownRelationshipTypeError('produces')`.

**Root cause:** genuine model disobedience — the LLM occasionally invents a
relationship/entity type outside the closed ontology despite the closed
GraphSchema grounding. The pipeline's `validate_extraction_result` rejects it
(fail closed — correct). It is nondeterministic; a fresh attempt on the same
chunk almost always conforms.

## The three defense layers (all shipped 2026-07-21)

1. **In-task auto-retry** — both failure modes are classified retryable for
   the `neo4j_graphrag` engine (`graph/graphrag.py::RETRYABLE_LLM_EXCEPTIONS`,
   `graph/pipeline.py::get_retryable_extraction_exceptions`), so
   `queue_document_extraction` retries up to 3× with capped backoff.
2. **Cross-sync requeue** — documents that still land FAILED are re-queued by
   later syncs within `GRAPH_EXTRACTION_MAX_SYNC_ATTEMPTS` (anti-loop budget;
   a document at the budget needs a manual
   `queue_document_extraction.delay(pk)` — deliberate operator escape hatch).
3. **OpenRouter model fallbacks** — `GRAPH_EXTRACTION_FALLBACK_MODELS` (.env,
   comma-separated) rides along as OpenRouter's `models` array via
   `extra_body`; when the primary model's providers fail, the router serves
   the request with the first healthy fallback model. Current chain:
   `google/gemini-2.5-flash, openai/gpt-4o-mini` (chosen by live probe, see
   above — re-benchmark before changing).

## Verified outcomes (full 15-document re-extraction each time)

| Configuration | Result |
|---|---|
| No defenses (original ingestion) | 14/15 stuck FAILED, all manual retries |
| Retry only | 13/15 self-healed; 1 healed by sync requeue; 1 manual (budget) |
| Retry + model fallbacks | **15/15 clean, zero human intervention**; in-body 502s eliminated; only 6 ontology retries, all self-healed |

## Operator quick reference

- Stuck document: check `SourceDocument.graph_extraction_error_summary` and
  `graph_extraction_attempts` vs `GRAPH_EXTRACTION_MAX_SYNC_ATTEMPTS`.
- At budget: `queue_document_extraction.delay(<pk>)` from a Django shell.
- Suspected provider degradation: run a ~12-request probe with
  `response_format={"type": "json_object"}` against the primary model and
  check for `choices=None` bodies; compare candidates before swapping
  `GRAPH_EXTRACTION_MODEL` / `GRAPH_EXTRACTION_FALLBACK_MODELS`.
- Remember: fail-closed means a FAILED document is *absent from retrieval*,
  never wrong or leaked — availability issue only.
