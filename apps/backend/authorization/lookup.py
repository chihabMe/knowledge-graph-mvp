import logging

from authorization.client import AuthzedSpiceDB, SpiceDB
from authorization.identifiers import document_object_id, user_object_id
from integrations.models import DriveConnection, SourceDocument

logger = logging.getLogger(__name__)


def allowed_source_document_ids(
    user_email: str, *, spicedb: SpiceDB | None = None
) -> tuple[int, ...]:
    """Return the Phase 5 source allowlist, failing closed as one operation."""
    normalized_email = user_email.strip().lower()
    if not normalized_email:
        return ()
    client = spicedb or AuthzedSpiceDB()
    allowed: list[int] = []
    try:
        for connection in DriveConnection.objects.filter(enabled=True).order_by("pk"):
            resources = set(
                client.lookup_documents(user_object_id(connection.pk, normalized_email))
            )
            if not resources:
                continue
            candidates = (
                SourceDocument.objects.filter(connection=connection)
                .permission_verified()
                .values_list("pk", flat=True)
            )
            allowed.extend(
                document_id
                for document_id in candidates
                if document_object_id(connection.pk, document_id) in resources
            )
    except Exception as exc:
        # Fail closed, but leave evidence: without this line a SpiceDB outage
        # is indistinguishable from "user has access to nothing". Class name
        # only — never principals, emails, or payloads.
        logger.warning(
            "allowed_source_document_ids failed closed: %s.%s",
            type(exc).__module__,
            type(exc).__name__,
        )
        return ()
    return tuple(sorted(set(allowed)))
