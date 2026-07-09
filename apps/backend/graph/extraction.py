"""Extraction adapter boundary.

Engines under evaluation (neo4j-graphrag, Graphify, Graphiti — see the Phase 3
tracker) plug in behind ExtractionAdapter; nothing outside this module may
depend on a concrete engine. Every adapter's output passes through
validate_extraction_result before it can reach a writer, so an engine cannot
introduce entity or relationship types outside the declared ontology.
"""

from dataclasses import dataclass, field
from typing import Protocol

from graph.ontology import validate_entity_type, validate_relationship_type


@dataclass(frozen=True)
class ExtractedChunk:
    index: int
    text: str


@dataclass(frozen=True)
class ExtractedEntity:
    entity_type: str
    name: str
    # Which chunk this entity was found in — fact-level provenance, required
    # for permission-safe retrieval (see the Phase 3 validation checklist).
    chunk_index: int


@dataclass(frozen=True)
class ExtractedRelationship:
    relationship_type: str
    source_name: str
    target_name: str
    chunk_index: int


@dataclass(frozen=True)
class ExtractionResult:
    chunks: tuple[ExtractedChunk, ...] = field(default_factory=tuple)
    entities: tuple[ExtractedEntity, ...] = field(default_factory=tuple)
    relationships: tuple[ExtractedRelationship, ...] = field(default_factory=tuple)


class ExtractionAdapter(Protocol):
    def extract(self, text: str) -> ExtractionResult: ...


def validate_extraction_result(result: ExtractionResult) -> ExtractionResult:
    for entity in result.entities:
        validate_entity_type(entity.entity_type)
    for relationship in result.relationships:
        validate_relationship_type(relationship.relationship_type)
    return result


class ParagraphChunkExtractor:
    """Deterministic baseline adapter: paragraphs become chunks, nothing else.

    Exists so the document→chunk→Neo4j pipeline is provably wired end-to-end
    before an extraction engine is chosen; it never invents entities or
    relationships.
    """

    def extract(self, text: str) -> ExtractionResult:
        paragraphs = [block.strip() for block in text.split("\n\n") if block.strip()]
        chunks = tuple(
            ExtractedChunk(index=index, text=paragraph)
            for index, paragraph in enumerate(paragraphs)
        )
        return ExtractionResult(chunks=chunks)
