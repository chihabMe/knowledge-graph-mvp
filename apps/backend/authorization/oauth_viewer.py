"""Exact, user-scoped SpiceDB reconciliation for ADR-015 direct grants."""

from collections.abc import Iterable
from dataclasses import dataclass

from django.conf import settings

from authorization.client import AuthzedSpiceDB, PermissionTuple, SpiceDB
from authorization.identifiers import connection_prefix, document_object_id, user_object_id
from integrations.models import DriveConnection, SourceDocument
from retrieval.identity import TrustedIdentityUnavailable, normalize_trusted_email


class OAuthViewerRelationshipError(RuntimeError):
    """Controlled direct-relationship failure with no principal or document data."""


@dataclass(frozen=True)
class OAuthViewerReconciliation:
    revision: str
    relationships_touched: int
    relationships_deleted: int


def _validated_connection(connection: DriveConnection) -> DriveConnection:
    if not connection.pk:
        raise OAuthViewerRelationshipError("permission_authority_mismatch")
    try:
        current = DriveConnection.objects.get(pk=connection.pk)
    except DriveConnection.DoesNotExist as exc:
        raise OAuthViewerRelationshipError("permission_authority_mismatch") from exc
    if (
        not current.enabled
        or current.permission_authority != DriveConnection.PermissionAuthority.PER_USER_OAUTH
        or settings.GOOGLE_PERMISSION_AUTHORITY
        != DriveConnection.PermissionAuthority.PER_USER_OAUTH
        or current.authorization_generation != connection.authorization_generation
        or not current.effective_root_id
    ):
        raise OAuthViewerRelationshipError("permission_authority_mismatch")
    return current


def _validated_user_id(connection: DriveConnection, user_email: str) -> str:
    try:
        normalized_email = normalize_trusted_email(user_email)
    except TrustedIdentityUnavailable as exc:
        raise OAuthViewerRelationshipError("identity_invalid") from exc
    allowed_domain = settings.GOOGLE_USER_OAUTH_ALLOWED_DOMAIN.lower()
    if (
        normalized_email.rpartition("@")[2] != allowed_domain
        or connection.workspace_domain.lower() != allowed_domain
    ):
        raise OAuthViewerRelationshipError("identity_domain_mismatch")
    return user_object_id(connection.pk, normalized_email)


def _desired_tuples(
    connection: DriveConnection,
    user_id: str,
    source_document_ids: Iterable[int],
) -> frozenset[PermissionTuple]:
    ids: set[int] = set()
    for document_id in source_document_ids:
        if isinstance(document_id, bool) or not isinstance(document_id, int) or document_id <= 0:
            raise OAuthViewerRelationshipError("document_scope_invalid")
        ids.add(document_id)
        if len(ids) > settings.GOOGLE_USER_VISIBILITY_MAX_DOCUMENTS:
            raise OAuthViewerRelationshipError("document_scope_exceeds_cap")
    indexed_ids = set(
        SourceDocument.objects.filter(
            connection=connection,
            active_in_scope=True,
            pk__in=ids,
        ).values_list("pk", flat=True)
    )
    if indexed_ids != ids:
        raise OAuthViewerRelationshipError("document_scope_invalid")
    return frozenset(
        PermissionTuple(
            resource_type="kgm/document",
            resource_id=document_object_id(connection.pk, document_id),
            relation="oauth_viewer",
            subject_type="kgm/user",
            subject_id=user_id,
        )
        for document_id in ids
    )


def _validate_current_scope(current: set[PermissionTuple], *, prefix: str, user_id: str) -> None:
    if any(
        item.resource_type != "kgm/document"
        or not item.resource_id.startswith(prefix)
        or item.relation != "oauth_viewer"
        or item.subject_type != "kgm/user"
        or item.subject_id != user_id
        or item.subject_relation
        for item in current
    ):
        raise OAuthViewerRelationshipError("relationship_scope_mismatch")


def reconcile_oauth_viewer_relationships(
    *,
    connection: DriveConnection,
    user_email: str,
    source_document_ids: Iterable[int],
    spicedb: SpiceDB | None = None,
) -> OAuthViewerReconciliation:
    """Make one user's direct tuples exactly match active indexed rows."""
    connection = _validated_connection(connection)
    user_id = _validated_user_id(connection, user_email)
    desired = _desired_tuples(connection, user_id, source_document_ids)
    prefix = connection_prefix(connection.pk)
    client = spicedb or AuthzedSpiceDB()
    try:
        current = client.read_oauth_viewer_tuples(prefix, user_id)
        _validate_current_scope(current, prefix=prefix, user_id=user_id)
        touches = set(desired - current)
        deletes = set(current - desired)
        # Obtain a causal revision for unchanged positive evidence too.
        if not touches and desired:
            touches = {min(desired)}
        revision = client.write_updates(touches=touches, deletes=deletes)
        verified = client.read_oauth_viewer_tuples(prefix, user_id, revision=revision)
        _validate_current_scope(verified, prefix=prefix, user_id=user_id)
    except OAuthViewerRelationshipError:
        raise
    except Exception as exc:
        raise OAuthViewerRelationshipError("spicedb_operation_failed") from exc
    if verified != set(desired):
        raise OAuthViewerRelationshipError("relationship_verification_mismatch")
    return OAuthViewerReconciliation(
        revision=revision,
        relationships_touched=len(touches),
        relationships_deleted=len(deletes),
    )


def delete_oauth_viewer_relationships(
    *,
    connection: DriveConnection,
    user_email: str,
    spicedb: SpiceDB | None = None,
) -> OAuthViewerReconciliation:
    """Delete and exactly verify only this user's direct relationships."""
    return reconcile_oauth_viewer_relationships(
        connection=connection,
        user_email=user_email,
        source_document_ids=(),
        spicedb=spicedb,
    )
