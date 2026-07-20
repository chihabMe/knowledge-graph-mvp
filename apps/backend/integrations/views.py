from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from integrations.drive.client import DriveRootCandidate
from integrations.drive.google_client import (
    DriveCredentialUnavailableError,
    GoogleDriveApiError,
    GoogleDriveMetadataClient,
)
from integrations.models import DriveConnection, DriveSyncRun, SourceDocument
from integrations.serializers import DriveDelegatedSubjectSerializer, DriveRootSelectionSerializer
from integrations.tasks import run_drive_sync


def _active_connection() -> DriveConnection | None:
    return DriveConnection.objects.filter(enabled=True).order_by("pk").first()


def _connection_defaults_from_settings() -> dict[str, object]:
    credential_reference = {
        "application_default": "GOOGLE_APPLICATION_CREDENTIALS",
        "oauth_dev": "GOOGLE_OAUTH_TOKEN_FILE",
    }.get(settings.GOOGLE_DRIVE_AUTH_MODE, "GOOGLE_SERVICE_ACCOUNT_FILE")
    return {
        "workspace_domain": settings.GOOGLE_WORKSPACE_DOMAIN,
        "delegated_subject_email": (
            settings.GOOGLE_DRIVE_DELEGATED_SUBJECT
            if settings.GOOGLE_PERMISSION_AUTHORITY
            == DriveConnection.PermissionAuthority.DELEGATED_ACL
            else ""
        ),
        "credential_reference": credential_reference,
        "scope_type": settings.GOOGLE_DRIVE_SCOPE_TYPE,
        "root_folder_id": (
            settings.GOOGLE_DRIVE_ROOT_ID
            if settings.GOOGLE_DRIVE_SCOPE_TYPE == DriveConnection.ScopeType.FOLDER
            else ""
        ),
        "shared_drive_id": (
            settings.GOOGLE_SHARED_DRIVE_ID
            if settings.GOOGLE_DRIVE_SCOPE_TYPE == DriveConnection.ScopeType.SHARED_DRIVE
            else ""
        ),
        "enabled": True,
        "permission_authority": settings.GOOGLE_PERMISSION_AUTHORITY,
    }


def _transient_connection_from_settings() -> DriveConnection:
    # An unsaved connection for read-only root discovery: listing candidates
    # only reads the delegated subject and scope, never the pk, so a GET can
    # answer without persisting anything (see _active_or_bootstrap_connection
    # for the write path used by the POST endpoints).
    return DriveConnection(**_connection_defaults_from_settings())


def _active_or_bootstrap_connection() -> DriveConnection:
    connection = _active_connection()
    if connection is not None:
        return connection

    return DriveConnection.objects.create(**_connection_defaults_from_settings())


def _root_candidate_payload(candidate: DriveRootCandidate) -> dict[str, str]:
    return {
        "scope_type": candidate.scope_type,
        "root_id": candidate.root_id,
        "name": candidate.name,
        "drive_url": candidate.drive_url,
        "shared_drive_id": candidate.shared_drive_id,
    }


def _root_candidate_error_response(exc: DriveCredentialUnavailableError) -> Response:
    return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)


def _drive_api_error_response(exc: GoogleDriveApiError) -> Response:
    return Response({"detail": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)


def _connection_has_selected_root(connection: DriveConnection) -> bool:
    return bool(connection.effective_root_id)


def _selected_root_payload(connection: DriveConnection) -> dict[str, str]:
    return {
        "scope_type": connection.scope_type,
        "root_folder_id": connection.root_folder_id,
        "shared_drive_id": connection.shared_drive_id,
    }


def _permission_metadata_access_status(report) -> str:
    if report.sampled_files == 0 and report.folder_listing_errors == 0:
        return "no_files"
    if report.unreadable_files == 0 and report.folder_listing_errors == 0:
        return "ok"
    # No file was reachable to sample and folder listing itself failed: the
    # problem is walking the tree, not ACL visibility. Keep this distinct from
    # "blocked" (ACLs sampled but unreadable) so the summary label alone points
    # at the right remediation; folder_listing_errors carries the count either way.
    if report.sampled_files == 0:
        return "listing_failed"
    if report.readable_files == 0:
        return "blocked"
    return "partial"


class DriveRootListView(APIView):
    permission_classes = [IsAdminUser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "drive-roots"

    def get(self, request):
        # Root discovery must not persist anything: an admin lists candidates
        # before any root is chosen, so a transient (unsaved) connection built
        # from settings answers the read without a write side-effect.
        connection = _active_connection() or _transient_connection_from_settings()
        try:
            candidates = GoogleDriveMetadataClient().list_root_candidates(connection)
        except DriveCredentialUnavailableError as exc:
            return _root_candidate_error_response(exc)
        except GoogleDriveApiError as exc:
            return _drive_api_error_response(exc)

        return Response(
            {
                "connection_id": connection.pk,
                "roots": [_root_candidate_payload(candidate) for candidate in candidates],
            }
        )


class DriveRootSelectionView(APIView):
    permission_classes = [IsAdminUser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "drive-roots"

    def post(self, request):
        serializer = DriveRootSelectionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        scope_type = serializer.validated_data["scope_type"]
        root_id = serializer.validated_data["root_id"]

        connection = _active_or_bootstrap_connection()
        try:
            candidates = GoogleDriveMetadataClient().list_root_candidates(connection)
        except DriveCredentialUnavailableError as exc:
            return _root_candidate_error_response(exc)
        except GoogleDriveApiError as exc:
            return _drive_api_error_response(exc)

        selected = next(
            (
                candidate
                for candidate in candidates
                if candidate.scope_type == scope_type and candidate.root_id == root_id
            ),
            None,
        )
        if selected is None:
            return Response(
                {"detail": "Selected Drive root is not visible to this connection."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            connection = DriveConnection.objects.select_for_update().get(pk=connection.pk)
            previous_scope = (
                connection.scope_type,
                connection.root_folder_id,
                connection.shared_drive_id,
            )
            connection.scope_type = selected.scope_type
            if selected.scope_type == DriveConnection.ScopeType.SHARED_DRIVE:
                connection.root_folder_id = ""
                connection.shared_drive_id = selected.root_id
            else:
                connection.root_folder_id = selected.root_id
                connection.shared_drive_id = ""
            selected_scope = (
                connection.scope_type,
                connection.root_folder_id,
                connection.shared_drive_id,
            )
            connection.save(
                update_fields=["scope_type", "root_folder_id", "shared_drive_id", "updated_at"]
            )
            rescoped_document_count = 0
            if selected_scope != previous_scope:
                rescoped_document_count = SourceDocument.objects.filter(
                    connection=connection,
                ).update(
                    retrieval_eligible=False,
                    updated_at=timezone.now(),
                )

        return Response(
            {
                "connection_id": connection.pk,
                "selected_root": _root_candidate_payload(selected),
                "rescoped_document_count": rescoped_document_count,
            }
        )


class DriveDelegatedSubjectView(APIView):
    permission_classes = [IsAdminUser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "drive-roots"

    def post(self, request):
        serializer = DriveDelegatedSubjectSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        delegated_subject_email = serializer.validated_data["delegated_subject_email"]

        connection = _active_or_bootstrap_connection()
        invalidated_document_count = 0
        with transaction.atomic():
            connection = DriveConnection.objects.select_for_update().get(pk=connection.pk)
            if connection.delegated_subject_email != delegated_subject_email:
                connection.delegated_subject_email = delegated_subject_email
                connection.save(update_fields=["delegated_subject_email", "updated_at"])
                invalidated_document_count = SourceDocument.objects.filter(
                    connection=connection,
                    retrieval_eligible=True,
                ).update(
                    retrieval_eligible=False,
                    updated_at=timezone.now(),
                )

        return Response(
            {
                "connection_id": connection.pk,
                "delegated_subject_email": connection.delegated_subject_email,
                "invalidated_document_count": invalidated_document_count,
            }
        )


class DrivePermissionCheckView(APIView):
    permission_classes = [IsAdminUser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "drive-roots"

    def get(self, request):
        # A read-only diagnostic: it never bootstraps. Without a persisted,
        # root-selected connection there is nothing to sample, so report the
        # missing precondition instead of creating a connection row on a GET.
        connection = _active_connection()
        if connection is None:
            return Response(
                {"detail": "No enabled Drive connection is configured."},
                status=status.HTTP_409_CONFLICT,
            )
        if not _connection_has_selected_root(connection):
            return Response(
                {"detail": "No Drive root has been selected for this connection."},
                status=status.HTTP_409_CONFLICT,
            )

        try:
            report = GoogleDriveMetadataClient().check_permission_access(connection)
        except DriveCredentialUnavailableError as exc:
            return _root_candidate_error_response(exc)
        except GoogleDriveApiError as exc:
            return _drive_api_error_response(exc)

        return Response(
            {
                "connection_id": connection.pk,
                "selected_root": _selected_root_payload(connection),
                "permission_metadata_access": _permission_metadata_access_status(report),
                "sampled_files": report.sampled_files,
                "permissions_readable": report.readable_files,
                "permissions_unreadable": report.unreadable_files,
                "folder_listing_errors": report.folder_listing_errors,
                "checked_all_available_files": report.checked_all_available_files,
            }
        )


class DriveSyncView(APIView):
    permission_classes = [IsAdminUser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "drive-sync"

    def post(self, request):
        # The Drive scope is server-side configuration only. Anything in the
        # request body (folder ids, shared-drive ids) is deliberately ignored
        # so a caller can never widen the sync scope through the API.
        connection = DriveConnection.objects.filter(enabled=True).order_by("pk").first()
        if connection is None:
            return Response(
                {"detail": "No enabled Drive connection is configured."},
                status=status.HTTP_409_CONFLICT,
            )
        if not _connection_has_selected_root(connection):
            return Response(
                {"detail": "No Drive root has been selected for this connection."},
                status=status.HTTP_409_CONFLICT,
            )

        # The audit record exists before the work is queued, so a lost or
        # crashed task still leaves a trace of who requested the sync.
        run = DriveSyncRun.create_for_connection(connection, triggered_by=request.user)
        run_drive_sync.delay(run.pk)

        return Response(
            {
                "run_id": run.pk,
                "status": run.status,
                "scope_type": run.scope_type,
                "root_folder_id": run.root_folder_id,
                "shared_drive_id": run.shared_drive_id,
            },
            status=status.HTTP_202_ACCEPTED,
        )
