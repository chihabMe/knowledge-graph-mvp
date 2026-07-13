"""Document → chunks → Neo4j extraction pipeline.

Runs for every stored document regardless of retrieval_eligible: permission
enforcement lives at retrieval (SpiceDB allowlist + the provenance guard),
and Phase 2 only re-queues extraction when content changes — skipping
ineligible documents here would leave them permanently absent from the graph
after their permissions later become readable. Nothing in this module is a
retrieval path; no content leaves the graph store.
"""

from io import BytesIO

from django.conf import settings
from django.db import transaction
from neo4j.exceptions import ServiceUnavailable, SessionExpired, TransientError
from pypdf import PdfReader
from pypdf.errors import DependencyError, PyPdfError

from graph.db import write_transaction
from graph.embeddings import EmbeddingAdapter, NoOpEmbeddingAdapter
from graph.extraction import ExtractionAdapter, ParagraphChunkExtractor, validate_extraction_result
from graph.writer import replace_document_chunks, replace_document_entities, upsert_document
from integrations.models import SourceDocument, SourceDocumentContent


def get_extraction_adapter() -> ExtractionAdapter:
    # Single swap point for the engine choice (ADR-010). The graphrag import
    # stays local so the paragraph baseline never touches the LLM stack.
    if settings.GRAPH_EXTRACTION_ENGINE == "neo4j_graphrag":
        from graph.graphrag import build_graphrag_extractor

        return build_graphrag_extractor()
    return ParagraphChunkExtractor(
        max_chars=settings.GRAPH_EXTRACTION_CHUNK_MAX_CHARS,
        overlap_chars=settings.GRAPH_EXTRACTION_CHUNK_OVERLAP_CHARS,
    )


def get_embedding_adapter() -> EmbeddingAdapter:
    return NoOpEmbeddingAdapter()


def get_retryable_extraction_exceptions() -> tuple[type[BaseException], ...]:
    """Transient failure types for the configured engine plus graph-store infra.

    The engine-specific set lives with the engine (the task layer must not
    know which provider stack an adapter is built on); the graph store and
    network layers are this pipeline's own infrastructure, so they are
    classified here.
    """
    infrastructure = (OSError, ServiceUnavailable, SessionExpired, TransientError)
    if settings.GRAPH_EXTRACTION_ENGINE == "neo4j_graphrag":
        from graph.graphrag import RETRYABLE_LLM_EXCEPTIONS

        return infrastructure + RETRYABLE_LLM_EXCEPTIONS
    return infrastructure


def _stored_content_text(stored: SourceDocumentContent) -> tuple[str | None, str | None]:
    """Return extracted text or a controlled, content-free skip status."""
    content = bytes(stored.content)
    if stored.exported_mime_type.startswith("text/"):
        try:
            return content.decode("utf-8"), None
        except UnicodeDecodeError:
            return None, "skipped_decode_error"

    if stored.exported_mime_type == "application/pdf":
        try:
            reader = PdfReader(BytesIO(content), strict=False)
            pages = [page.extract_text() or "" for page in reader.pages]
        except (DependencyError, OSError, PyPdfError, TypeError, ValueError):
            return None, "skipped_pdf_decode_error"
        text = "\n\n".join(page.strip() for page in pages if page.strip())
        if not text:
            return None, "skipped_pdf_no_extractable_text"
        return text, None

    return None, "skipped_unsupported_mime_type"


def extract_document_to_graph(
    source_document_id: int, expected_content_hash: str | None = None
) -> dict[str, int | str]:
    """Extract one stored document into Document + Chunk nodes.

    The return value flows into the Celery result backend — ids, status, and
    counts only, never text.
    """
    document = SourceDocument.objects.select_related("content").get(pk=source_document_id)

    try:
        stored = document.content
    except SourceDocumentContent.DoesNotExist:
        return {"source_document_id": source_document_id, "status": "skipped_no_content"}
    text, skip_status = _stored_content_text(stored)
    if skip_status:
        # Fail closed without letting byte values or parser errors from client
        # content reach logs or the Celery result backend.
        return {"source_document_id": source_document_id, "status": skip_status}
    assert text is not None

    expected_content_hash = expected_content_hash or stored.content_hash
    if (
        not expected_content_hash
        or document.content_hash != expected_content_hash
        or stored.content_hash != expected_content_hash
    ):
        return {"source_document_id": source_document_id, "status": "skipped_stale_content_version"}

    result = validate_extraction_result(get_extraction_adapter().extract(text))
    chunk_embeddings = get_embedding_adapter().embed_chunks(result.chunks)

    # Do not hold a Postgres row lock while the LLM runs. Instead, lock only
    # around the final version check and graph replacement. A concurrent content
    # refresh then waits, resets the new version to PENDING, and queues its own
    # extraction after this older write releases the lock.
    with transaction.atomic():
        current_document = SourceDocument.objects.select_for_update().get(pk=source_document_id)
        try:
            current_stored = current_document.content
        except SourceDocumentContent.DoesNotExist:
            return {
                "source_document_id": source_document_id,
                "status": "skipped_stale_content_version",
            }
        if (
            current_document.content_hash != expected_content_hash
            or current_stored.content_hash != expected_content_hash
        ):
            return {
                "source_document_id": source_document_id,
                "status": "skipped_stale_content_version",
            }

        # The four writer stages are one replacement operation. In particular,
        # chunk deletion must never commit before replacement chunks/entities do.
        with write_transaction() as db_transaction:
            upsert_document(db_transaction, current_document)
            written = replace_document_chunks(
                db_transaction,
                current_document,
                result.chunks,
                chunk_embeddings=chunk_embeddings,
                embedding_dimensions=settings.GRAPH_CHUNK_EMBEDDING_DIMENSIONS,
            )
            entity_counts = replace_document_entities(
                db_transaction, current_document, result.entities, result.relationships
            )

    return {
        "source_document_id": source_document_id,
        "status": "extracted",
        "chunks": written,
        **entity_counts,
    }
