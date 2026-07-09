"""Document → chunks → Neo4j extraction pipeline.

Runs for every stored document regardless of retrieval_eligible: permission
enforcement lives at retrieval (SpiceDB allowlist + the provenance guard),
and Phase 2 only re-queues extraction when content changes — skipping
ineligible documents here would leave them permanently absent from the graph
after their permissions later become readable. Nothing in this module is a
retrieval path; no content leaves the graph store.
"""

from graph.db import session
from graph.extraction import ExtractionAdapter, ParagraphChunkExtractor, validate_extraction_result
from graph.writer import replace_document_chunks, upsert_document
from integrations.models import SourceDocument, SourceDocumentContent


def get_extraction_adapter() -> ExtractionAdapter:
    # Single swap point for the engine choice (neo4j-graphrag / Graphify /
    # Graphiti evaluation is an open Phase 3 task). The baseline is
    # deterministic and cannot invent entities or relationships.
    return ParagraphChunkExtractor()


def extract_document_to_graph(source_document_id: int) -> dict[str, int | str]:
    """Extract one stored document into Document + Chunk nodes.

    The return value flows into the Celery result backend — ids, status, and
    counts only, never text.
    """
    document = SourceDocument.objects.get(pk=source_document_id)

    try:
        stored = document.content
    except SourceDocumentContent.DoesNotExist:
        return {"source_document_id": source_document_id, "status": "skipped_no_content"}
    if not stored.exported_mime_type.startswith("text/"):
        return {
            "source_document_id": source_document_id,
            "status": "skipped_unsupported_mime_type",
        }
    try:
        text = bytes(stored.content).decode("utf-8")
    except UnicodeDecodeError:
        # Fail closed without letting byte values from client content reach
        # logs or the result backend via the exception message.
        return {"source_document_id": source_document_id, "status": "skipped_decode_error"}

    result = validate_extraction_result(get_extraction_adapter().extract(text))

    with session() as db_session:
        upsert_document(db_session, document)
        written = replace_document_chunks(db_session, document, result.chunks)

    return {
        "source_document_id": source_document_id,
        "status": "extracted",
        "chunks": written,
    }
