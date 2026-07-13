# Phase 5: Permission-Safe Retrieval

## Purpose

Answer user questions using only graph/vector context derived from documents the user is allowed to see.

## Scope

- Query endpoint.
- SpiceDB pre-filter.
- Neo4j retrieval constrained by provenance.
- Hybrid vector and graph traversal.
- Context assembly.
- Citations.
- Safe refusal behavior.

## Out Of Scope

- Custom frontend.
- Advanced analytics dashboards.

## Tasks

- [x] Build `/api/query/` contract. Effort: High. (Question-only request;
  extractive answer/citations/refusal response; unknown fields rejected.)
- [x] Resolve authenticated user identity. Effort: Extra High. (Uses the
  server-side Django session user email; request JSON identity is rejected.
  Open WebUI Google OIDC wiring remains Phase 6.)
- [x] Ask SpiceDB for allowed documents before retrieval. Effort: Extra High.
- [x] Exclude documents whose `SourceDocument.retrieval_eligible` is false
  before querying Neo4j chunks, vectors, or graph paths. Effort: Extra High.
- [x] Compose the Phase 3 provenance guard into every implemented Neo4j
  retrieval query. Effort: Extra High. (`graph/guard.py`: `provenance_where`
  Cypher fragment + `record_has_provenance` post-query check. The baseline
  chunk and bounded one-hop fact paths guard every node and relationship;
  the future vector path is separately tracked below and must do the same.)
- [ ] Filter Neo4j vector search by allowed provenance. Effort: Extra High.
- [x] Filter graph traversal by allowed provenance. Effort: Extra High.
  (Bounded one-hop entity facts only; unrestricted traversal is not present.)
- [x] Assemble cited context. Effort: Extra High. (Citations are sourced only
  from the fresh PostgreSQL intersection of retrieved and SpiceDB-allowed IDs.)
- [ ] Call OpenRouter safely. Effort: High.
- [x] Add refusal behavior. Effort: Extra High. (Authorization, evidence,
  relevance, and retrieval failures share one non-revealing response.)
- [x] Add graph-path leak tests. Effort: Extra High.

## Validation

- [x] Allowed facts are answerable through the baseline extractive path.
- [x] Restricted facts are refused.
- [x] Restricted facts do not leak through connected visible nodes.
- [x] Documents marked `retrieval_eligible = false` contribute no chunks,
  graph facts, citations, or prompt context even if stale SpiceDB relationships
  still exist.
- [x] Documents with expired permission-verification evidence contribute no
  chunks, graph facts, citations, or prompt context even if SpiceDB still
  returns an old grant.
- [x] Citations point only to allowed Drive files.
- [x] Insufficient context returns a clear no-answer response.
- [x] Graph records missing source provenance are excluded from every
  retrieval query (guard composed, not just available), proven by leak tests.
- [x] Live development acceptance refreshes permission evidence, refuses an
  unrelated question despite an allowed document, and returns citations only
  to the SpiceDB-allowed Drive PDF for a relevant question.

## Completion Status

In progress. The first permission-safe vertical slice is complete and live
validated with the development Drive PDF. The exact next task is to implement
the production embedding adapter and then add a permission/provenance-filtered
Neo4j vector retrieval path. OpenRouter answer synthesis remains blocked on
proving that vector boundary safe.
