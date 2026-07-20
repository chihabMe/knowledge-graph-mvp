from dataclasses import dataclass, field


@dataclass(frozen=True)
class RetrievedChunk:
    source_document_id: int
    chunk_id: str
    text: str
    relevance: float = 0.0
    retrieval_modes: tuple[str, ...] = field(default_factory=tuple)
    # Content hash of the graph document version this chunk was extracted
    # from. Empty means unknown, which retrieval must treat as stale.
    content_version: str = ""


@dataclass(frozen=True)
class RetrievedFact:
    source_document_id: int
    chunk_id: str
    source_name: str
    relationship_type: str
    target_name: str
    text: str
    relevance: float = 0.0
    retrieval_modes: tuple[str, ...] = field(default_factory=tuple)
    # Content hash of the graph document version this fact was extracted
    # from. Empty means unknown, which retrieval must treat as stale.
    content_version: str = ""


@dataclass(frozen=True)
class RetrievalEvidence:
    chunks: tuple[RetrievedChunk, ...] = field(default_factory=tuple)
    facts: tuple[RetrievedFact, ...] = field(default_factory=tuple)
