from rest_framework import status
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from integrations.models import DriveConnection, DriveSyncRun
from integrations.tasks import run_drive_sync


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
