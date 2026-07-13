"""Bounded serialization of already permission-filtered retrieval evidence."""

import json
from dataclasses import dataclass, field

from retrieval.types import RetrievalEvidence, RetrievedChunk, RetrievedFact

DEFAULT_CONTEXT_ITEM_MAX_CHARS = 2_000


@dataclass(frozen=True)
class AssembledContext:
    text: str = ""
    chunks: tuple[RetrievedChunk, ...] = field(default_factory=tuple)
    facts: tuple[RetrievedFact, ...] = field(default_factory=tuple)


def _compact(text: str) -> str:
    return " ".join(text.split())


def _json_line(*, source: str, kind: str, chunk_id: str, content: str) -> str:
    return json.dumps(
        {
            "source": source,
            "kind": kind,
            "chunk_id": chunk_id,
            "content": content,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def assemble_context(
    evidence: RetrievalEvidence,
    *,
    max_chars: int,
    item_max_chars: int = DEFAULT_CONTEXT_ITEM_MAX_CHARS,
) -> AssembledContext:
    """Serialize a bounded JSONL context and retain exactly its source items."""
    if max_chars < 1 or item_max_chars < 1:
        raise ValueError("Context limits must be positive.")

    lines: list[str] = []
    included_chunks: list[RetrievedChunk] = []
    included_facts: list[RetrievedFact] = []
    used_chars = 0
    source_number = 0

    items = [("chunk", chunk.chunk_id, chunk.text, chunk) for chunk in evidence.chunks] + [
        (
            "graph_fact",
            fact.chunk_id,
            (
                f"{fact.source_name} {fact.relationship_type.replace('_', ' ')} "
                f"{fact.target_name}. Evidence: {fact.text}"
            ),
            fact,
        )
        for fact in evidence.facts
    ]

    for kind, chunk_id, raw_content, item in items:
        content = _compact(raw_content)
        if not content:
            continue
        if len(content) > item_max_chars:
            content = f"{content[: item_max_chars - 1]}…"
        next_source = f"S{source_number + 1}"
        line = _json_line(
            source=next_source,
            kind=kind,
            chunk_id=chunk_id,
            content=content,
        )
        separator_size = 1 if lines else 0
        remaining = max_chars - used_chars - separator_size
        if remaining < 1:
            break
        if len(line) > remaining:
            # Shrink only the untrusted content value, then re-serialize so the
            # JSONL envelope always remains syntactically complete.
            low = min(16, len(content))
            high = len(content)
            fitted = ""
            while low <= high:
                midpoint = (low + high) // 2
                candidate_content = content[:midpoint]
                if midpoint < len(content) and midpoint > 0:
                    candidate_content = f"{candidate_content[:-1]}…"
                candidate = _json_line(
                    source=next_source,
                    kind=kind,
                    chunk_id=chunk_id,
                    content=candidate_content,
                )
                if len(candidate) <= remaining:
                    fitted = candidate
                    low = midpoint + 1
                else:
                    high = midpoint - 1
            if not fitted:
                break
            line = fitted

        lines.append(line)
        used_chars += separator_size + len(line)
        source_number += 1
        if isinstance(item, RetrievedChunk):
            included_chunks.append(item)
        else:
            included_facts.append(item)

    return AssembledContext(
        text="\n".join(lines),
        chunks=tuple(included_chunks),
        facts=tuple(included_facts),
    )
