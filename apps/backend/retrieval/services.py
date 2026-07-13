"""Query orchestration with permission checks ahead of every retrieval."""

import logging
from dataclasses import dataclass

from django.conf import settings

from authorization.lookup import allowed_source_document_ids
from integrations.models import SourceDocument
from retrieval.answers import AnswerGenerator, build_answer_generator
from retrieval.context import assemble_context
from retrieval.neo4j import Neo4jPermissionSafeRetriever
from retrieval.types import RetrievalEvidence

logger = logging.getLogger(__name__)

REFUSAL_ANSWER = "I do not have enough accessible context to answer that."
REFUSAL_REASON = "insufficient_accessible_context"


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


def answer_query(
    question: str,
    user_email: str,
    *,
    allowed_lookup=None,
    retriever: Neo4jPermissionSafeRetriever | None = None,
    answer_generator: AnswerGenerator | None = None,
) -> QueryResult:
    """Return an answer only after authorization, retrieval, and evidence gates."""
    lookup = allowed_lookup or allowed_source_document_ids
    try:
        allowed_ids = tuple(lookup(user_email))
        if not allowed_ids:
            return _refusal()

        retriever = retriever or Neo4jPermissionSafeRetriever()
        evidence = retriever.retrieve(question, allowed_ids)
        evidence_ids = {item.source_document_id for item in (*evidence.chunks, *evidence.facts)}
        allowed_set = {value for value in allowed_ids if type(value) is int}
        candidate_ids = evidence_ids & allowed_set
        if not candidate_ids:
            return _refusal()

        # Recheck the shared permission-evidence predicate after Neo4j returns.
        # This can only narrow the SpiceDB allowlist; PostgreSQL never grants access.
        documents = {
            document.pk: document
            for document in SourceDocument.objects.permission_verified().filter(
                pk__in=candidate_ids
            )
        }
        safe_evidence = RetrievalEvidence(
            chunks=tuple(item for item in evidence.chunks if item.source_document_id in documents),
            facts=tuple(item for item in evidence.facts if item.source_document_id in documents),
        )
        if not safe_evidence.chunks and not safe_evidence.facts:
            return _refusal()

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
