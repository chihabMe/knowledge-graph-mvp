from django.conf import settings
from rest_framework import status
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from integrations.drive.client import DriveRootCandidate
from integrations.drive.google_client import (
    GoogleDriveMetadataClient,
    MissingServiceAccountKeyError,
)
from integrations.models import DriveConnection, DriveSyncRun
from integrations.serializers import DriveRootSelectionSerializer
from integrations.tasks import run_drive_sync


def _active_or_bootstrap_connection() -> DriveConnection:
    connection = DriveConnection.objects.filter(enabled=True).order_by("pk").first()
    if connection is not None:
        return connection

    return DriveConnection.objects.create(
        workspace_domain=settings.GOOGLE_WORKSPACE_DOMAIN,
        delegated_subject_email=settings.GOOGLE_DRIVE_DELEGATED_SUBJECT,
        credential_reference="GOOGLE_SERVICE_ACCOUNT_FILE",
        scope_type=settings.GOOGLE_DRIVE_SCOPE_TYPE,
        root_folder_id=(
            settings.GOOGLE_DRIVE_ROOT_ID
            if settings.GOOGLE_DRIVE_SCOPE_TYPE == DriveConnection.ScopeType.FOLDER
            else ""
        ),
        shared_drive_id=(
            settings.GOOGLE_SHARED_DRIVE_ID
            if settings.GOOGLE_DRIVE_SCOPE_TYPE == DriveConnection.ScopeType.SHARED_DRIVE
            else ""
        ),
        enabled=True,
    )


def _root_candidate_payload(candidate: DriveRootCandidate) -> dict[str, str]:
    return {
        "scope_type": candidate.scope_type,
        "root_id": candidate.root_id,
        "name": candidate.name,
        "drive_url": candidate.drive_url,
        "shared_drive_id": candidate.shared_drive_id,
    }


def _root_candidate_error_response(exc: MissingServiceAccountKeyError) -> Response:
    return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)


def _connection_has_selected_root(connection: DriveConnection) -> bool:
    if connection.scope_type == DriveConnection.ScopeType.SHARED_DRIVE:
        return bool(connection.shared_drive_id)
    return bool(connection.root_folder_id)


class DriveRootListView(APIView):
    permission_classes = [IsAdminUser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "drive-roots"

    def get(self, request):
        connection = _active_or_bootstrap_connection()
        try:
            candidates = GoogleDriveMetadataClient().list_root_candidates(connection)
        except MissingServiceAccountKeyError as exc:
            return _root_candidate_error_response(exc)

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
        except MissingServiceAccountKeyError as exc:
            return _root_candidate_error_response(exc)

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

        connection.scope_type = selected.scope_type
        if selected.scope_type == DriveConnection.ScopeType.SHARED_DRIVE:
            connection.root_folder_id = ""
            connection.shared_drive_id = selected.root_id
        else:
            connection.root_folder_id = selected.root_id
            connection.shared_drive_id = ""
        connection.save(
            update_fields=["scope_type", "root_folder_id", "shared_drive_id", "updated_at"]
        )

        return Response(
            {
                "connection_id": connection.pk,
                "selected_root": _root_candidate_payload(selected),
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
