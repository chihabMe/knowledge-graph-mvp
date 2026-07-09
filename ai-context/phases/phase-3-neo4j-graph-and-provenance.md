# Phase 3: Neo4j Graph And Provenance

## Purpose

Build a graph representation of documents, chunks, entities, and relationships with strict source provenance.

## Scope

- Neo4j constraints and indexes.
- Extraction engine evaluation behind an adapter.
- Document nodes.
- Chunk nodes.
- Entity nodes.
- Relationship edges.
- Source provenance on every derived graph element.
- Vector index setup.

## Out Of Scope

- Permission-safe retrieval UI.
- Google Drive permission enforcement.

## Tasks

- [x] Define initial graph schema. Effort: High. (Document/Chunk/Entity
  constraints in `graph/schema.py`. Extracted entities share one structural
  `:Entity` label with `entity_type` as a property — ontology types as node
  labels would collide with the structural `:Document` uniqueness
  constraint.)
- [x] Ship the ontology as code: a constants module declaring the allowed entity and relationship types, plus a test that fails when extraction produces an undeclared type. Effort: High. (`graph/ontology.py` + `validate_extraction_result` boundary check.)
- [x] Evaluate `neo4j-graphrag`, Graphify, and Graphiti for provenance support. Effort: Extra High. (2026-07-09 — `neo4j-graphrag` chosen; fact-level provenance confirmed supported. See ADR-010 for the comparison and the per-document entity-scoping rule.)
- [x] Create an extraction adapter boundary before committing to one engine. Effort: High. (`graph/extraction.py`: `ExtractionAdapter` protocol, typed result dataclasses, deterministic `ParagraphChunkExtractor` baseline.)
- [x] Add Neo4j migration/setup command. Effort: High. (`manage.py graph_setup`, idempotent.)
- [x] Store Document and Chunk nodes. Effort: High. (Writers in
  `graph/writer.py` with fail-closed provenance, wired into
  `queue_document_extraction` via `graph/pipeline.py` — text/* content only,
  unsupported/undecodable content skips with a status. Live-validated
  2026-07-09: `graph_setup` applied both constraints to the real Neo4j, and
  a synthetic document ran through the real Celery worker end-to-end —
  Document + 2 Chunk nodes written with full provenance and `belongs_to`
  edges, no content in worker logs; smoke data then removed from both
  stores. Deliberate decision: extraction runs regardless of
  `retrieval_eligible`, because enforcement lives at retrieval and Phase 2
  only re-queues extraction on content change — skipping ineligible
  documents would leave them permanently missing from the graph once their
  permissions become readable.)
- [~] Store extracted entity nodes. Effort: High.
  (`replace_document_entities` in `graph/writer.py`: per-document scoped
  `entity_id`, `mentions` edge to the source chunk, chunk-anchor check fails
  loudly. Offline-tested; live LLM extraction blocked on the OpenRouter
  key.)
- [~] Store extracted relationship edges. Effort: Extra High. (Same writer:
  endpoints resolved by name against the document's own entities;
  unresolvable/ambiguous endpoints counted and skipped. Offline-tested;
  live LLM extraction blocked on the OpenRouter key.)
- [x] Attach source provenance to every graph element. Effort: Extra High.
  (Enforced for Document, Chunk, Entity nodes and relationship edges —
  `MissingProvenanceError` refuses incomplete identity; every element
  carries the identity triple + `source_permissions_version`.)
- [~] Add retrieval guard that excludes graph records missing source provenance. Effort: Extra High. (`graph/guard.py`: `provenance_where` Cypher fragment + `record_has_provenance` post-query check; wired into real queries when the retrieval path exists.)
- [ ] Add vector index support. Effort: High.
- [~] Add provenance tests. Effort: Extra High. (Offline writer/guard/ontology
  tests in `graph/tests.py`; live Neo4j validation pending.)

## Validation

- [ ] Every node derived from source material has source document metadata.
- [ ] Every relationship derived from source material has source document metadata.
- [ ] Queries can filter by allowed source document IDs.
- [ ] Missing provenance defaults to unusable for retrieval.
- [ ] Extraction engine choice documents fact-level vs document-level provenance support.
- [ ] Extraction cannot introduce entity or relationship types outside the declared ontology without a failing test.
- [ ] Fact-level source attribution includes source document and chunk IDs; document-level-only provenance is treated as insufficient for permission-safe retrieval unless strict document visibility is enforced.

## Completion Status

In progress (started 2026-07-09, branch `phase-3/graph-foundation`).
Foundation laid: `graph` Django app with a process-wide Neo4j driver
(`graph/db.py`), idempotent constraint setup (`manage.py graph_setup`),
ontology as code mirroring the brief's section 8, an engine-agnostic
extraction adapter boundary with a deterministic paragraph-chunk baseline,
fail-closed Document/Chunk writers keyed by `source_document_id`, and the
provenance retrieval guard. The pipeline is wired and live-validated for
the chunk path: Phase 2's `queue_document_extraction` runs
`graph/pipeline.py` end-to-end. Engine decision made (ADR-010):
`neo4j-graphrag` behind the adapter (`graph/graphrag.py`), selected via
`GRAPH_EXTRACTION_ENGINE=neo4j_graphrag`, with entity/relationship writers
(`replace_document_entities`) storing per-document-scoped entities with
`mentions` fact-level provenance. Offline tests cover the real
neo4j-graphrag extraction component with only the LLM faked. Remaining:
live LLM extraction validation (blocked on OpenRouter key), vector index,
wiring the guard into a real retrieval query (Phase 5 seam).
