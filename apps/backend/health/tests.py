from unittest.mock import patch

from django.test import SimpleTestCase
from django.urls import reverse
from rest_framework.test import APIClient


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
