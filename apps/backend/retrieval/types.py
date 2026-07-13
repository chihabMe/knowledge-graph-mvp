from dataclasses import dataclass, field


@dataclass(frozen=True)
class RetrievedChunk:
    source_document_id: int
    chunk_id: str
    text: str


@dataclass(frozen=True)
class RetrievedFact:
    source_document_id: int
    chunk_id: str
    source_name: str
    relationship_type: str
    target_name: str
    text: str


@dataclass(frozen=True)
class RetrievalEvidence:
    chunks: tuple[RetrievedChunk, ...] = field(default_factory=tuple)
    facts: tuple[RetrievedFact, ...] = field(default_factory=tuple)
