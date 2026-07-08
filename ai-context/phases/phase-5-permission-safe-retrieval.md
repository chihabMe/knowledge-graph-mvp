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

- [ ] Build `/api/query/` contract. Effort: High.
- [ ] Resolve authenticated user identity. Effort: Extra High.
- [ ] Ask SpiceDB for allowed documents before retrieval. Effort: Extra High.
- [ ] Exclude documents whose `SourceDocument.retrieval_eligible` is false
  before querying Neo4j chunks, vectors, or graph paths. Effort: Extra High.
- [ ] Filter Neo4j vector search by allowed provenance. Effort: Extra High.
- [ ] Filter graph traversal by allowed provenance. Effort: Extra High.
- [ ] Assemble cited context. Effort: Extra High.
- [ ] Call OpenRouter safely. Effort: High.
- [ ] Add refusal behavior. Effort: Extra High.
- [ ] Add graph-path leak tests. Effort: Extra High.

## Validation

- [ ] Allowed facts are answerable.
- [ ] Restricted facts are refused.
- [ ] Restricted facts do not leak through connected visible nodes.
- [ ] Documents marked `retrieval_eligible = false` contribute no chunks,
  graph facts, citations, or prompt context even if stale SpiceDB relationships
  still exist.
- [ ] Citations point only to allowed Drive files.
- [ ] Insufficient context returns a clear no-answer response.

## Completion Status

Not started.
