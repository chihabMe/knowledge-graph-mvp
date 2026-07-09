"""Neo4j constraints and indexes, applied by the `graph_setup` management
command. Statements use IF NOT EXISTS so re-running is always safe.

Only Document and Chunk are declared here — they are the structural nodes
Phase 2 data already gives us stable identity for. Entity/relationship
constraints are added once those writers exist (later Phase 3 steps), so
this list isn't presupposing an extraction schema before it's designed.
"""

# A Document node's identity IS its provenance: it is keyed by the Postgres
# SourceDocument pk, named identically to the provenance field carried by
# every derived node so the two can never drift apart. Extracted entities
# share the single :Entity label (type lives in the entity_type property —
# see graph/writer.py for why ontology types aren't used as labels), so one
# constraint covers all of them.
CONSTRAINTS = [
    "CREATE CONSTRAINT document_source_id_unique IF NOT EXISTS "
    "FOR (d:Document) REQUIRE d.source_document_id IS UNIQUE",
    "CREATE CONSTRAINT chunk_id_unique IF NOT EXISTS FOR (c:Chunk) REQUIRE c.chunk_id IS UNIQUE",
    "CREATE CONSTRAINT entity_id_unique IF NOT EXISTS FOR (e:Entity) REQUIRE e.entity_id IS UNIQUE",
]
