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

- [ ] Define initial graph schema. Effort: High.
- [ ] Evaluate `neo4j-graphrag`, Graphify, and Graphiti for provenance support. Effort: Extra High.
- [ ] Create an extraction adapter boundary before committing to one engine. Effort: High.
- [ ] Add Neo4j migration/setup command. Effort: High.
- [ ] Store Document and Chunk nodes. Effort: High.
- [ ] Store extracted entity nodes. Effort: High.
- [ ] Store extracted relationship edges. Effort: Extra High.
- [ ] Attach source provenance to every graph element. Effort: Extra High.
- [ ] Add retrieval guard that excludes graph records missing source provenance. Effort: Extra High.
- [ ] Add vector index support. Effort: High.
- [ ] Add provenance tests. Effort: Extra High.

## Validation

- [ ] Every node derived from source material has source document metadata.
- [ ] Every relationship derived from source material has source document metadata.
- [ ] Queries can filter by allowed source document IDs.
- [ ] Missing provenance defaults to unusable for retrieval.
- [ ] Extraction engine choice documents fact-level vs document-level provenance support.
- [ ] Fact-level source attribution includes source document and chunk IDs; document-level-only provenance is treated as insufficient for permission-safe retrieval unless strict document visibility is enforced.

## Completion Status

Not started.
