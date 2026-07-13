"""Embedding adapters shared by ingestion and permission-safe retrieval."""

import math
from dataclasses import dataclass
from typing import Protocol

from django.conf import settings
from openai import APIConnectionError, APITimeoutError, InternalServerError, OpenAI, RateLimitError

from graph.extraction import ExtractedChunk


@dataclass(frozen=True)
class ChunkEmbedding:
    chunk_index: int
    vector: tuple[float, ...]


class ChunkEmbeddingValidationError(ValueError):
    """Raised instead of writing malformed or misaligned embeddings."""


class EmbeddingResponseError(RuntimeError):
    """Raised when an embedding provider returns an unusable response."""


RETRYABLE_EMBEDDING_EXCEPTIONS = (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
)


class EmbeddingAdapter(Protocol):
    def embed_chunks(self, chunks: tuple[ExtractedChunk, ...]) -> tuple[ChunkEmbedding, ...]: ...

    def embed_query(self, question: str) -> tuple[float, ...]: ...


class NoOpEmbeddingAdapter:
    """Explicitly disabled adapter for deterministic local/test operation."""

    def embed_chunks(self, chunks: tuple[ExtractedChunk, ...]) -> tuple[ChunkEmbedding, ...]:
        return ()

    def embed_query(self, question: str) -> tuple[float, ...]:
        return ()


class OpenRouterEmbeddingAdapter:
    """Generate ordered chunk and query vectors through OpenRouter."""

    def __init__(self, *, client, model: str, dimensions: int, batch_size: int):
        if not model:
            raise ValueError("Embedding model is required.")
        if dimensions < 1:
            raise ValueError("Embedding dimensions must be positive.")
        if not 1 <= batch_size <= 256:
            raise ValueError("Embedding batch size must be between 1 and 256.")
        self._client = client
        self._model = model
        self._dimensions = dimensions
        self._batch_size = batch_size

    def _embed_texts(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        if not texts:
            return ()
        response = self._client.embeddings.create(
            model=self._model,
            input=list(texts),
            dimensions=self._dimensions,
            encoding_format="float",
        )
        data = getattr(response, "data", None)
        if not isinstance(data, list) or len(data) != len(texts):
            raise EmbeddingResponseError("Embedding response count did not match the request.")

        vectors_by_index: dict[int, tuple[float, ...]] = {}
        for item in data:
            index = getattr(item, "index", None)
            raw_vector = getattr(item, "embedding", None)
            if type(index) is not int or index in vectors_by_index:
                raise EmbeddingResponseError("Embedding response contained invalid indices.")
            if not isinstance(raw_vector, list) or len(raw_vector) != self._dimensions:
                raise EmbeddingResponseError("Embedding response contained invalid dimensions.")
            vector: list[float] = []
            for value in raw_vector:
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise EmbeddingResponseError(
                        "Embedding response contained a non-numeric value."
                    )
                numeric = float(value)
                if not math.isfinite(numeric):
                    raise EmbeddingResponseError("Embedding response contained a non-finite value.")
                vector.append(numeric)
            vectors_by_index[index] = tuple(vector)

        expected_indices = set(range(len(texts)))
        if set(vectors_by_index) != expected_indices:
            raise EmbeddingResponseError("Embedding response indices did not match the request.")
        return tuple(vectors_by_index[index] for index in range(len(texts)))

    def embed_chunks(self, chunks: tuple[ExtractedChunk, ...]) -> tuple[ChunkEmbedding, ...]:
        embeddings: list[ChunkEmbedding] = []
        for start in range(0, len(chunks), self._batch_size):
            batch = chunks[start : start + self._batch_size]
            vectors = self._embed_texts(tuple(chunk.text for chunk in batch))
            embeddings.extend(
                ChunkEmbedding(chunk_index=chunk.index, vector=vector)
                for chunk, vector in zip(batch, vectors, strict=True)
            )
        return tuple(embeddings)

    def embed_query(self, question: str) -> tuple[float, ...]:
        if not question.strip():
            return ()
        return self._embed_texts((question,))[0]


def build_embedding_adapter() -> EmbeddingAdapter:
    """Build the configured adapter without making a provider request."""
    if settings.GRAPH_EMBEDDING_PROVIDER == "none":
        return NoOpEmbeddingAdapter()

    default_headers = {"X-OpenRouter-Title": settings.OPENROUTER_APP_NAME}
    if settings.OPENROUTER_SITE_URL:
        default_headers["HTTP-Referer"] = settings.OPENROUTER_SITE_URL
    client = OpenAI(
        api_key=settings.OPENROUTER_API_KEY,
        base_url=settings.OPENROUTER_BASE_URL,
        timeout=settings.OPENROUTER_REQUEST_TIMEOUT_SECONDS,
        max_retries=0,
        default_headers=default_headers,
    )
    return OpenRouterEmbeddingAdapter(
        client=client,
        model=settings.OPENROUTER_EMBEDDING_MODEL,
        dimensions=settings.GRAPH_CHUNK_EMBEDDING_DIMENSIONS,
        batch_size=settings.GRAPH_EMBEDDING_BATCH_SIZE,
    )


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
