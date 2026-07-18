"""Fail-closed switching between mutually exclusive Drive permission authorities."""

import uuid
from dataclasses import dataclass

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from authorization.client import AuthzedSpiceDB, SpiceDB
from authorization.identifiers import connection_prefix
from integrations.models import (
    DriveConnection,
    GoogleDriveAuthorization,
    SourceDocument,
    UserDocumentVisibility,
)


class PermissionAuthoritySwitchError(RuntimeError):
    """Controlled cutover failure without principals, file IDs, or provider payloads."""


@dataclass(frozen=True)
class PermissionAuthoritySwitchResult:
    connection_id: int
    previous_authority: str
    permission_authority: str
    documents_invalidated: int
    authorizations_invalidated: int
    relationships_deleted: int
    changed: bool


def _validate_target(target_authority: str) -> None:
    if target_authority not in DriveConnection.PermissionAuthority.values:
        raise PermissionAuthoritySwitchError("permission_authority_invalid")
    if settings.GOOGLE_PERMISSION_AUTHORITY != target_authority:
        raise PermissionAuthoritySwitchError("configured_authority_mismatch")


def _invalidate_authorizations(connection: DriveConnection, *, now) -> int:
    authorizations = list(
        GoogleDriveAuthorization.objects.select_for_update().filter(connection=connection)
    )
    UserDocumentVisibility.objects.filter(authorization__in=authorizations).delete()
    for authorization in authorizations:
        authorization.authorization_generation = uuid.uuid4()
        authorization.status = GoogleDriveAuthorization.Status.DISCONNECTED
        authorization.encrypted_refresh_credential = b""
        authorization.encryption_key_version = ""
        authorization.granted_scopes = []
        authorization.disconnected_at = now
        authorization.last_successful_visibility_sync_at = None
        authorization.save(
            update_fields=[
                "authorization_generation",
                "status",
                "encrypted_refresh_credential",
                "encryption_key_version",
                "granted_scopes",
                "disconnected_at",
                "last_successful_visibility_sync_at",
                "updated_at",
            ]
        )
    return len(authorizations)


def _deny_connection(connection_id: int) -> tuple[DriveConnection, int, int]:
    now = timezone.now()
    with transaction.atomic():
        try:
            connection = DriveConnection.objects.select_for_update().get(pk=connection_id)
        except DriveConnection.DoesNotExist as exc:
            raise PermissionAuthoritySwitchError("connection_unavailable") from exc
        if not connection.effective_root_id:
            raise PermissionAuthoritySwitchError("selected_root_missing")

        connection.enabled = False
        connection.authorization_generation = uuid.uuid4()
        connection.save(update_fields=["enabled", "authorization_generation", "updated_at"])
        documents = SourceDocument.objects.filter(connection=connection)
        documents_invalidated = documents.update(
            retrieval_eligible=False,
            source_permissions_version="",
            spicedb_permissions_version="",
            spicedb_revision="",
            spicedb_verified_at=None,
            updated_at=now,
        )
        # The authority generation is graph provenance. Re-run successful
        # extractions once after the next Drive metadata sync so Neo4j receives
        # the new non-empty generation without waiting for a content change.
        documents.filter(
            graph_extraction_status=SourceDocument.GraphExtractionStatus.SUCCEEDED,
        ).exclude(content_hash="").update(
            graph_extraction_status=SourceDocument.GraphExtractionStatus.PENDING,
            graph_extraction_queued_at=None,
            updated_at=now,
        )
        authorizations_invalidated = _invalidate_authorizations(connection, now=now)
    return connection, documents_invalidated, authorizations_invalidated


def _delete_connection_relationships(connection_id: int, spicedb: SpiceDB) -> int:
    prefix = connection_prefix(connection_id)
    try:
        current = spicedb.read_managed_tuples(prefix)
        revision = spicedb.write_updates(touches=(), deletes=current)
        verified = spicedb.read_managed_tuples(prefix, revision=revision)
    except Exception as exc:
        raise PermissionAuthoritySwitchError("spicedb_cleanup_failed") from exc
    if verified:
        raise PermissionAuthoritySwitchError("spicedb_cleanup_incomplete")
    return len(current)


def switch_permission_authority(
    *,
    connection_id: int,
    target_authority: str,
    spicedb: SpiceDB | None = None,
) -> PermissionAuthoritySwitchResult:
    """Deny first, delete old relationships, then activate one authority.

    PostgreSQL and SpiceDB cannot share a transaction. The connection is
    therefore committed disabled before SpiceDB cleanup begins. Any failure
    leaves it disabled with all local evidence invalidated.
    """
    _validate_target(target_authority)
    try:
        current = DriveConnection.objects.get(pk=connection_id)
    except DriveConnection.DoesNotExist as exc:
        raise PermissionAuthoritySwitchError("connection_unavailable") from exc
    if current.permission_authority == target_authority:
        return PermissionAuthoritySwitchResult(
            connection_id=current.pk,
            previous_authority=current.permission_authority,
            permission_authority=current.permission_authority,
            documents_invalidated=0,
            authorizations_invalidated=0,
            relationships_deleted=0,
            changed=False,
        )

    previous_authority = current.permission_authority
    denied, documents_invalidated, authorizations_invalidated = _deny_connection(connection_id)
    relationships_deleted = _delete_connection_relationships(
        connection_id,
        spicedb or AuthzedSpiceDB(),
    )

    now = timezone.now()
    with transaction.atomic():
        connection = DriveConnection.objects.select_for_update().get(pk=connection_id)
        if connection.authorization_generation != denied.authorization_generation:
            raise PermissionAuthoritySwitchError("connection_changed_during_cutover")
        connection.permission_authority = target_authority
        connection.permission_authority_changed_at = now
        connection.enabled = True
        connection.save(
            update_fields=[
                "permission_authority",
                "permission_authority_changed_at",
                "enabled",
                "updated_at",
            ]
        )

    return PermissionAuthoritySwitchResult(
        connection_id=connection.pk,
        previous_authority=previous_authority,
        permission_authority=connection.permission_authority,
        documents_invalidated=documents_invalidated,
        authorizations_invalidated=authorizations_invalidated,
        relationships_deleted=relationships_deleted,
        changed=True,
    )
