"""Neo4j writers for Document and Chunk nodes.

Provenance or exclusion (project invariant 2): every node written here
carries source-document provenance, and a document whose identity fields are
incomplete is refused outright — never written with gaps for someone to
backfill later.
"""

from graph.extraction import ExtractedChunk
from graph.ontology import validate_relationship_type
from integrations.models import SourceDocument

# Structural edge from a chunk to the document it was derived from. Drawn
# from the declared ontology like every other relationship type.
CHUNK_DOCUMENT_RELATIONSHIP = "belongs_to"
validate_relationship_type(CHUNK_DOCUMENT_RELATIONSHIP)


class MissingProvenanceError(ValueError):
    """Raised instead of writing a graph element with incomplete provenance."""


class DocumentNodeMissingError(ValueError):
    """Raised instead of silently dropping chunks when the Document node is absent."""


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
    db_session, document: SourceDocument, chunks: tuple[ExtractedChunk, ...]
) -> int:
    """Replace the document's chunk set (delete then create).

    Chunks are derived content, so re-extraction always rewrites the full set;
    merging per-chunk would leave orphans behind when a document shrinks.
    """
    provenance = document_provenance(document)
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
        db_session.run(
            "MATCH (d:Document {source_document_id: $source_document_id}) "
            "CREATE (c:Chunk {chunk_id: $chunk_id, "
            "                 chunk_index: $chunk_index, "
            "                 text: $text, "
            "                 source_document_id: $source_document_id, "
            "                 connection_id: $connection_id, "
            "                 drive_file_id: $drive_file_id, "
            "                 source_permissions_version: $source_permissions_version}) "
            f"CREATE (c)-[:{CHUNK_DOCUMENT_RELATIONSHIP}]->(d)",
            **provenance,
            chunk_id=f"{provenance['source_document_id']}:{chunk.index}",
            chunk_index=chunk.index,
            text=chunk.text,
        )
    return len(chunks)
