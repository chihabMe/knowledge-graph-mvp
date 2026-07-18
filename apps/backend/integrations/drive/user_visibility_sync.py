"""Fail-closed persistence and reconciliation for per-user Drive visibility."""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from authorization.oauth_viewer import (
    OAuthViewerRelationshipError,
    reconcile_oauth_viewer_relationships,
)
from integrations.drive.user_oauth import REQUIRED_SCOPES
from integrations.drive.user_visibility_client import (
    IndexedDriveVisibilityClient,
    IndexedVisibilityBatch,
    IndexedVisibilityResult,
)
from integrations.models import (
    DriveConnection,
    GoogleDriveAuthorization,
    SourceDocument,
    UserDocumentVisibility,
    UserVisibilitySyncRun,
)
from retrieval.identity import TrustedIdentityUnavailable, normalize_trusted_email

_RESULT_REASON_CODES = frozenset(
    {
        "",
        "drive_api_error",
        "inaccessible",
        "malformed_response",
        "transient_failure",
        "trashed",
    }
)
_CONTROLLED_ERROR_CODES = frozenset(
    {
        "authorization_unavailable",
        "batch_scope_mismatch",
        "credential_refresh_failed",
        "credential_unavailable",
        "document_cap_exceeded",
        "oauth_client_unavailable",
        "permission_authority_mismatch",
        "relationship_scope_mismatch",
        "relationship_verification_mismatch",
        "spicedb_operation_failed",
        "visibility_result_invalid",
    }
)


class UserVisibilitySyncError(RuntimeError):
    """Controlled synchronization failure with no identity or Drive metadata."""


def _error_code(exc: Exception) -> str:
    code = str(exc)
    if code in _CONTROLLED_ERROR_CODES:
        return code
    if isinstance(exc, OAuthViewerRelationshipError):
        return "spicedb_operation_failed"
    return "visibility_sync_failed"


def _invalidate_authorization_evidence(
    authorization_id: int,
    *,
    reason_code: str,
) -> None:
    UserDocumentVisibility.objects.filter(authorization_id=authorization_id).update(
        state=UserDocumentVisibility.State.UNKNOWN,
        spicedb_revision="",
        spicedb_verified_at=None,
        reason_code=reason_code,
        updated_at=timezone.now(),
    )


def invalidate_authorization_evidence(
    authorization_id: int,
    *,
    reason_code: str,
) -> None:
    """Public deny-only helper for task recovery paths."""
    if reason_code not in {
        "authorization_unavailable",
        "stale_run_timeout",
        "user_cap_exceeded",
    }:
        raise ValueError("unsupported visibility invalidation reason")
    _invalidate_authorization_evidence(authorization_id, reason_code=reason_code)


def _preinvalidate(run: UserVisibilitySyncRun) -> tuple[uuid.UUID, tuple[int, ...]]:
    marker = uuid.uuid4()
    now = timezone.now()
    over_cap = False
    with transaction.atomic():
        current = (
            UserVisibilitySyncRun.objects.select_for_update()
            .select_related("authorization", "connection")
            .get(pk=run.pk)
        )
        _invalidate_authorization_evidence(
            current.authorization_id,
            reason_code="refresh_pending",
        )
        candidate_ids = tuple(
            SourceDocument.objects.filter(
                connection_id=current.connection_id,
                active_in_scope=True,
            )
            .order_by("pk")
            .values_list("pk", flat=True)
        )
        if len(candidate_ids) > settings.GOOGLE_USER_VISIBILITY_MAX_DOCUMENTS:
            over_cap = True
        else:
            for document_id in candidate_ids:
                UserDocumentVisibility.objects.update_or_create(
                    authorization_id=current.authorization_id,
                    source_document_id=document_id,
                    defaults={
                        "connection_generation": current.connection_generation,
                        "authorization_generation": current.authorization_generation,
                        "state": UserDocumentVisibility.State.UNKNOWN,
                        "checked_at": now,
                        "visibility_sync_marker": marker,
                        "spicedb_revision": "",
                        "spicedb_verified_at": None,
                        "reason_code": "refresh_pending",
                    },
                )
    if over_cap:
        raise UserVisibilitySyncError("document_cap_exceeded")
    return marker, candidate_ids


def _validate_scope(run: UserVisibilitySyncRun) -> None:
    connection = run.connection
    authorization = run.authorization
    if (
        settings.GOOGLE_PERMISSION_AUTHORITY != DriveConnection.PermissionAuthority.PER_USER_OAUTH
        or not connection.enabled
        or connection.permission_authority != DriveConnection.PermissionAuthority.PER_USER_OAUTH
        or not connection.effective_root_id
        or connection.authorization_generation != run.connection_generation
        or authorization.connection_id != connection.pk
        or authorization.connection_generation != run.connection_generation
        or authorization.authorization_generation != run.authorization_generation
        or authorization.status != GoogleDriveAuthorization.Status.ACTIVE
        or not REQUIRED_SCOPES.issubset(set(authorization.granted_scopes))
        or not bytes(authorization.encrypted_refresh_credential)
        or not authorization.encryption_key_version
    ):
        raise UserVisibilitySyncError("authorization_unavailable")


def _validated_results(
    batch: IndexedVisibilityBatch,
    *,
    run: UserVisibilitySyncRun,
    candidate_ids: tuple[int, ...],
) -> dict[int, IndexedVisibilityResult]:
    if (
        batch.authorization_id != run.authorization_id
        or batch.connection_generation != str(run.connection_generation)
        or batch.authorization_generation != str(run.authorization_generation)
    ):
        raise UserVisibilitySyncError("batch_scope_mismatch")
    results: dict[int, IndexedVisibilityResult] = {}
    valid_states = {choice for choice, _ in UserDocumentVisibility.State.choices}
    for result in batch.results:
        if (
            result.source_document_id in results
            or result.state not in valid_states
            or result.reason_code not in _RESULT_REASON_CODES
        ):
            raise UserVisibilitySyncError("visibility_result_invalid")
        results[result.source_document_id] = result
    if set(results) != set(candidate_ids):
        raise UserVisibilitySyncError("batch_scope_mismatch")
    return results


def _commit_verified_results(
    *,
    run: UserVisibilitySyncRun,
    marker: uuid.UUID,
    candidate_ids: tuple[int, ...],
    results: dict[int, IndexedVisibilityResult],
    revision: str,
    relationships_touched: int,
    relationships_deleted: int,
) -> UserVisibilitySyncRun:
    verified_at = timezone.now()
    visible_count = 0
    denied_count = 0
    unknown_count = 0
    with transaction.atomic():
        connection = DriveConnection.objects.select_for_update().get(pk=run.connection_id)
        authorization = GoogleDriveAuthorization.objects.select_for_update().get(
            pk=run.authorization_id
        )
        current_run = UserVisibilitySyncRun.objects.select_for_update().get(pk=run.pk)
        current_run.connection = connection
        current_run.authorization = authorization
        _validate_scope(current_run)
        current_ids = set(
            SourceDocument.objects.filter(
                connection=connection,
                active_in_scope=True,
                pk__in=candidate_ids,
            ).values_list("pk", flat=True)
        )
        if current_ids != set(candidate_ids):
            raise UserVisibilitySyncError("batch_scope_mismatch")
        for document_id, result in results.items():
            is_visible = result.state == UserDocumentVisibility.State.VERIFIED_VISIBLE
            updated = UserDocumentVisibility.objects.filter(
                authorization=authorization,
                source_document_id=document_id,
                visibility_sync_marker=marker,
            ).update(
                connection_generation=current_run.connection_generation,
                authorization_generation=current_run.authorization_generation,
                state=result.state,
                checked_at=verified_at,
                spicedb_revision=revision if is_visible else "",
                spicedb_verified_at=verified_at if is_visible else None,
                reason_code=result.reason_code,
                updated_at=verified_at,
            )
            if updated != 1:
                raise UserVisibilitySyncError("batch_scope_mismatch")
            visible_count += int(is_visible)
            denied_count += int(result.state == UserDocumentVisibility.State.DENIED)
            unknown_count += int(result.state == UserDocumentVisibility.State.UNKNOWN)
        current_run.documents_considered = len(candidate_ids)
        current_run.documents_verified_visible = visible_count
        current_run.documents_denied = denied_count
        current_run.documents_unknown = unknown_count
        current_run.relationships_touched = relationships_touched
        current_run.relationships_deleted = relationships_deleted
        current_run.status = (
            UserVisibilitySyncRun.Status.PARTIAL
            if unknown_count
            else UserVisibilitySyncRun.Status.SUCCEEDED
        )
        current_run.error_code = ""
        current_run.finished_at = verified_at
        current_run.save(
            update_fields=[
                "documents_considered",
                "documents_verified_visible",
                "documents_denied",
                "documents_unknown",
                "relationships_touched",
                "relationships_deleted",
                "status",
                "error_code",
                "finished_at",
            ]
        )
        if not unknown_count:
            authorization.last_successful_visibility_sync_at = verified_at
            authorization.save(update_fields=["last_successful_visibility_sync_at"])
    return current_run


def _mark_failed(run_id: int, code: str) -> None:
    UserVisibilitySyncRun.objects.filter(
        pk=run_id,
        status__in=[UserVisibilitySyncRun.Status.QUEUED, UserVisibilitySyncRun.Status.RUNNING],
    ).update(
        status=UserVisibilitySyncRun.Status.FAILED,
        error_code=code,
        finished_at=timezone.now(),
    )


def synchronize_user_visibility(
    run: UserVisibilitySyncRun,
    *,
    visibility_client: IndexedDriveVisibilityClient | None = None,
    spicedb=None,
) -> UserVisibilitySyncRun:
    """Invalidate, check, reconcile, verify, and commit one user's evidence."""
    try:
        marker, candidate_ids = _preinvalidate(run)
        run = UserVisibilitySyncRun.objects.select_related("connection", "authorization").get(
            pk=run.pk
        )
        _validate_scope(run)
        batch = (visibility_client or IndexedDriveVisibilityClient()).check_authorization(
            run.authorization_id
        )
        results = _validated_results(batch, run=run, candidate_ids=candidate_ids)
        visible_ids = tuple(
            document_id
            for document_id, result in results.items()
            if result.state == UserDocumentVisibility.State.VERIFIED_VISIBLE
        )
        reconciliation = reconcile_oauth_viewer_relationships(
            connection=run.connection,
            user_email=run.authorization.normalized_email,
            source_document_ids=visible_ids,
            spicedb=spicedb,
        )
        return _commit_verified_results(
            run=run,
            marker=marker,
            candidate_ids=candidate_ids,
            results=results,
            revision=reconciliation.revision,
            relationships_touched=reconciliation.relationships_touched,
            relationships_deleted=reconciliation.relationships_deleted,
        )
    except Exception as exc:
        code = _error_code(exc)
        _mark_failed(run.pk, code)
        raise UserVisibilitySyncError(code) from exc


def queue_user_visibility_sync(*, user_email: str, dispatch) -> UserVisibilitySyncRun:
    """Create or safely redispatch only the signed-in user's durable run."""
    try:
        normalized_email = normalize_trusted_email(user_email)
    except TrustedIdentityUnavailable as exc:
        raise UserVisibilitySyncError("authorization_unavailable") from exc
    if settings.GOOGLE_PERMISSION_AUTHORITY != DriveConnection.PermissionAuthority.PER_USER_OAUTH:
        raise UserVisibilitySyncError("permission_authority_mismatch")
    over_cap_authorization_id: int | None = None
    run: UserVisibilitySyncRun | None = None
    with transaction.atomic():
        authorizations = list(
            GoogleDriveAuthorization.objects.select_for_update()
            .select_related("connection")
            .filter(
                normalized_email=normalized_email,
                status=GoogleDriveAuthorization.Status.ACTIVE,
                connection__enabled=True,
                connection__permission_authority=(
                    DriveConnection.PermissionAuthority.PER_USER_OAUTH
                ),
            )
        )
        if len(authorizations) != 1:
            raise UserVisibilitySyncError("authorization_unavailable")
        authorization = authorizations[0]
        if (
            authorization.workspace_domain.lower()
            != settings.GOOGLE_USER_OAUTH_ALLOWED_DOMAIN.lower()
            or authorization.connection.workspace_domain.lower()
            != settings.GOOGLE_USER_OAUTH_ALLOWED_DOMAIN.lower()
            or not authorization.connection.effective_root_id
            or authorization.connection_generation
            != authorization.connection.authorization_generation
            or not REQUIRED_SCOPES.issubset(set(authorization.granted_scopes))
            or not bytes(authorization.encrypted_refresh_credential)
            or not authorization.encryption_key_version
        ):
            raise UserVisibilitySyncError("authorization_unavailable")
        connected_users = GoogleDriveAuthorization.objects.filter(
            connection=authorization.connection,
            status=GoogleDriveAuthorization.Status.ACTIVE,
            connection_generation=authorization.connection.authorization_generation,
        ).count()
        if connected_users > settings.GOOGLE_USER_VISIBILITY_MAX_USERS:
            over_cap_authorization_id = authorization.pk
        else:
            active_runs = list(
                UserVisibilitySyncRun.objects.select_for_update()
                .filter(
                    authorization=authorization,
                    status__in=[
                        UserVisibilitySyncRun.Status.QUEUED,
                        UserVisibilitySyncRun.Status.RUNNING,
                    ],
                )
                .order_by("pk")
            )
            running = next(
                (
                    item
                    for item in active_runs
                    if item.status == UserVisibilitySyncRun.Status.RUNNING
                ),
                None,
            )
            if running:
                return running
            run = (
                active_runs[0]
                if active_runs
                else UserVisibilitySyncRun.create_for_authorization(authorization)
            )
    if over_cap_authorization_id is not None:
        invalidate_authorization_evidence(
            over_cap_authorization_id,
            reason_code="user_cap_exceeded",
        )
        raise UserVisibilitySyncError("authorization_unavailable")
    if run is None:
        raise UserVisibilitySyncError("authorization_unavailable")
    dispatch(run.pk)
    return run
