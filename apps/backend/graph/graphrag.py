"""neo4j-graphrag-backed extraction adapter (ADR-010).

Only the package's extraction component is used, driven chunk-by-chunk so
every entity and relationship keeps an exact chunk_index. Writing stays in
graph/writer.py (our fail-closed writers), and the package's entity resolver
is deliberately not used — cross-document entity merging is a permission
hazard (see ADR-010).

The declared ontology is passed to the engine as a closed GraphSchema
(additional types disallowed), but that is grounding for the LLM, not the
enforcement point: everything returned still goes through
validate_extraction_result in the pipeline.
"""

import asyncio

from django.conf import settings
from neo4j_graphrag.exceptions import LLMGenerationError
from neo4j_graphrag.experimental.components.entity_relation_extractor import (
    LLMEntityRelationExtractor,
    OnError,
)
from neo4j_graphrag.experimental.components.schema import (
    GraphSchema,
    NodeType,
    PropertyType,
    RelationshipType,
)
from neo4j_graphrag.experimental.components.types import TextChunk
from neo4j_graphrag.llm import LLMInterface
from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError

from graph.extraction import (
    ExtractedChunk,
    ExtractedEntity,
    ExtractedRelationship,
    ExtractionResult,
    ParagraphChunkExtractor,
)
from graph.ontology import ENTITY_TYPES, RELATIONSHIP_TYPES


class MalformedModelOutputError(Exception):
    """The extraction model returned output the engine could not parse.

    At temperature 0 the provider is still nondeterministic in practice, so a
    fresh attempt on the identical chunk regularly succeeds — this is a
    transient quality failure, not a bug in our pipeline.
    """


# Transient provider failures this engine's extraction can hit. Owned here so
# the task layer never has to know which LLM stack an engine is built on.
RETRYABLE_LLM_EXCEPTIONS = (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
    MalformedModelOutputError,
)


def ontology_graph_schema() -> GraphSchema:
    return GraphSchema(
        node_types=tuple(
            NodeType(
                label=entity_type,
                properties=[PropertyType(name="name", type="STRING", required=True)],
            )
            for entity_type in sorted(ENTITY_TYPES)
        ),
        relationship_types=tuple(
            RelationshipType(label=relationship_type)
            for relationship_type in sorted(RELATIONSHIP_TYPES)
        ),
        additional_node_types=False,
        additional_relationship_types=False,
    )


class GraphRAGExtractor:
    """ExtractionAdapter backed by neo4j-graphrag's LLM extraction."""

    def __init__(
        self,
        llm: LLMInterface,
        *,
        chunker: ParagraphChunkExtractor | None = None,
        max_concurrent_llm_calls: int = 4,
    ):
        # OnError.RAISE: a chunk whose extraction fails aborts the document
        # instead of being silently dropped — fail closed, retry later.
        self._extractor = LLMEntityRelationExtractor(
            llm=llm,
            create_lexical_graph=False,
            on_error=OnError.RAISE,
        )
        self._schema = ontology_graph_schema()
        self._chunker = chunker or ParagraphChunkExtractor()
        if max_concurrent_llm_calls < 1:
            raise ValueError("max_concurrent_llm_calls must be positive")
        self._max_concurrent_llm_calls = max_concurrent_llm_calls

    def extract(self, text: str) -> ExtractionResult:
        chunks = self._chunker.extract(text).chunks
        entities, relationships = asyncio.run(self._extract_all(chunks))
        return ExtractionResult(
            chunks=chunks,
            entities=tuple(entities),
            relationships=tuple(relationships),
        )

    async def _extract_all(
        self, chunks: tuple[ExtractedChunk, ...]
    ) -> tuple[list[ExtractedEntity], list[ExtractedRelationship]]:
        # Chunk extractions are independent LLM calls; run them concurrently
        # (bounded, for provider rate limits). gather preserves input order,
        # so output ordering stays deterministic. Any failure still aborts
        # the whole document (OnError.RAISE — fail closed).
        semaphore = asyncio.Semaphore(self._max_concurrent_llm_calls)

        async def extract_chunk(chunk: ExtractedChunk):
            async with semaphore:
                try:
                    return await self._extractor.extract_for_chunk(
                        self._schema,
                        "",
                        TextChunk(text=chunk.text, index=chunk.index),
                    )
                except (LLMGenerationError, TypeError, KeyError, IndexError, AttributeError) as exc:
                    # The engine choked on what the model produced (None
                    # content, truncated JSON, missing fields). Classified
                    # retryable so the task-level backoff gets a fresh sample.
                    raise MalformedModelOutputError(type(exc).__name__) from exc

        chunk_graphs = await asyncio.gather(*(extract_chunk(chunk) for chunk in chunks))

        entities: list[ExtractedEntity] = []
        relationships: list[ExtractedRelationship] = []
        for chunk, chunk_graph in zip(chunks, chunk_graphs, strict=True):
            endpoints_by_node_id: dict[str, tuple[str, str]] = {}
            for node in chunk_graph.nodes:
                name = (node.properties or {}).get("name") or node.id
                endpoints_by_node_id[node.id] = (node.label, name)
                entities.append(
                    ExtractedEntity(
                        entity_type=node.label,
                        name=name,
                        chunk_index=chunk.index,
                    )
                )
            for relationship in chunk_graph.relationships:
                source = endpoints_by_node_id.get(relationship.start_node_id)
                target = endpoints_by_node_id.get(relationship.end_node_id)
                if source is None or target is None:
                    # The LLM referenced a node id it never declared — there
                    # is no entity to anchor the edge to, so it cannot be
                    # stored with valid provenance. Dropped, not guessed.
                    continue
                source_type, source_name = source
                target_type, target_name = target
                relationships.append(
                    ExtractedRelationship(
                        relationship_type=relationship.type,
                        source_name=source_name,
                        target_name=target_name,
                        chunk_index=chunk.index,
                        # Types ride along so the writer can tell same-named
                        # entities of different types apart.
                        source_type=source_type,
                        target_type=target_type,
                    )
                )
        return entities, relationships


def build_graphrag_extractor() -> GraphRAGExtractor:
    from neo4j_graphrag.llm import OpenAILLM

    model_params: dict = {"temperature": 0.0, "response_format": {"type": "json_object"}}
    if settings.GRAPH_EXTRACTION_FALLBACK_MODELS:
        # OpenRouter's model-fallback routing: `model` stays the primary and
        # `models` lists the rescue chain. Passed via extra_body because it is
        # an OpenRouter extension the OpenAI SDK does not model natively.
        model_params["extra_body"] = {"models": list(settings.GRAPH_EXTRACTION_FALLBACK_MODELS)}
    llm = OpenAILLM(
        model_name=settings.GRAPH_EXTRACTION_MODEL,
        model_params=model_params,
        api_key=settings.OPENROUTER_API_KEY,
        base_url=settings.OPENROUTER_BASE_URL,
    )
    return GraphRAGExtractor(
        llm=llm,
        chunker=ParagraphChunkExtractor(
            max_chars=settings.GRAPH_EXTRACTION_CHUNK_MAX_CHARS,
            overlap_chars=settings.GRAPH_EXTRACTION_CHUNK_OVERLAP_CHARS,
        ),
        max_concurrent_llm_calls=settings.GRAPH_EXTRACTION_MAX_CONCURRENT_LLM_CALLS,
    )
