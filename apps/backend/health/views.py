from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from health.checks import collect_health


class HealthView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        status, services = collect_health()
        http_status = 200 if status == "ok" else 503

        return Response(
            {
                "status": status,
                "services": services,
            },
            status=http_status,
        )
