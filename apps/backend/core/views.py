from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from core.tasks import smoke_test


class SmokeTaskView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def post(self, request):
        task = smoke_test.delay()

        return Response(
            {
                "task_id": task.id,
                "status": "queued",
            },
            status=status.HTTP_202_ACCEPTED,
        )
