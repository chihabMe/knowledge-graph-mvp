import logging
from secrets import compare_digest

from django.conf import settings
from rest_framework.authentication import SessionAuthentication, get_authorization_header
from rest_framework.permissions import AllowAny, BasePermission
from rest_framework.response import Response
from rest_framework.views import APIView

from health.checks import collect_health
from integrations.freshness import STATUS_OK, build_freshness_report

logger = logging.getLogger(__name__)


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


def _valid_monitor_bearer(request) -> bool:
    expected = settings.FRESHNESS_MONITOR_BEARER_KEY
    if not expected:
        return False
    parts = get_authorization_header(request).split()
    if len(parts) != 2 or parts[0].lower() != b"bearer" or not parts[1]:
        return False
    return compare_digest(parts[1], expected.encode("utf-8"))


class FreshnessMonitorPermission(BasePermission):
    """Staff session or the dedicated monitoring bearer key; nothing else."""

    def has_permission(self, request, view) -> bool:
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated and user.is_staff:
            return True
        return _valid_monitor_bearer(request)


class FreshnessView(APIView):
    """Aggregated synchronization freshness for the monitoring service.

    Non-200 responses are the alert signal, so a warn state must not return
    200. The body carries counts, worst-case ages, and status labels only —
    never user identities, Drive IDs, or document titles.
    """

    authentication_classes = [SessionAuthentication]
    permission_classes = [FreshnessMonitorPermission]

    def get(self, request):
        try:
            report = build_freshness_report()
        except Exception as exc:
            # Class only: aggregation failure must alert, not leak or 500.
            logger.error(
                "freshness report failed closed: %s.%s",
                type(exc).__module__,
                type(exc).__name__,
            )
            return Response({"status": "error"}, status=503)
        http_status = 200 if report.status == STATUS_OK else 503
        return Response(report.as_payload(), status=http_status)
