"""Embedding adapter boundary for graph vector support.

Phase 3 owns the Neo4j vector shape and writer contract, but the concrete
embedding provider is intentionally left behind an adapter so local testing and
future OpenRouter production settings do not leak into graph storage code.
"""

from dataclasses import dataclass
from typing import Protocol

from graph.extraction import ExtractedChunk


@dataclass(frozen=True)
class ChunkEmbedding:
    chunk_index: int
    vector: tuple[float, ...]


class ChunkEmbeddingValidationError(ValueError):
    """Raised instead of writing malformed or misaligned embeddings."""


class EmbeddingAdapter(Protocol):
    def embed_chunks(self, chunks: tuple[ExtractedChunk, ...]) -> tuple[ChunkEmbedding, ...]: ...


class NoOpEmbeddingAdapter:
    """Default adapter until a real embedding provider is configured."""

    def embed_chunks(self, chunks: tuple[ExtractedChunk, ...]) -> tuple[ChunkEmbedding, ...]:
        return ()


def validate_chunk_embeddings(
    chunks: tuple[ExtractedChunk, ...],
    embeddings: tuple[ChunkEmbedding, ...],
    *,
    dimensions: int | None,
) -> dict[int, list[float]]:
    if not embeddings:
        return {}
    if len(embeddings) != len(chunks):
        raise ChunkEmbeddingValidationError(
            f"Expected {len(chunks)} chunk embeddings, got {len(embeddings)}."
        )

    vectors_by_chunk_index: dict[int, list[float]] = {}
    for chunk, embedding in zip(chunks, embeddings, strict=True):
        if embedding.chunk_index != chunk.index:
            raise ChunkEmbeddingValidationError(
                f"Embedding for chunk {embedding.chunk_index} does not match chunk {chunk.index}."
            )
        if dimensions is not None and len(embedding.vector) != dimensions:
            raise ChunkEmbeddingValidationError(
                f"Embedding for chunk {chunk.index} has {len(embedding.vector)} dimensions; "
                f"expected {dimensions}."
            )
        vectors_by_chunk_index[chunk.index] = [float(value) for value in embedding.vector]

    return vectors_by_chunk_index
