from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient

from integrations.freshness import STATUS_OK, STATUS_WARN


class HealthEndpointTests(SimpleTestCase):
    def setUp(self):
        self.client = APIClient()

    @patch(
        "health.checks.SERVICE_CHECKS",
        {
            "django": lambda: None,
            "postgres": lambda: None,
            "redis": lambda: None,
            "neo4j": lambda: None,
        },
    )
    def test_health_endpoint_returns_ok_when_services_are_available(self):
        response = self.client.get(reverse("health"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
        self.assertEqual(response.json()["services"]["postgres"], "ok")

    @patch(
        "health.checks.SERVICE_CHECKS",
        {
            "django": lambda: None,
            "postgres": lambda: (_ for _ in ()).throw(RuntimeError("connection failed")),
            "redis": lambda: None,
            "neo4j": lambda: None,
        },
    )
    def test_health_endpoint_returns_degraded_without_leaking_errors(self):
        response = self.client.get(reverse("health"))

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "degraded")
        self.assertEqual(response.json()["services"]["postgres"], "error")
        self.assertNotIn("connection failed", response.content.decode())

    @patch(
        "health.checks.SERVICE_CHECKS",
        {
            "django": lambda: None,
            "postgres": lambda: None,
            "redis": lambda: None,
            "neo4j": lambda: None,
            "spicedb": lambda: None,
        },
    )
    def test_spicedb_health_success_is_reported(self):
        response = self.client.get(reverse("health"))
        self.assertEqual(response.json()["services"]["spicedb"], "ok")

    @patch(
        "health.checks.SERVICE_CHECKS",
        {
            "django": lambda: None,
            "postgres": lambda: None,
            "redis": lambda: None,
            "neo4j": lambda: None,
            "spicedb": lambda: (_ for _ in ()).throw(RuntimeError("dns.internal:50051 secret-key")),
        },
    )
    def test_spicedb_health_failure_does_not_leak_details(self):
        response = self.client.get(reverse("health"))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["services"]["spicedb"], "error")
        self.assertNotIn("dns.internal", response.content.decode())
        self.assertNotIn("secret-key", response.content.decode())

    def test_spicedb_probe_is_bounded_and_reuses_one_client(self):
        import health.checks as checks

        with patch.object(checks, "_spicedb_probe", None):
            with patch("health.checks.AuthzedSpiceDB") as client_class:
                checks.check_spicedb()
                checks.check_spicedb()
            client_class.assert_called_once_with(timeout=1)
            self.assertEqual(client_class.return_value.check.call_count, 2)


@override_settings(FRESHNESS_MONITOR_BEARER_KEY="m" * 32)
class FreshnessEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.staff = get_user_model().objects.create_user(
            username="monitor-admin",
            is_staff=True,
        )

    @staticmethod
    def report(status=STATUS_OK):
        return SimpleNamespace(
            status=status,
            as_payload=lambda: {
                "status": status,
                "active_connections": 1,
                "worst_last_success_age_seconds": 30,
            },
        )

    @patch("health.views.build_freshness_report")
    def test_staff_session_can_read_identity_free_report(self, build_report):
        build_report.return_value = self.report()
        self.client.force_authenticate(self.staff)

        response = self.client.get(reverse("health-freshness"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], STATUS_OK)

    @patch("health.views.build_freshness_report")
    def test_monitor_bearer_can_read_report(self, build_report):
        build_report.return_value = self.report()

        response = self.client.get(
            reverse("health-freshness"),
            HTTP_AUTHORIZATION=f"Bearer {'m' * 32}",
        )

        self.assertEqual(response.status_code, 200)

    @patch("health.views.build_freshness_report")
    def test_anonymous_wrong_bearer_and_non_staff_are_denied(self, build_report):
        build_report.return_value = self.report()
        self.assertEqual(self.client.get(reverse("health-freshness")).status_code, 403)
        self.assertEqual(
            self.client.get(
                reverse("health-freshness"),
                HTTP_AUTHORIZATION="Bearer wrong",
            ).status_code,
            403,
        )
        user = get_user_model().objects.create_user(
            username="ordinary-user",
        )
        self.client.force_authenticate(user)
        self.assertEqual(self.client.get(reverse("health-freshness")).status_code, 403)

    @patch("health.views.build_freshness_report")
    def test_warning_returns_non_200_for_external_monitor(self, build_report):
        build_report.return_value = self.report(STATUS_WARN)

        response = self.client.get(
            reverse("health-freshness"),
            HTTP_AUTHORIZATION=f"Bearer {'m' * 32}",
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], STATUS_WARN)

    @patch(
        "health.views.build_freshness_report",
        side_effect=RuntimeError("pilot@example.com private-drive-id"),
    )
    def test_aggregation_failure_is_redacted_and_fails_closed(self, build_report):
        with self.assertLogs("health.views", level="ERROR") as captured:
            response = self.client.get(
                reverse("health-freshness"),
                HTTP_AUTHORIZATION=f"Bearer {'m' * 32}",
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"status": "error"})
        combined = response.content.decode() + "\n".join(captured.output)
        self.assertNotIn("pilot@example.com", combined)
        self.assertNotIn("private-drive-id", combined)
