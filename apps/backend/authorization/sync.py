from collections import defaultdict
from dataclasses import dataclass
from uuid import uuid4

from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.utils import timezone

from authorization.client import AuthzedSpiceDB, PermissionTuple, SpiceDB
from authorization.identifiers import (
    connection_prefix,
    document_object_id,
    folder_object_id,
    group_object_id,
    user_object_id,
)
from integrations.drive.client import DrivePermissionResource
from integrations.drive.groups import GoogleGroupResolver, GroupResolutionError
from integrations.drive.permissions import (
    has_domain_visibility,
    has_public_link,
    source_permissions_version,
)
from integrations.models import (
    DriveFolder,
    DriveFolderPermissionSnapshot,
    DrivePermissionSnapshot,
    PermissionSyncRun,
    SourceDocument,
)

SUPPORTED_ROLES = {
    "reader",
    "commenter",
    "writer",
    "fileOrganizer",
    "organizer",
    "owner",
}
ROLE_RELATIONS = {"fileOrganizer": "file_organizer"}


class PermissionSyncError(RuntimeError):
    """Controlled failure boundary for a permission run."""


@dataclass(frozen=True)
class SyncResult:
    desired: frozenset[PermissionTuple]
    eligible_document_versions: dict[int, str]
    excluded_document_reasons: dict[int, str]
    groups_resolved: int


def synchronize_permissions(
    run: PermissionSyncRun,
    *,
    drive_client,
    group_resolver=None,
    spicedb: SpiceDB | None = None,
) -> PermissionSyncRun:
    """Scan, reconcile, verify, then CAS-enable documents.

    A failed run keeps the previous verified state rather than blanking the
    connection: eligibility flips only in _commit_verified_documents (CAS on
    the ACL version) plus the marker sweep, and the live fully-consistent
    SpiceDB lookup remains the query-time gate either way.

    External payloads never escape this service or enter task results/logs.
    """
    connection = run.connection
    spicedb = spicedb or AuthzedSpiceDB()
    group_resolver = group_resolver or GoogleGroupResolver()
    try:
        resources = drive_client.list_permission_resources(connection)
        _persist_complete_scan(connection, resources)
        result = _desired_state(connection, group_resolver)
        current = spicedb.read_managed_tuples(connection_prefix(connection.pk))
        touches = result.desired - current
        deletes = current - result.desired
        # Even an unchanged non-empty set gets a write token for the evidence row.
        if not touches and result.desired:
            touches = {min(result.desired)}
        revision = spicedb.write_updates(touches=touches, deletes=deletes)
        verified = spicedb.read_managed_tuples(connection_prefix(connection.pk), revision=revision)
        if verified != set(result.desired):
            raise PermissionSyncError("relationship_verification_mismatch")
        verified_count = _commit_verified_documents(connection, result, revision)
        run.documents_seen = sum(resource.resource_type == "document" for resource in resources)
        run.folders_seen = sum(resource.resource_type == "folder" for resource in resources)
        run.groups_resolved = result.groups_resolved
        run.relationships_touched = len(touches)
        run.relationships_deleted = len(deletes)
        run.documents_verified = verified_count
        run.documents_excluded = len(result.excluded_document_reasons)
        run.status = (
            PermissionSyncRun.Status.PARTIAL
            if result.excluded_document_reasons
            else PermissionSyncRun.Status.SUCCEEDED
        )
        run.error_code = ""
    except Exception as exc:
        run.status = PermissionSyncRun.Status.FAILED
        run.error_code = _error_code(exc)
        raise
    finally:
        run.finished_at = timezone.now()
        run.save(
            update_fields=[
                "status",
                "documents_seen",
                "folders_seen",
                "groups_resolved",
                "relationships_touched",
                "relationships_deleted",
                "documents_verified",
                "documents_excluded",
                "error_code",
                "finished_at",
            ]
        )
    return run


def _persist_complete_scan(connection, resources: list[DrivePermissionResource]) -> None:
    marker = str(uuid4())
    now = timezone.now()
    with transaction.atomic():
        for resource in resources:
            version = source_permissions_version(resource.permissions)
            if resource.resource_type == "folder":
                folder, _ = DriveFolder.objects.update_or_create(
                    connection=connection,
                    drive_folder_id=resource.drive_id,
                    defaults={
                        "parent_folder_ids": resource.parent_folder_ids,
                        "source_permissions_version": version,
                        "active_in_scope": True,
                        "last_seen_sync_marker": marker,
                    },
                )
                DriveFolderPermissionSnapshot.objects.update_or_create(
                    drive_folder=folder,
                    defaults={
                        "raw_permissions": resource.permissions,
                        "permissions_complete": not resource.permissions_fetch_failed,
                        "captured_at": now,
                    },
                )
                continue
            SourceDocument.objects.filter(
                connection=connection, drive_file_id=resource.drive_id
            ).update(
                parent_folder_ids=resource.parent_folder_ids,
                source_permissions_version=version,
                last_permission_sync_time=now,
                active_in_scope=True,
                last_seen_sync_marker=marker,
                updated_at=now,
            )
            document = SourceDocument.objects.filter(
                connection=connection, drive_file_id=resource.drive_id
            ).first()
            if document:
                DrivePermissionSnapshot.objects.update_or_create(
                    source_document=document,
                    defaults={
                        "raw_permissions": resource.permissions,
                        "permissions_complete": not resource.permissions_fetch_failed,
                        "has_public_link": has_public_link(resource.permissions),
                        "has_domain_visibility": has_domain_visibility(resource.permissions),
                        "captured_at": now,
                    },
                )
                if resource.permissions_fetch_failed:
                    SourceDocument.objects.filter(pk=document.pk).update(
                        exclusion_reason=(
                            SourceDocument.ExclusionReason.PERMISSION_METADATA_INCOMPLETE
                        )
                    )

        DriveFolder.objects.filter(connection=connection, active_in_scope=True).exclude(
            last_seen_sync_marker=marker
        ).update(active_in_scope=False, updated_at=now)
        SourceDocument.objects.filter(connection=connection, active_in_scope=True).exclude(
            last_seen_sync_marker=marker
        ).update(
            active_in_scope=False,
            retrieval_eligible=False,
            exclusion_reason=SourceDocument.ExclusionReason.INACTIVE_IN_SCOPE,
            spicedb_permissions_version="",
            spicedb_revision="",
            spicedb_verified_at=None,
            updated_at=now,
        )


def _desired_state(connection, group_resolver) -> SyncResult:
    folders = list(
        DriveFolder.objects.filter(connection=connection, active_in_scope=True).select_related(
            "permission_snapshot"
        )
    )
    documents = list(
        SourceDocument.objects.filter(connection=connection, active_in_scope=True).select_related(
            "permission_snapshot"
        )
    )
    folder_by_drive_id = {folder.drive_folder_id: folder for folder in folders}
    _validate_acyclic(folders, folder_by_drive_id)
    group_emails = _referenced_groups(folders, documents)
    try:
        memberships = group_resolver.resolve(connection, group_emails) if group_emails else {}
        unresolved_groups: set[str] = set()
    except GroupResolutionError:
        memberships = {}
        unresolved_groups = group_emails

    desired: set[PermissionTuple] = set()
    for group_email, membership in memberships.items():
        resource_id = group_object_id(connection.pk, group_email)
        for email in membership.users:
            desired.add(
                PermissionTuple(
                    "kgm/group",
                    resource_id,
                    "member",
                    "kgm/user",
                    user_object_id(connection.pk, email),
                )
            )
        for child in membership.child_groups:
            desired.add(
                PermissionTuple(
                    "kgm/group",
                    resource_id,
                    "member",
                    "kgm/group",
                    group_object_id(connection.pk, child),
                    "member",
                )
            )

    invalid_folders: set[str] = set()
    for folder in folders:
        resource_id = folder_object_id(connection.pk, folder.drive_folder_id)
        snapshot = _snapshot(folder)
        reason = _resource_acl_reason(snapshot, unresolved_groups)
        if reason:
            invalid_folders.add(folder.drive_folder_id)
        else:
            for parent_id in folder.parent_folder_ids:
                if parent_id in folder_by_drive_id:
                    desired.add(
                        PermissionTuple(
                            "kgm/folder",
                            resource_id,
                            "parent",
                            "kgm/folder",
                            folder_object_id(connection.pk, parent_id),
                        )
                    )
            desired.update(
                _acl_tuples(
                    connection.pk,
                    "kgm/folder",
                    resource_id,
                    snapshot.raw_permissions,
                    folder.parent_folder_ids,
                    folder_by_drive_id,
                )
            )

    invalid_folders = _include_descendants(invalid_folders, folders)
    eligible: dict[int, str] = {}
    excluded: dict[int, str] = {}
    for document in documents:
        resource_id = document_object_id(connection.pk, document.pk)
        snapshot = _snapshot(document)
        reason = _document_reason(document, snapshot, unresolved_groups, invalid_folders)
        if reason:
            excluded[document.pk] = reason
            continue
        document_tuples: set[PermissionTuple] = set()
        for parent_id in document.parent_folder_ids:
            if parent_id in folder_by_drive_id:
                document_tuples.add(
                    PermissionTuple(
                        "kgm/document",
                        resource_id,
                        "parent",
                        "kgm/folder",
                        folder_object_id(connection.pk, parent_id),
                    )
                )
        document_tuples.update(
            _acl_tuples(
                connection.pk,
                "kgm/document",
                resource_id,
                snapshot.raw_permissions,
                document.parent_folder_ids,
                folder_by_drive_id,
            )
        )
        # retrieval_eligible must imply at least one verified SpiceDB grant
        # path; an empty tuple set would otherwise leave the flag trusted
        # with nothing backing it.
        if not document_tuples:
            excluded[document.pk] = SourceDocument.ExclusionReason.NO_EFFECTIVE_GRANTS
            continue
        desired.update(document_tuples)
        eligible[document.pk] = document.source_permissions_version
    return SyncResult(frozenset(desired), eligible, excluded, len(memberships))


def _snapshot(resource):
    try:
        return resource.permission_snapshot
    except ObjectDoesNotExist:
        return None


def _principal_email(permission) -> str:
    email = permission.get("emailAddress")
    return email.strip().lower() if isinstance(email, str) else ""


def _referenced_groups(folders, documents) -> set[str]:
    result: set[str] = set()
    for resource in [*folders, *documents]:
        snapshot = _snapshot(resource)
        if not snapshot:
            continue
        for permission in snapshot.raw_permissions:
            # Deleted principals are skipped everywhere ACLs are read; a
            # deleted group would 404 in the Directory API and mark every
            # group on the connection unresolved.
            if permission.get("deleted"):
                continue
            if permission.get("type") == "group" and _principal_email(permission):
                result.add(_principal_email(permission))
    return result


def _resource_acl_reason(snapshot, unresolved_groups: set[str]) -> str:
    if snapshot is None or getattr(snapshot, "permissions_complete", True) is False:
        return SourceDocument.ExclusionReason.PERMISSION_METADATA_INCOMPLETE
    permissions = snapshot.raw_permissions
    if has_public_link(permissions):
        return SourceDocument.ExclusionReason.PUBLIC_LINK_NOT_SUPPORTED
    if has_domain_visibility(permissions):
        return SourceDocument.ExclusionReason.DOMAIN_WIDE_VISIBILITY_NOT_SUPPORTED
    for permission in permissions:
        if permission.get("deleted"):
            continue
        role = permission.get("role")
        principal_type = permission.get("type")
        email = _principal_email(permission)
        if role not in SUPPORTED_ROLES or principal_type not in {"user", "group"} or not email:
            return SourceDocument.ExclusionReason.UNSUPPORTED_PERMISSION
        if permission.get("pendingOwner"):
            return SourceDocument.ExclusionReason.PERMISSION_METADATA_INCOMPLETE
        if principal_type == "group" and email in unresolved_groups:
            return SourceDocument.ExclusionReason.GROUP_MEMBERSHIP_UNRESOLVED
    return ""


def _document_reason(document, snapshot, unresolved_groups, invalid_folders) -> str:
    if document.exclusion_reason in {
        SourceDocument.ExclusionReason.UNSUPPORTED_MIME_TYPE,
        SourceDocument.ExclusionReason.INACTIVE_IN_SCOPE,
    }:
        return document.exclusion_reason
    if any(parent in invalid_folders for parent in document.parent_folder_ids):
        return SourceDocument.ExclusionReason.PERMISSION_METADATA_INCOMPLETE
    return _resource_acl_reason(snapshot, unresolved_groups)


def _acl_tuples(
    connection_id,
    resource_type,
    resource_id,
    permissions,
    parent_ids,
    folder_by_drive_id,
):
    tuples: set[PermissionTuple] = set()
    for permission in permissions:
        if permission.get("deleted") or _safely_inherited(
            permission, parent_ids, folder_by_drive_id
        ):
            continue
        email = _principal_email(permission)
        principal_type = permission["type"]
        subject_type = "kgm/user" if principal_type == "user" else "kgm/group"
        subject_id = (
            user_object_id(connection_id, email)
            if principal_type == "user"
            else group_object_id(connection_id, email)
        )
        tuples.add(
            PermissionTuple(
                resource_type,
                resource_id,
                ROLE_RELATIONS.get(permission["role"], permission["role"]),
                subject_type,
                subject_id,
                "" if principal_type == "user" else "member",
            )
        )
    return tuples


def _safely_inherited(permission, parent_ids, folder_by_drive_id) -> bool:
    details = permission.get("permissionDetails")
    if not isinstance(details, list) or not details:
        return False
    inherited = [detail for detail in details if detail.get("inherited")]
    if len(inherited) != len(details):
        return False
    sources = {detail.get("inheritedFrom") for detail in inherited}
    return bool(sources) and all(
        source in parent_ids and source in folder_by_drive_id for source in sources
    )


def _validate_acyclic(folders, folder_by_drive_id) -> None:
    edges = {
        folder.drive_folder_id: [
            parent for parent in folder.parent_folder_ids if parent in folder_by_drive_id
        ]
        for folder in folders
    }
    visiting: set[str] = set()
    visited: set[str] = set()

    # Explicit stack instead of recursion: hierarchy depth is client data,
    # and a deep-enough tree must not crash with RecursionError.
    for start in edges:
        if start in visited:
            continue
        visiting.add(start)
        stack = [(start, iter(edges[start]))]
        while stack:
            node, parents = stack[-1]
            for parent in parents:
                if parent in visiting:
                    raise PermissionSyncError("folder_hierarchy_cycle")
                if parent not in visited:
                    visiting.add(parent)
                    stack.append((parent, iter(edges[parent])))
                    break
            else:
                visiting.remove(node)
                visited.add(node)
                stack.pop()


def _include_descendants(invalid: set[str], folders) -> set[str]:
    children = defaultdict(set)
    for folder in folders:
        for parent in folder.parent_folder_ids:
            children[parent].add(folder.drive_folder_id)
    pending = list(invalid)
    while pending:
        for child in children[pending.pop()]:
            if child not in invalid:
                invalid.add(child)
                pending.append(child)
    return invalid


def _commit_verified_documents(connection, result: SyncResult, revision: str) -> int:
    now = timezone.now()
    verified = 0
    with transaction.atomic():
        for document_id, version in result.eligible_document_versions.items():
            verified += SourceDocument.objects.filter(
                pk=document_id,
                connection=connection,
                active_in_scope=True,
                source_permissions_version=version,
            ).update(
                retrieval_eligible=True,
                exclusion_reason="",
                spicedb_permissions_version=version,
                # Empty means "no explicit revision"; an eligible document
                # always has grant tuples, so a real token exists here.
                spicedb_revision=revision,
                spicedb_verified_at=now,
                updated_at=now,
            )
        for document_id, reason in result.excluded_document_reasons.items():
            SourceDocument.objects.filter(pk=document_id, connection=connection).update(
                retrieval_eligible=False,
                exclusion_reason=reason,
                spicedb_permissions_version="",
                spicedb_revision="",
                spicedb_verified_at=None,
                updated_at=now,
            )
    return verified


def _error_code(exc: Exception) -> str:
    if isinstance(exc, PermissionSyncError) and exc.args:
        return str(exc.args[0])[:64]
    return type(exc).__name__[:64]
