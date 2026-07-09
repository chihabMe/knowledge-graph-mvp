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

from graph.extraction import (
    ExtractedChunk,
    ExtractedEntity,
    ExtractedRelationship,
    ExtractionResult,
    ParagraphChunkExtractor,
)
from graph.ontology import ENTITY_TYPES, RELATIONSHIP_TYPES


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

    def __init__(self, llm: LLMInterface):
        # OnError.RAISE: a chunk whose extraction fails aborts the document
        # instead of being silently dropped — fail closed, retry later.
        self._extractor = LLMEntityRelationExtractor(
            llm=llm,
            create_lexical_graph=False,
            on_error=OnError.RAISE,
        )
        self._schema = ontology_graph_schema()
        self._chunker = ParagraphChunkExtractor()

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
        entities: list[ExtractedEntity] = []
        relationships: list[ExtractedRelationship] = []
        for chunk in chunks:
            chunk_graph = await self._extractor.extract_for_chunk(
                self._schema,
                "",
                TextChunk(text=chunk.text, index=chunk.index),
            )
            names_by_node_id: dict[str, str] = {}
            for node in chunk_graph.nodes:
                name = (node.properties or {}).get("name") or node.id
                names_by_node_id[node.id] = name
                entities.append(
                    ExtractedEntity(
                        entity_type=node.label,
                        name=name,
                        chunk_index=chunk.index,
                    )
                )
            for relationship in chunk_graph.relationships:
                source_name = names_by_node_id.get(relationship.start_node_id)
                target_name = names_by_node_id.get(relationship.end_node_id)
                if source_name is None or target_name is None:
                    # The LLM referenced a node id it never declared — there
                    # is no entity to anchor the edge to, so it cannot be
                    # stored with valid provenance. Dropped, not guessed.
                    continue
                relationships.append(
                    ExtractedRelationship(
                        relationship_type=relationship.type,
                        source_name=source_name,
                        target_name=target_name,
                        chunk_index=chunk.index,
                    )
                )
        return entities, relationships


def build_graphrag_extractor() -> GraphRAGExtractor:
    from neo4j_graphrag.llm import OpenAILLM

    llm = OpenAILLM(
        model_name=settings.GRAPH_EXTRACTION_MODEL,
        model_params={"temperature": 0.0, "response_format": {"type": "json_object"}},
        api_key=settings.OPENROUTER_API_KEY,
        base_url=settings.OPENROUTER_BASE_URL,
    )
    return GraphRAGExtractor(llm=llm)
