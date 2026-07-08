from rest_framework import status
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework.views import APIView

from core.tasks import smoke_test


class SmokeTaskView(APIView):
    # Queues real broker work, so it must not be anonymous — admin-only,
    # same bar as the Drive sync endpoint. `make smoke` enqueues the task
    # directly instead of going through HTTP.
    permission_classes = [IsAdminUser]

    def post(self, request):
        task = smoke_test.delay()

        return Response(
            {
                "task_id": task.id,
                "status": "queued",
            },
            status=status.HTTP_202_ACCEPTED,
        )
