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
    # Endpoint entity types, when the engine knows them (neo4j-graphrag node
    # labels). They disambiguate same-named entities of different types; blank
    # means the writer may only resolve that endpoint by name.
    source_type: str = ""
    target_type: str = ""


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
    """Deterministic baseline adapter with bounded, overlap-preserving chunks.

    Exists so the document→chunk→Neo4j pipeline is provably wired end-to-end
    before an extraction engine is chosen; it never invents entities or
    relationships.
    """

    def __init__(self, *, max_chars: int = 12_000, overlap_chars: int = 1_000):
        if max_chars < 1:
            raise ValueError("max_chars must be positive")
        if not 0 <= overlap_chars < max_chars:
            raise ValueError("overlap_chars must be non-negative and smaller than max_chars")
        self._max_chars = max_chars
        self._overlap_chars = overlap_chars

    def extract(self, text: str) -> ExtractionResult:
        paragraphs = [block.strip() for block in text.split("\n\n") if block.strip()]
        chunk_texts = [
            chunk
            for paragraph in paragraphs
            for chunk in self._split_oversized_paragraph(paragraph)
        ]
        chunks = tuple(
            ExtractedChunk(index=index, text=chunk) for index, chunk in enumerate(chunk_texts)
        )
        return ExtractionResult(chunks=chunks)

    def _split_oversized_paragraph(self, paragraph: str) -> tuple[str, ...]:
        if len(paragraph) <= self._max_chars:
            return (paragraph,)

        chunks: list[str] = []
        start = 0
        while start < len(paragraph):
            max_end = min(start + self._max_chars, len(paragraph))
            end = max_end
            if max_end < len(paragraph):
                # Prefer a row or word boundary near the end of the budget. CSV
                # exports usually have newlines; a long single row still falls
                # back to a bounded character split.
                preferred_start = start + (self._max_chars // 2)
                boundary = max(
                    paragraph.rfind("\n", preferred_start, max_end + 1),
                    paragraph.rfind(" ", preferred_start, max_end + 1),
                )
                if boundary > start:
                    end = boundary

            chunk = paragraph[start:end].strip()
            if not chunk:
                end = max_end
                chunk = paragraph[start:end].strip()
            chunks.append(chunk)
            if end >= len(paragraph):
                break
            start = max(end - self._overlap_chars, start + 1)

        return tuple(chunks)
