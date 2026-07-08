from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient


class ProjectImportTests(SimpleTestCase):
    def test_django_project_imports(self):
        from django.conf import settings

        self.assertEqual(settings.ROOT_URLCONF, "config.urls")


class SmokeTaskEndpointTests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.admin = get_user_model().objects.create_user(
            username="admin",
            email="admin@example.com",
            password="test-password",
            is_staff=True,
        )

    def test_anonymous_request_is_rejected(self):
        response = self.client.post(reverse("smoke-task"))

        # 403 (not 401) is pinned by the project's DRF auth config:
        # SessionAuthentication only, which never issues WWW-Authenticate.
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_non_admin_request_is_rejected(self):
        user = get_user_model().objects.create_user(
            username="regular",
            email="regular@example.com",
            password="test-password",
        )
        self.client.force_authenticate(user=user)

        response = self.client.post(reverse("smoke-task"))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(
        CELERY_BROKER_URL="memory://",
        CELERY_RESULT_BACKEND="cache+memory://",
        CELERY_TASK_ALWAYS_EAGER=True,
        CELERY_TASK_STORE_EAGER_RESULT=True,
    )
    def test_admin_can_queue_smoke_task(self):
        self.client.force_authenticate(user=self.admin)

        response = self.client.post(reverse("smoke-task"))

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.json()["status"], "queued")
        self.assertTrue(response.json()["task_id"])
