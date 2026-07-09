"""Neo4j constraints and indexes, applied by the `graph_setup` command.

Statements use IF NOT EXISTS so re-running is always safe.
"""

import re

from django.conf import settings

# A Document node's identity IS its provenance: it is keyed by the Postgres
# SourceDocument pk, named identically to the provenance field carried by
# every derived node so the two can never drift apart. Extracted entities
# share the single :Entity label (type lives in the entity_type property —
# see graph/writer.py for why ontology types aren't used as labels), so one
# constraint covers all of them.
CONSTRAINTS = [
    "CREATE CONSTRAINT document_source_id_unique IF NOT EXISTS "
    "FOR (d:Document) REQUIRE d.source_document_id IS UNIQUE",
    "CREATE CONSTRAINT chunk_id_unique IF NOT EXISTS FOR (c:Chunk) REQUIRE c.chunk_id IS UNIQUE",
    "CREATE CONSTRAINT entity_id_unique IF NOT EXISTS FOR (e:Entity) REQUIRE e.entity_id IS UNIQUE",
]

_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def chunk_vector_index_statement(
    *, index_name: str, dimensions: int, similarity_function: str
) -> str:
    if not _IDENTIFIER_RE.fullmatch(index_name):
        raise ValueError(f"Unsafe Neo4j index name: {index_name!r}.")
    if dimensions < 1:
        raise ValueError("Vector index dimensions must be positive.")
    if similarity_function not in {"cosine", "euclidean"}:
        raise ValueError(f"Unsupported vector similarity function: {similarity_function!r}.")

    return (
        f"CREATE VECTOR INDEX {index_name} IF NOT EXISTS "
        "FOR (c:Chunk) ON (c.embedding) "
        "OPTIONS {indexConfig: {"
        f"`vector.dimensions`: {dimensions}, "
        f"`vector.similarity_function`: '{similarity_function}'"
        "}}"
    )


def graph_setup_statements() -> list[str]:
    return [
        *CONSTRAINTS,
        chunk_vector_index_statement(
            index_name=settings.GRAPH_CHUNK_VECTOR_INDEX_NAME,
            dimensions=settings.GRAPH_CHUNK_EMBEDDING_DIMENSIONS,
            similarity_function=settings.GRAPH_CHUNK_VECTOR_SIMILARITY,
        ),
    ]
