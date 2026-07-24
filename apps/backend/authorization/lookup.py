import datetime
import logging

from django.conf import settings
from django.utils import timezone

from authorization.client import AuthzedSpiceDB, PermissionTuple, SpiceDB
from authorization.identifiers import connection_prefix, document_object_id, user_object_id
from integrations.drive.user_oauth import GOOGLE_ISSUERS, REQUIRED_SCOPES
from integrations.models import (
    DriveConnection,
    GoogleDriveAuthorization,
    SourceDocument,
    UserDocumentVisibility,
)
from retrieval.identity import TrustedIdentityUnavailable, normalize_trusted_email

logger = logging.getLogger(__name__)


def _visibility_cutoff():
    return timezone.now() - datetime.timedelta(
        seconds=settings.GOOGLE_USER_VISIBILITY_MAX_AGE_SECONDS
    )


def _active_mode_connections() -> tuple[DriveConnection, ...]:
    authority = settings.GOOGLE_PERMISSION_AUTHORITY
    if authority not in DriveConnection.PermissionAuthority.values:
        return ()
    connections = tuple(
        DriveConnection.objects.filter(
            enabled=True,
            permission_authority=authority,
        ).order_by("pk")
    )
    # The OAuth onboarding boundary intentionally supports one selected root.
    # Treat an ambiguous per-user configuration as unavailable, never as a
    # reason to combine authorizations across roots.
    if authority == DriveConnection.PermissionAuthority.PER_USER_OAUTH and len(connections) != 1:
        return ()
    return connections


def _current_authorization(
    connection: DriveConnection,
    normalized_email: str,
) -> GoogleDriveAuthorization | None:
    allowed_domain = settings.GOOGLE_USER_OAUTH_ALLOWED_DOMAIN.strip().lower()
    if (
        not allowed_domain
        or normalized_email.rpartition("@")[2] != allowed_domain
        or connection.workspace_domain.strip().lower() != allowed_domain
        or not connection.effective_root_id
    ):
        return None
    authorizations = list(
        GoogleDriveAuthorization.objects.filter(
            connection=connection,
            normalized_email=normalized_email,
        ).order_by("pk")[:2]
    )
    if len(authorizations) != 1:
        return None
    authorization = authorizations[0]
    if (
        authorization.status != GoogleDriveAuthorization.Status.ACTIVE
        or authorization.google_issuer not in GOOGLE_ISSUERS
        or not authorization.google_subject
        or authorization.workspace_domain.strip().lower() != allowed_domain
        or authorization.connection_generation != connection.authorization_generation
        or not REQUIRED_SCOPES.issubset(set(authorization.granted_scopes))
        or not bytes(authorization.encrypted_refresh_credential)
        or not authorization.encryption_key_version
    ):
        return None
    connected_users = GoogleDriveAuthorization.objects.filter(
        connection=connection,
        status=GoogleDriveAuthorization.Status.ACTIVE,
        connection_generation=connection.authorization_generation,
    ).count()
    if connected_users > settings.GOOGLE_USER_VISIBILITY_MAX_USERS:
        return None
    return authorization


def _fresh_per_user_documents(
    connection: DriveConnection,
    authorization: GoogleDriveAuthorization,
    *,
    source_document_ids=None,
    pending_only: bool = False,
) -> tuple[SourceDocument, ...]:
    cutoff = _visibility_cutoff()
    filters = {
        "authorization": authorization,
        "authorization__status": GoogleDriveAuthorization.Status.ACTIVE,
        "authorization__connection_generation": connection.authorization_generation,
        "authorization__authorization_generation": authorization.authorization_generation,
        "source_document__connection": connection,
        "source_document__active_in_scope": True,
        "connection_generation": connection.authorization_generation,
        "authorization_generation": authorization.authorization_generation,
        "state": UserDocumentVisibility.State.VERIFIED_VISIBLE,
        "checked_at__gte": cutoff,
        "spicedb_verified_at__gte": cutoff,
    }
    if pending_only:
        filters.update(
            {
                "source_document__retrieval_eligible": False,
                "source_document__graph_extraction_status__in": [
                    SourceDocument.GraphExtractionStatus.PENDING,
                    SourceDocument.GraphExtractionStatus.RUNNING,
                ],
            }
        )
    else:
        filters["source_document__retrieval_eligible"] = True
    if source_document_ids is not None:
        filters["source_document_id__in"] = source_document_ids
    rows = (
        UserDocumentVisibility.objects.filter(**filters)
        .exclude(source_document__source_permissions_version="")
        .exclude(spicedb_revision="")
        .select_related("source_document")
        .order_by("source_document_id")
    )
    return tuple(row.source_document for row in rows)


def _valid_direct_resource_ids(
    tuples: set[PermissionTuple],
    *,
    prefix: str,
    user_id: str,
) -> set[str]:
    valid = {
        item.resource_id
        for item in tuples
        if item.resource_type == "kgm/document"
        and item.resource_id.startswith(prefix)
        and item.relation == "oauth_viewer"
        and item.subject_type == "kgm/user"
        and item.subject_id == user_id
        and not item.subject_relation
    }
    if len(valid) != len(tuples) or len(valid) > settings.GOOGLE_USER_VISIBILITY_MAX_DOCUMENTS:
        return set()
    return valid


def _delegated_allowed_ids(
    connection: DriveConnection,
    normalized_email: str,
    client: SpiceDB,
) -> tuple[int, ...]:
    resources = set(client.lookup_documents(user_object_id(connection.pk, normalized_email)))
    if not resources:
        return ()
    candidates = (
        SourceDocument.objects.filter(connection=connection)
        .permission_verified()
        .values_list("pk", flat=True)
    )
    return tuple(
        document_id
        for document_id in candidates
        if document_object_id(connection.pk, document_id) in resources
    )


def _per_user_allowed_ids(
    connection: DriveConnection,
    normalized_email: str,
    client: SpiceDB,
) -> tuple[int, ...]:
    authorization = _current_authorization(connection, normalized_email)
    if authorization is None:
        return ()
    user_id = user_object_id(connection.pk, normalized_email)
    prefix = connection_prefix(connection.pk)
    # No revision requests a fully-consistent direct-relationship read. This
    # deliberately does not evaluate the schema's combined `view` permission.
    resources = _valid_direct_resource_ids(
        client.read_oauth_viewer_tuples(prefix, user_id),
        prefix=prefix,
        user_id=user_id,
    )
    if not resources:
        return ()
    return tuple(
        document.pk
        for document in _fresh_per_user_documents(connection, authorization)
        if document_object_id(connection.pk, document.pk) in resources
    )


def allowed_source_document_ids(
    user_email: str, *, spicedb: SpiceDB | None = None
) -> tuple[int, ...]:
    """Return the mode-specific SpiceDB/evidence intersection; fail closed."""
    try:
        normalized_email = normalize_trusted_email(user_email)
    except TrustedIdentityUnavailable:
        return ()
    client = spicedb or AuthzedSpiceDB()
    allowed: list[int] = []
    try:
        authority = settings.GOOGLE_PERMISSION_AUTHORITY
        for connection in _active_mode_connections():
            if authority == DriveConnection.PermissionAuthority.PER_USER_OAUTH:
                allowed.extend(_per_user_allowed_ids(connection, normalized_email, client))
            elif authority == DriveConnection.PermissionAuthority.DELEGATED_ACL:
                allowed.extend(_delegated_allowed_ids(connection, normalized_email, client))
            else:
                return ()
    except Exception as exc:
        # Class name only: never principals, emails, tuples, or provider payloads.
        logger.warning(
            "allowed_source_document_ids failed closed: %s.%s",
            type(exc).__module__,
            type(exc).__name__,
        )
        return ()
    return tuple(sorted(set(allowed)))


def has_pending_authorized_content(user_email: str, *, spicedb: SpiceDB | None = None) -> bool:
    """Return whether a user's already-authorized content is re-indexing.

    This is a user-experience signal only. It still requires the direct
    SpiceDB relation and fresh per-user visibility evidence, and never returns
    document identity or makes a document retrievable.
    """
    try:
        normalized_email = normalize_trusted_email(user_email)
        if (
            settings.GOOGLE_PERMISSION_AUTHORITY
            != DriveConnection.PermissionAuthority.PER_USER_OAUTH
        ):
            return False
        client = spicedb or AuthzedSpiceDB()
        for connection in _active_mode_connections():
            authorization = _current_authorization(connection, normalized_email)
            if authorization is None:
                continue
            user_id = user_object_id(connection.pk, normalized_email)
            prefix = connection_prefix(connection.pk)
            resources = _valid_direct_resource_ids(
                client.read_oauth_viewer_tuples(prefix, user_id),
                prefix=prefix,
                user_id=user_id,
            )
            if not resources:
                continue
            if any(
                document_object_id(connection.pk, document.pk) in resources
                for document in _fresh_per_user_documents(
                    connection,
                    authorization,
                    pending_only=True,
                )
            ):
                return True
        return False
    except Exception as exc:
        logger.warning(
            "pending authorized content check failed closed: %s.%s",
            type(exc).__module__,
            type(exc).__name__,
        )
        return False


def fresh_authorized_documents(
    user_email: str,
    source_document_ids,
) -> dict[int, SourceDocument]:
    """Recheck mode-specific PostgreSQL deny evidence after graph retrieval.

    This helper never grants: callers must first intersect with the SpiceDB
    allowlist. It exists to close evidence-expiry and generation-change races
    between the initial authorization lookup and context assembly.
    """
    ids = {
        value
        for value in source_document_ids
        if isinstance(value, int) and not isinstance(value, bool) and value > 0
    }
    if not ids:
        return {}
    try:
        normalized_email = normalize_trusted_email(user_email)
        authority = settings.GOOGLE_PERMISSION_AUTHORITY
        documents: dict[int, SourceDocument] = {}
        for connection in _active_mode_connections():
            connection_ids = set(
                SourceDocument.objects.filter(connection=connection, pk__in=ids).values_list(
                    "pk", flat=True
                )
            )
            if not connection_ids:
                continue
            if authority == DriveConnection.PermissionAuthority.DELEGATED_ACL:
                current = SourceDocument.objects.permission_verified().filter(
                    connection=connection,
                    pk__in=connection_ids,
                )
            elif authority == DriveConnection.PermissionAuthority.PER_USER_OAUTH:
                authorization = _current_authorization(connection, normalized_email)
                if authorization is None:
                    continue
                current = _fresh_per_user_documents(
                    connection,
                    authorization,
                    source_document_ids=connection_ids,
                )
            else:
                return {}
            documents.update({document.pk: document for document in current})
        return documents
    except Exception as exc:
        logger.warning(
            "fresh_authorized_documents failed closed: %s.%s",
            type(exc).__module__,
            type(exc).__name__,
        )
        return {}
