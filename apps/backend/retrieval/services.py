"""Query orchestration with permission checks ahead of every retrieval."""

import logging
from dataclasses import dataclass

from django.conf import settings

from authorization.lookup import (
    allowed_source_document_ids,
    fresh_authorized_documents,
    has_pending_authorized_content,
)
from retrieval.answers import AnswerGenerator, build_answer_generator
from retrieval.context import assemble_context
from retrieval.neo4j import Neo4jPermissionSafeRetriever
from retrieval.types import RetrievalEvidence

logger = logging.getLogger(__name__)

REFUSAL_ANSWER = "I do not have enough accessible context to answer that."
REFUSAL_REASON = "insufficient_accessible_context"
UPDATING_ANSWER = (
    "Some of your accessible documents are being updated. Please try again in a few minutes."
)
UPDATING_REASON = "content_update_in_progress"


@dataclass(frozen=True)
class QueryResult:
    answer: str
    citations: tuple[dict[str, str], ...]
    refused: bool
    reason: str | None

    def as_payload(self) -> dict:
        return {
            "answer": self.answer,
            "citations": list(self.citations),
            "refused": self.refused,
            "reason": self.reason,
        }


def _refusal() -> QueryResult:
    return QueryResult(
        answer=REFUSAL_ANSWER,
        citations=(),
        refused=True,
        reason=REFUSAL_REASON,
    )


def _updating() -> QueryResult:
    return QueryResult(
        answer=UPDATING_ANSWER,
        citations=(),
        refused=True,
        reason=UPDATING_REASON,
    )


def _refusal_for_user(user_email: str, pending_content_lookup) -> QueryResult:
    try:
        if pending_content_lookup and pending_content_lookup(user_email):
            return _updating()
    except Exception as exc:
        logger.warning(
            "pending content state check failed closed: %s.%s",
            type(exc).__module__,
            type(exc).__name__,
        )
    return _refusal()


def _content_current(item, documents) -> bool:
    """Require the item's extracted content version to match the document's
    current content hash. Superseded, empty, and unknown versions are all
    excluded, so chunks from a replaced document version cannot reach answer
    context while re-extraction is pending or failed."""
    document = documents.get(item.source_document_id)
    return (
        document is not None
        and bool(item.content_version)
        and item.content_version == document.content_hash
    )


def answer_query(
    question: str,
    user_email: str,
    *,
    allowed_lookup=None,
    retriever: Neo4jPermissionSafeRetriever | None = None,
    answer_generator: AnswerGenerator | None = None,
    pending_content_lookup=None,
) -> QueryResult:
    """Return an answer only after authorization, retrieval, and evidence gates."""
    lookup = allowed_lookup or allowed_source_document_ids
    # Test-only/custom callers may supply an alternate allowlist without a
    # matching authorization backend. Production uses the real pending-content
    # check, which retains the same SpiceDB plus fresh-evidence requirements.
    pending_lookup = pending_content_lookup
    if pending_lookup is None and allowed_lookup is None:
        pending_lookup = has_pending_authorized_content
    try:
        allowed_ids = tuple(lookup(user_email))
        if not allowed_ids:
            return _refusal_for_user(user_email, pending_lookup)

        retriever = retriever or Neo4jPermissionSafeRetriever()
        evidence = retriever.retrieve(question, allowed_ids)
        evidence_ids = {item.source_document_id for item in (*evidence.chunks, *evidence.facts)}
        allowed_set = {value for value in allowed_ids if type(value) is int}
        candidate_ids = evidence_ids & allowed_set
        if not candidate_ids:
            return _refusal_for_user(user_email, pending_lookup)

        # Recheck the current mode's PostgreSQL deny evidence after Neo4j
        # returns, and gate every item on content currency. Both checks can
        # only narrow the SpiceDB allowlist; they never grant.
        documents = fresh_authorized_documents(user_email, candidate_ids)
        safe_evidence = RetrievalEvidence(
            chunks=tuple(item for item in evidence.chunks if _content_current(item, documents)),
            facts=tuple(item for item in evidence.facts if _content_current(item, documents)),
        )
        if not safe_evidence.chunks and not safe_evidence.facts:
            return _refusal_for_user(user_email, pending_lookup)

        context = assemble_context(
            safe_evidence,
            max_chars=settings.QUERY_CONTEXT_MAX_CHARS,
        )
        if not context.text:
            return _refusal()

        generator = answer_generator or build_answer_generator()
        generated = generator.generate(question, context)
        if not generated.supported:
            return _refusal()

        citations: list[dict[str, str]] = []
        seen_citations: set[tuple[int, str]] = set()
        for item in (*context.chunks, *context.facts):
            key = (item.source_document_id, item.chunk_id)
            if key in seen_citations:
                continue
            seen_citations.add(key)
            document = documents[item.source_document_id]
            citations.append(
                {
                    "title": document.title,
                    "drive_file_id": document.drive_file_id,
                    "drive_url": document.drive_url,
                    "chunk_id": item.chunk_id,
                }
            )

        return QueryResult(
            answer=generated.answer,
            citations=tuple(citations),
            refused=False,
            reason=None,
        )
    except Exception as exc:
        # Class only: never log the question, identity, context, or remote payload.
        logger.warning(
            "permission-safe query failed closed: %s.%s",
            type(exc).__module__,
            type(exc).__name__,
        )
        return _refusal()
