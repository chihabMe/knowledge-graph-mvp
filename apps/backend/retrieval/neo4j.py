"""Permission-constrained Neo4j retrieval paths for Phase 5.

Both paths apply the SpiceDB-derived document allowlist and the shared
provenance guard inside Cypher. Post-query checks are defense in depth only;
they are never a substitute for the query constraints.
"""

import re
from collections.abc import Mapping
from dataclasses import replace

from django.conf import settings

from graph.db import session
from graph.embeddings import EmbeddingAdapter, build_embedding_adapter
from graph.guard import PROVENANCE_FIELDS, provenance_where, record_has_provenance
from retrieval.types import RetrievalEvidence, RetrievedChunk, RetrievedFact

CHUNK_RETRIEVAL_CYPHER = f"""
MATCH (chunk:Chunk)-[belongs:belongs_to]->(document:Document)
WHERE {provenance_where("chunk")}
  AND {provenance_where("belongs")}
  AND {provenance_where("document")}
  AND chunk.source_document_id = belongs.source_document_id
  AND chunk.source_document_id = document.source_document_id
WITH chunk, belongs, document,
     [term IN $query_terms
      WHERE toLower(coalesce(chunk.text, '')) CONTAINS term] AS matching_terms
WHERE size(matching_terms) >= $minimum_should_match
RETURN properties(chunk) AS chunk,
       properties(belongs) AS belongs,
       properties(document) AS document,
       size(matching_terms) AS relevance
ORDER BY relevance DESC, chunk.source_document_id, chunk.chunk_index
LIMIT $limit
""".strip()

FACT_RETRIEVAL_CYPHER = f"""
MATCH (source:Entity)-[fact]->(target:Entity)
WHERE {provenance_where("source")}
  AND {provenance_where("fact")}
  AND {provenance_where("target")}
  AND source.source_document_id = fact.source_document_id
  AND source.source_document_id = target.source_document_id
WITH source, fact, target
MATCH (chunk:Chunk)-[belongs:belongs_to]->(document:Document)
WHERE {provenance_where("chunk")}
  AND {provenance_where("belongs")}
  AND {provenance_where("document")}
  AND chunk.source_document_id = fact.source_document_id
  AND chunk.chunk_index = fact.chunk_index
  AND chunk.source_document_id = belongs.source_document_id
  AND chunk.source_document_id = document.source_document_id
WITH source, fact, target, chunk, belongs, document,
     [term IN $query_terms
      WHERE toLower(coalesce(source.name, '')) CONTAINS term
         OR toLower(coalesce(target.name, '')) CONTAINS term
         OR toLower(coalesce(chunk.text, '')) CONTAINS term] AS matching_terms
WHERE size(matching_terms) >= $minimum_should_match
RETURN properties(source) AS source,
       properties(fact) AS fact,
       properties(target) AS target,
       properties(chunk) AS chunk,
       properties(belongs) AS belongs,
       properties(document) AS document,
       type(fact) AS relationship_type,
       size(matching_terms) AS relevance
ORDER BY relevance DESC, chunk.source_document_id, chunk.chunk_index
LIMIT $limit
""".strip()


def vector_retrieval_cypher(similarity_function: str) -> str:
    """Build a pre-filtered vector query for one validated similarity function.

    Neo4j 5's vector-index procedure cannot pre-filter candidates by an
    arbitrary document allowlist. This query deliberately matches permitted,
    provenance-complete chunks first and only then computes vector similarity.
    It trades index acceleration for the non-negotiable authorization order.
    """
    if similarity_function not in {"cosine", "euclidean"}:
        raise ValueError(f"Unsupported vector similarity function: {similarity_function!r}.")
    return f"""
MATCH (chunk:Chunk)-[belongs:belongs_to]->(document:Document)
WHERE {provenance_where("chunk")}
  AND {provenance_where("belongs")}
  AND {provenance_where("document")}
  AND chunk.source_document_id = belongs.source_document_id
  AND chunk.source_document_id = document.source_document_id
  AND chunk.embedding IS NOT NULL
  AND size(chunk.embedding) = $embedding_dimensions
WITH chunk, belongs, document,
     vector.similarity.{similarity_function}(chunk.embedding, $query_embedding) AS score
WHERE score >= $minimum_vector_score
RETURN properties(chunk) AS chunk,
       properties(belongs) AS belongs,
       properties(document) AS document,
       score
ORDER BY score DESC, chunk.source_document_id, chunk.chunk_index
LIMIT $limit
""".strip()


VECTOR_RETRIEVAL_CYPHER = vector_retrieval_cypher("cosine")

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
_QUERY_STOP_WORDS = frozenset(
    {
        "are",
        "can",
        "could",
        "did",
        "does",
        "for",
        "from",
        "has",
        "have",
        "how",
        "into",
        "the",
        "their",
        "there",
        "these",
        "this",
        "those",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
    }
)


def question_terms(question: str, *, limit: int = 12) -> tuple[str, ...]:
    """Return bounded, deduplicated terms for the baseline non-vector search."""
    terms: list[str] = []
    seen: set[str] = set()
    for match in _TOKEN_RE.finditer(question.casefold()):
        term = match.group(0)
        if not 3 <= len(term) <= 64 or term in seen or term in _QUERY_STOP_WORDS:
            continue
        seen.add(term)
        terms.append(term)
        if len(terms) == limit:
            break
    return tuple(terms)


def _record_data(record) -> Mapping:
    data = record.data() if hasattr(record, "data") else record
    return data if isinstance(data, Mapping) else {}


def _properties(data: Mapping, key: str) -> dict:
    value = data.get(key)
    return dict(value) if isinstance(value, Mapping) else {}


def _has_consistent_allowed_provenance(
    properties: tuple[dict, ...], allowed_source_document_ids: frozenset[int]
) -> bool:
    if not properties or not all(record_has_provenance(item) for item in properties):
        return False
    signature = tuple(properties[0].get(field) for field in PROVENANCE_FIELDS)
    if any(
        tuple(item.get(field) for field in PROVENANCE_FIELDS) != signature
        for item in properties[1:]
    ):
        return False
    source_document_id = properties[0].get("source_document_id")
    return type(source_document_id) is int and source_document_id in allowed_source_document_ids


def fuse_chunk_rankings(
    rankings: tuple[tuple[str, tuple[RetrievedChunk, ...]], ...], *, limit: int
) -> tuple[RetrievedChunk, ...]:
    """Fuse bounded vector and keyword rankings without comparing raw scores."""
    scores: dict[tuple[int, str], float] = {}
    chunks: dict[tuple[int, str], RetrievedChunk] = {}
    modes: dict[tuple[int, str], set[str]] = {}
    for mode, ranking in rankings:
        for rank, chunk in enumerate(ranking, start=1):
            key = (chunk.source_document_id, chunk.chunk_id)
            chunks.setdefault(key, chunk)
            modes.setdefault(key, set()).add(mode)
            scores[key] = scores.get(key, 0.0) + 1.0 / (60 + rank)

    ordered_keys = sorted(scores, key=lambda key: (-scores[key], key[0], key[1]))[:limit]
    return tuple(
        replace(
            chunks[key],
            relevance=scores[key],
            retrieval_modes=tuple(sorted(modes[key])),
        )
        for key in ordered_keys
    )


class Neo4jPermissionSafeRetriever:
    """Run bounded hybrid chunk and one-hop fact retrieval over allowed provenance."""

    def __init__(
        self,
        *,
        limit: int | None = None,
        embedding_adapter: EmbeddingAdapter | None = None,
        minimum_vector_score: float | None = None,
        vector_similarity: str | None = None,
    ):
        limit = settings.QUERY_RETRIEVAL_LIMIT if limit is None else limit
        if not 1 <= limit <= 20:
            raise ValueError("Retrieval limit must be between 1 and 20.")
        minimum_vector_score = (
            settings.QUERY_VECTOR_MIN_SCORE
            if minimum_vector_score is None
            else minimum_vector_score
        )
        if not 0.0 <= minimum_vector_score <= 1.0:
            raise ValueError("Minimum vector score must be between 0 and 1.")
        self._limit = limit
        self._minimum_vector_score = minimum_vector_score
        self._embedding_adapter = embedding_adapter or build_embedding_adapter()
        self._vector_similarity = vector_similarity or settings.GRAPH_CHUNK_VECTOR_SIMILARITY
        self._vector_cypher = vector_retrieval_cypher(self._vector_similarity)

    def retrieve(
        self, question: str, allowed_source_document_ids: tuple[int, ...]
    ) -> RetrievalEvidence:
        allowed = frozenset(value for value in allowed_source_document_ids if type(value) is int)
        if not allowed:
            return RetrievalEvidence()

        terms = question_terms(question)
        query_embedding = self._embedding_adapter.embed_query(question)
        if query_embedding and len(query_embedding) != settings.GRAPH_CHUNK_EMBEDDING_DIMENSIONS:
            raise ValueError("Query embedding dimensions do not match the Chunk vector shape.")
        if not query_embedding and not terms:
            return RetrievalEvidence()

        parameters = {
            "allowed_source_document_ids": sorted(allowed),
            "query_terms": list(terms),
            # The baseline keyword path is deliberately conservative: one
            # generic overlap is not enough to expose a permitted document as
            # relevant context for a different question.
            "minimum_should_match": min(2, len(terms)),
            "limit": self._limit,
        }
        vector_records = []
        chunk_records = []
        fact_records = []
        with session() as db_session:
            if query_embedding:
                vector_records = list(
                    db_session.run(
                        self._vector_cypher,
                        allowed_source_document_ids=sorted(allowed),
                        query_embedding=list(query_embedding),
                        embedding_dimensions=len(query_embedding),
                        minimum_vector_score=self._minimum_vector_score,
                        limit=self._limit,
                    )
                )
            if terms:
                chunk_records = list(db_session.run(CHUNK_RETRIEVAL_CYPHER, **parameters))
                fact_records = list(db_session.run(FACT_RETRIEVAL_CYPHER, **parameters))

        vector_chunks = self._chunks(vector_records, allowed, mode="vector")
        keyword_chunks = self._chunks(chunk_records, allowed, mode="keyword")

        return RetrievalEvidence(
            chunks=fuse_chunk_rankings(
                (("vector", vector_chunks), ("keyword", keyword_chunks)),
                limit=self._limit,
            ),
            facts=self._facts(fact_records, allowed),
        )

    @staticmethod
    def _chunks(records, allowed: frozenset[int], *, mode: str) -> tuple[RetrievedChunk, ...]:
        chunks: list[RetrievedChunk] = []
        seen: set[tuple[int, str]] = set()
        for record in records:
            data = _record_data(record)
            chunk = _properties(data, "chunk")
            belongs = _properties(data, "belongs")
            document = _properties(data, "document")
            if not _has_consistent_allowed_provenance((chunk, belongs, document), allowed):
                continue
            chunk_id = chunk.get("chunk_id")
            text = chunk.get("text")
            raw_relevance = data.get("score", data.get("relevance", 0.0))
            source_document_id = chunk["source_document_id"]
            key = (source_document_id, chunk_id)
            if (
                not isinstance(chunk_id, str)
                or not chunk_id
                or not isinstance(text, str)
                or not text
                or isinstance(raw_relevance, bool)
                or not isinstance(raw_relevance, (int, float))
            ):
                continue
            if key in seen:
                continue
            seen.add(key)
            content_version = document.get("content_hash")
            chunks.append(
                RetrievedChunk(
                    source_document_id=source_document_id,
                    chunk_id=chunk_id,
                    text=text,
                    relevance=float(raw_relevance),
                    retrieval_modes=(mode,),
                    content_version=content_version if isinstance(content_version, str) else "",
                )
            )
        return tuple(chunks)

    @staticmethod
    def _facts(records, allowed: frozenset[int]) -> tuple[RetrievedFact, ...]:
        facts: list[RetrievedFact] = []
        seen: set[tuple[int, str, str, str, str]] = set()
        for record in records:
            data = _record_data(record)
            source = _properties(data, "source")
            fact = _properties(data, "fact")
            target = _properties(data, "target")
            chunk = _properties(data, "chunk")
            belongs = _properties(data, "belongs")
            document = _properties(data, "document")
            if not _has_consistent_allowed_provenance(
                (source, fact, target, chunk, belongs, document), allowed
            ):
                continue
            source_name = source.get("name")
            target_name = target.get("name")
            relationship_type = data.get("relationship_type")
            chunk_id = chunk.get("chunk_id")
            text = chunk.get("text")
            if not all(
                isinstance(value, str) and value
                for value in (source_name, target_name, relationship_type, chunk_id, text)
            ):
                continue
            source_document_id = source["source_document_id"]
            key = (
                source_document_id,
                chunk_id,
                source_name,
                relationship_type,
                target_name,
            )
            if key in seen:
                continue
            seen.add(key)
            content_version = document.get("content_hash")
            facts.append(
                RetrievedFact(
                    source_document_id=source_document_id,
                    chunk_id=chunk_id,
                    source_name=source_name,
                    relationship_type=relationship_type,
                    target_name=target_name,
                    text=text,
                    relevance=float(data.get("relevance", 0.0)),
                    retrieval_modes=("graph",),
                    content_version=content_version if isinstance(content_version, str) else "",
                )
            )
        return tuple(facts)
