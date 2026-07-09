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
        "    d.title = $title, "
        "    d.mime_type = $mime_type, "
        "    d.drive_url = $drive_url",
        **provenance,
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
    # loudly instead. Callers upsert_document() first.
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
    for chunk in chunks:
        embedding = embedding_vectors.get(chunk.index)
        embedding_property = ", embedding: $embedding" if embedding is not None else ""
        db_session.run(
            "MATCH (d:Document {source_document_id: $source_document_id}) "
            "CREATE (c:Chunk {chunk_id: $chunk_id, "
            "                 chunk_index: $chunk_index, "
            "                 text: $text, "
            "                 source_document_id: $source_document_id, "
            "                 connection_id: $connection_id, "
            "                 drive_file_id: $drive_file_id, "
            "                 source_permissions_version: $source_permissions_version"
            f"{embedding_property}}}) "
            f"CREATE (c)-[r:{CHUNK_DOCUMENT_RELATIONSHIP}]->(d) "
            "SET r.source_document_id = $source_document_id, "
            "    r.connection_id = $connection_id, "
            "    r.drive_file_id = $drive_file_id, "
            "    r.source_permissions_version = $source_permissions_version",
            **provenance,
            chunk_id=f"{provenance['source_document_id']}:{chunk.index}",
            chunk_index=chunk.index,
            text=chunk.text,
            embedding=embedding,
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

    Relationships resolve their endpoints by name against this document's
    own entities; an unresolvable or ambiguous name is counted and skipped —
    an edge that can't be anchored to known entities can't be stored with
    valid provenance.
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
    written_entity_ids: set[str] = set()
    for entity in entities:
        entity_id = _entity_id(source_document_id, entity)
        entity_ids_by_name.setdefault(entity.name, set()).add(entity_id)
        written_entity_ids.add(entity_id)
        created = db_session.run(
            "MATCH (c:Chunk {chunk_id: $chunk_id}) "
            "MERGE (e:Entity {entity_id: $entity_id}) "
            "SET e.name = $name, "
            "    e.entity_type = $entity_type, "
            "    e.source_document_id = $source_document_id, "
            "    e.connection_id = $connection_id, "
            "    e.drive_file_id = $drive_file_id, "
            "    e.source_permissions_version = $source_permissions_version "
            f"MERGE (c)-[m:{CHUNK_ENTITY_RELATIONSHIP}]->(e) "
            "SET m.source_document_id = $source_document_id, "
            "    m.connection_id = $connection_id, "
            "    m.drive_file_id = $drive_file_id, "
            "    m.source_permissions_version = $source_permissions_version "
            "RETURN count(e) AS n",
            **provenance,
            entity_id=entity_id,
            name=entity.name,
            entity_type=entity.entity_type,
            chunk_id=f"{source_document_id}:{entity.chunk_index}",
        ).single()
        if created is None or created["n"] == 0:
            raise ChunkNodeMissingError(
                f"No Chunk node {source_document_id}:{entity.chunk_index} to anchor an entity."
            )

    written_relationships = 0
    skipped_relationships = 0
    for relationship in relationships:
        source_ids = entity_ids_by_name.get(relationship.source_name, set())
        target_ids = entity_ids_by_name.get(relationship.target_name, set())
        if len(source_ids) != 1 or len(target_ids) != 1:
            skipped_relationships += 1
            continue
        db_session.run(
            "MATCH (a:Entity {entity_id: $source_id}) "
            "MATCH (b:Entity {entity_id: $target_id}) "
            f"MERGE (a)-[r:{relationship.relationship_type}]->(b) "
            "SET r.source_document_id = $source_document_id, "
            "    r.connection_id = $connection_id, "
            "    r.drive_file_id = $drive_file_id, "
            "    r.source_permissions_version = $source_permissions_version, "
            "    r.chunk_index = $chunk_index",
            **provenance,
            source_id=next(iter(source_ids)),
            target_id=next(iter(target_ids)),
            chunk_index=relationship.chunk_index,
        )
        written_relationships += 1

    return {
        "entities": len(written_entity_ids),
        "relationships": written_relationships,
        "relationships_skipped": skipped_relationships,
    }
