"""Neo4j writers for Document and Chunk nodes.

Provenance or exclusion (project invariant 2): every node written here
carries source-document provenance, and a document whose identity fields are
incomplete is refused outright — never written with gaps for someone to
backfill later.
"""

from graph.embeddings import ChunkEmbedding, validate_chunk_embeddings
from graph.extraction import ExtractedChunk, ExtractedEntity, ExtractedRelationship
from graph.ontology import validate_entity_type, validate_relationship_type
from integrations.models import SourceDocument

# Structural edge from a chunk to the document it was derived from. Drawn
# from the declared ontology like every other relationship type.
CHUNK_DOCUMENT_RELATIONSHIP = "belongs_to"
validate_relationship_type(CHUNK_DOCUMENT_RELATIONSHIP)

# Structural edge from a chunk to an entity found in it — the fact-level
# provenance anchor (ADR-010).
CHUNK_ENTITY_RELATIONSHIP = "mentions"
validate_relationship_type(CHUNK_ENTITY_RELATIONSHIP)


class MissingProvenanceError(ValueError):
    """Raised instead of writing a graph element with incomplete provenance."""


class DocumentNodeMissingError(ValueError):
    """Raised instead of silently dropping chunks when the Document node is absent."""


class ChunkNodeMissingError(ValueError):
    """Raised when an entity references a chunk that was never written."""


def _entity_id(source_document_id: int, entity: ExtractedEntity) -> str:
    # Per-document scoping (ADR-010): entities are never merged across
    # documents, so a restricted document's facts can't surface one hop from
    # an unrestricted document through a shared node.
    normalized_name = " ".join(entity.name.split()).lower()
    return f"{source_document_id}:{entity.entity_type}:{normalized_name}"


def document_provenance(document: SourceDocument) -> dict[str, int | str]:
    if not document.pk or not document.connection_id or not document.drive_file_id:
        raise MissingProvenanceError(
            "SourceDocument is missing identity fields required for provenance."
        )
    # source_permissions_version may legitimately be blank (permission fetch
    # failed → the document is already retrieval-ineligible in Postgres); the
    # identity triple above is what the retrieval guard requires.
    return {
        "source_document_id": document.pk,
        "connection_id": document.connection_id,
        "drive_file_id": document.drive_file_id,
        "source_permissions_version": document.source_permissions_version,
    }


def upsert_document(db_session, document: SourceDocument) -> None:
    provenance = document_provenance(document)
    db_session.run(
        "MERGE (d:Document {source_document_id: $source_document_id}) "
        "SET d.connection_id = $connection_id, "
        "    d.drive_file_id = $drive_file_id, "
        "    d.source_permissions_version = $source_permissions_version, "
        "    d.content_hash = $content_hash, "
        "    d.title = $title, "
        "    d.mime_type = $mime_type, "
        "    d.drive_url = $drive_url",
        **provenance,
        content_hash=document.content_hash,
        title=document.title,
        mime_type=document.mime_type,
        drive_url=document.drive_url,
    )


def replace_document_chunks(
    db_session,
    document: SourceDocument,
    chunks: tuple[ExtractedChunk, ...],
    *,
    chunk_embeddings: tuple[ChunkEmbedding, ...] = (),
    embedding_dimensions: int | None = None,
) -> int:
    """Replace the document's chunk set (delete then create).

    Chunks are derived content, so re-extraction always rewrites the full set;
    merging per-chunk would leave orphans behind when a document shrinks.
    """
    provenance = document_provenance(document)
    embedding_vectors = validate_chunk_embeddings(
        chunks, chunk_embeddings, dimensions=embedding_dimensions
    )
    # MATCH + CREATE against an absent Document node would silently create
    # nothing while this function reports success — check explicitly and fail
    # loudly instead (also covers the empty-chunks case, which never reaches
    # the batched create below). Callers upsert_document() first.
    found = db_session.run(
        "MATCH (d:Document {source_document_id: $source_document_id}) RETURN d.source_document_id",
        source_document_id=provenance["source_document_id"],
    ).single()
    if found is None:
        raise DocumentNodeMissingError(
            f"No Document node for source_document_id={provenance['source_document_id']}."
        )

    db_session.run(
        "MATCH (c:Chunk {source_document_id: $source_document_id}) DETACH DELETE c",
        source_document_id=provenance["source_document_id"],
    )
    if not chunks:
        return 0
    # One UNWIND batch instead of a round-trip per chunk. A null embedding in
    # the map simply stores no property — same shape as before for chunks
    # without vectors.
    db_session.run(
        "MATCH (d:Document {source_document_id: $source_document_id}) "
        "UNWIND $chunks AS chunk "
        "CREATE (c:Chunk {chunk_id: chunk.chunk_id, "
        "                 chunk_index: chunk.chunk_index, "
        "                 text: chunk.text, "
        "                 embedding: chunk.embedding, "
        "                 source_document_id: $source_document_id, "
        "                 connection_id: $connection_id, "
        "                 drive_file_id: $drive_file_id, "
        "                 source_permissions_version: $source_permissions_version}) "
        f"CREATE (c)-[r:{CHUNK_DOCUMENT_RELATIONSHIP}]->(d) "
        "SET r.source_document_id = $source_document_id, "
        "    r.connection_id = $connection_id, "
        "    r.drive_file_id = $drive_file_id, "
        "    r.source_permissions_version = $source_permissions_version",
        **provenance,
        chunks=[
            {
                "chunk_id": f"{provenance['source_document_id']}:{chunk.index}",
                "chunk_index": chunk.index,
                "text": chunk.text,
                "embedding": embedding_vectors.get(chunk.index),
            }
            for chunk in chunks
        ],
    )
    return len(chunks)


def replace_document_entities(
    db_session,
    document: SourceDocument,
    entities: tuple[ExtractedEntity, ...],
    relationships: tuple[ExtractedRelationship, ...],
) -> dict[str, int]:
    """Replace the document's extracted entities and relationships.

    Entity nodes carry a single structural :Entity label with entity_type as
    a property — using ontology types as Neo4j labels would collide with the
    structural :Document nodes (the ontology legitimately declares "Document"
    as an entity type, and its uniqueness constraint is keyed on the same
    source_document_id every entity carries as provenance).

    Relationships resolve their endpoints against this document's own
    entities — by (entity_type, name) when the engine supplied endpoint
    types, by bare name otherwise. An unresolvable or ambiguous endpoint is
    counted and skipped — an edge that can't be anchored to known entities
    can't be stored with valid provenance.
    """
    for entity in entities:
        validate_entity_type(entity.entity_type)
    for relationship in relationships:
        validate_relationship_type(relationship.relationship_type)
    provenance = document_provenance(document)
    source_document_id = provenance["source_document_id"]

    db_session.run(
        "MATCH (e:Entity {source_document_id: $source_document_id}) DETACH DELETE e",
        source_document_id=source_document_id,
    )

    entity_ids_by_name: dict[str, set[str]] = {}
    entity_ids_by_type_and_name: dict[tuple[str, str], set[str]] = {}
    written_entity_ids: set[str] = set()
    entity_rows = []
    for entity in entities:
        entity_id = _entity_id(source_document_id, entity)
        entity_ids_by_name.setdefault(entity.name, set()).add(entity_id)
        entity_ids_by_type_and_name.setdefault((entity.entity_type, entity.name), set()).add(
            entity_id
        )
        written_entity_ids.add(entity_id)
        entity_rows.append(
            {
                "entity_id": entity_id,
                "name": entity.name,
                "entity_type": entity.entity_type,
                "chunk_id": f"{source_document_id}:{entity.chunk_index}",
            }
        )
    if entity_rows:
        # One UNWIND batch per document. MATCH drops rows whose anchoring
        # chunk is missing, so a shortfall in the returned count is the same
        # loud failure the per-row write used to raise.
        anchored = db_session.run(
            "UNWIND $entities AS entity "
            "MATCH (c:Chunk {chunk_id: entity.chunk_id}) "
            "MERGE (e:Entity {entity_id: entity.entity_id}) "
            "SET e.name = entity.name, "
            "    e.entity_type = entity.entity_type, "
            "    e.source_document_id = $source_document_id, "
            "    e.connection_id = $connection_id, "
            "    e.drive_file_id = $drive_file_id, "
            "    e.source_permissions_version = $source_permissions_version "
            f"MERGE (c)-[m:{CHUNK_ENTITY_RELATIONSHIP}]->(e) "
            "SET m.source_document_id = $source_document_id, "
            "    m.connection_id = $connection_id, "
            "    m.drive_file_id = $drive_file_id, "
            "    m.source_permissions_version = $source_permissions_version "
            "RETURN count(*) AS n",
            **provenance,
            entities=entity_rows,
        ).single()
        if anchored is None or anchored["n"] != len(entity_rows):
            raise ChunkNodeMissingError(
                f"{len(entity_rows) - (0 if anchored is None else anchored['n'])} of "
                f"{len(entity_rows)} entity mentions reference Chunk nodes that were "
                f"never written for source_document_id={source_document_id}."
            )

    def endpoint_ids(name: str, entity_type: str) -> set[str]:
        if entity_type:
            return entity_ids_by_type_and_name.get((entity_type, name), set())
        return entity_ids_by_name.get(name, set())

    written_relationships = 0
    skipped_relationships = 0
    relationship_rows_by_type: dict[str, list[dict[str, int | str]]] = {}
    for relationship in relationships:
        source_ids = endpoint_ids(relationship.source_name, relationship.source_type)
        target_ids = endpoint_ids(relationship.target_name, relationship.target_type)
        if len(source_ids) != 1 or len(target_ids) != 1:
            skipped_relationships += 1
            continue
        relationship_rows_by_type.setdefault(relationship.relationship_type, []).append(
            {
                "source_id": next(iter(source_ids)),
                "target_id": next(iter(target_ids)),
                "chunk_index": relationship.chunk_index,
            }
        )
        written_relationships += 1

    # Relationship types cannot be Cypher parameters, so batching is per type
    # (bounded by the ontology). Every interpolated type passed
    # validate_relationship_type at the top of this function.
    for relationship_type, relationship_rows in relationship_rows_by_type.items():
        db_session.run(
            "UNWIND $relationships AS rel "
            "MATCH (a:Entity {entity_id: rel.source_id}) "
            "MATCH (b:Entity {entity_id: rel.target_id}) "
            f"MERGE (a)-[r:{relationship_type}]->(b) "
            "SET r.source_document_id = $source_document_id, "
            "    r.connection_id = $connection_id, "
            "    r.drive_file_id = $drive_file_id, "
            "    r.source_permissions_version = $source_permissions_version, "
            "    r.chunk_index = rel.chunk_index",
            **provenance,
            relationships=relationship_rows,
        )

    return {
        "entities": len(written_entity_ids),
        "relationships": written_relationships,
        "relationships_skipped": skipped_relationships,
    }
