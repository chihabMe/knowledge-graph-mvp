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

- [~] Define initial graph schema. Effort: High. (Document/Chunk constraints
  shipped in `graph/schema.py`; entity/relationship constraints follow the
  extraction-engine decision so the schema isn't presupposed.)
- [x] Ship the ontology as code: a constants module declaring the allowed entity and relationship types, plus a test that fails when extraction produces an undeclared type. Effort: High. (`graph/ontology.py` + `validate_extraction_result` boundary check.)
- [ ] Evaluate `neo4j-graphrag`, Graphify, and Graphiti for provenance support. Effort: Extra High.
- [x] Create an extraction adapter boundary before committing to one engine. Effort: High. (`graph/extraction.py`: `ExtractionAdapter` protocol, typed result dataclasses, deterministic `ParagraphChunkExtractor` baseline.)
- [x] Add Neo4j migration/setup command. Effort: High. (`manage.py graph_setup`, idempotent.)
- [~] Store Document and Chunk nodes. Effort: High. (Writers in
  `graph/writer.py` with fail-closed provenance, wired into
  `queue_document_extraction` via `graph/pipeline.py` — text/* content only,
  unsupported/undecodable content skips with a status; live Neo4j
  validation pending. Deliberate decision: extraction runs regardless of
  `retrieval_eligible`, because enforcement lives at retrieval and Phase 2
  only re-queues extraction on content change — skipping ineligible
  documents would leave them permanently missing from the graph once their
  permissions become readable.)
- [ ] Store extracted entity nodes. Effort: High.
- [ ] Store extracted relationship edges. Effort: Extra High.
- [~] Attach source provenance to every graph element. Effort: Extra High.
  (Enforced for Document/Chunk writes — `MissingProvenanceError` refuses
  incomplete identity; extends to entity/relationship writers when built.)
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
provenance retrieval guard. The pipeline is wired: Phase 2's
`queue_document_extraction` now runs `graph/pipeline.py` end-to-end
(stored text content → paragraph chunks → Neo4j with provenance). All
offline-tested. Next: evaluate extraction engines behind the adapter,
entity/relationship writers, vector index, live Neo4j validation.
