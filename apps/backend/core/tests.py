from django.test import SimpleTestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient


class ProjectImportTests(SimpleTestCase):
    def test_django_project_imports(self):
        from django.conf import settings

        self.assertEqual(settings.ROOT_URLCONF, "config.urls")


class SmokeTaskEndpointTests(SimpleTestCase):
    def setUp(self):
        self.client = APIClient()

    @override_settings(
        CELERY_BROKER_URL="memory://",
        CELERY_RESULT_BACKEND="cache+memory://",
        CELERY_TASK_ALWAYS_EAGER=True,
        CELERY_TASK_STORE_EAGER_RESULT=True,
    )
    def test_smoke_task_endpoint_queues_task(self):
        response = self.client.post(reverse("smoke-task"))

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.json()["status"], "queued")
        self.assertTrue(response.json()["task_id"])
