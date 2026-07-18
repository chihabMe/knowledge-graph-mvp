from rest_framework import status
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from integrations.models import DriveConnection, PermissionSyncRun
from integrations.tasks import run_permission_sync


def _run_payload(run: PermissionSyncRun) -> dict[str, int | str]:
    return {
        "run_id": run.pk,
        "status": run.status,
        "connection_id": run.connection_id,
        "documents_seen": run.documents_seen,
        "folders_seen": run.folders_seen,
        "groups_resolved": run.groups_resolved,
        "relationships_touched": run.relationships_touched,
        "relationships_deleted": run.relationships_deleted,
        "documents_verified": run.documents_verified,
        "documents_excluded": run.documents_excluded,
        "error_code": run.error_code,
    }


class PermissionSyncView(APIView):
    permission_classes = [IsAdminUser]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "permission-sync"

    def post(self, request):
        connection = DriveConnection.objects.filter(enabled=True).order_by("pk").first()
        if connection is None:
            return Response(
                {"detail": "No enabled Drive connection is configured."},
                status=status.HTTP_409_CONFLICT,
            )
        if connection.permission_authority != DriveConnection.PermissionAuthority.DELEGATED_ACL:
            return Response(
                {"detail": "Delegated permission synchronization is not active."},
                status=status.HTTP_409_CONFLICT,
            )
        if not connection.effective_root_id:
            return Response(
                {"detail": "No Drive root has been selected for this connection."},
                status=status.HTTP_409_CONFLICT,
            )
        run = PermissionSyncRun.create_for_connection(connection, triggered_by=request.user)
        run_permission_sync.delay(run.pk)
        return Response(
            {"run_id": run.pk, "status": run.status, "connection_id": connection.pk},
            status=status.HTTP_202_ACCEPTED,
        )


class PermissionSyncDetailView(APIView):
    permission_classes = [IsAdminUser]

    def get(self, request, run_id: int):
        try:
            run = PermissionSyncRun.objects.get(pk=run_id)
        except PermissionSyncRun.DoesNotExist:
            return Response({"detail": "Permission sync run not found."}, status=404)
        return Response(_run_payload(run))
