"""Query-layer retrieval guard.

Anything missing source provenance is unusable for retrieval — fail closed
(project invariant 2). Retrieval queries (Phase 5) must compose
provenance_where() into their Cypher and pass the SpiceDB-derived
$allowed_source_document_ids parameter; record_has_provenance() is the
post-query defense for anything that slips through a hand-written query.
"""

PROVENANCE_FIELDS = ("source_document_id", "connection_id", "drive_file_id")

ALLOWED_DOCUMENTS_PARAMETER = "allowed_source_document_ids"


def provenance_where(alias: str) -> str:
    """Cypher WHERE fragment: provenance complete AND document allowed."""
    field_checks = " AND ".join(f"{alias}.{field} IS NOT NULL" for field in PROVENANCE_FIELDS)
    return f"({field_checks} AND {alias}.source_document_id IN ${ALLOWED_DOCUMENTS_PARAMETER})"


def record_has_provenance(properties: dict) -> bool:
    return all(properties.get(field) is not None for field in PROVENANCE_FIELDS)
