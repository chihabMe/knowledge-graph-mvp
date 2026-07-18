from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import IsAuthenticated
from rest_framework.throttling import ScopedRateThrottle

from integrations.drive.user_oauth import OAuthStatus, UserDriveOAuthError
from integrations.drive_oauth_views import (
    DriveOAuthCallbackView,
    DriveOAuthDisconnectView,
    DriveOAuthStartView,
    DriveOAuthStatusView,
    DriveVisibilitySyncView,
)


class DriveOAuthApiTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="pilot",
            email="pilot@example.com",
            password="test-password",
        )

    def test_views_use_explicit_session_auth_permissions_and_scoped_throttles(self):
        expected_scopes = {
            DriveOAuthStartView: "drive-oauth-start",
            DriveOAuthCallbackView: "drive-oauth-callback",
            DriveOAuthStatusView: "drive-oauth-status",
            DriveOAuthDisconnectView: "drive-oauth-disconnect",
            DriveVisibilitySyncView: "drive-visibility-sync",
        }
        for view, scope in expected_scopes.items():
            with self.subTest(view=view):
                self.assertEqual(view.authentication_classes, [SessionAuthentication])
                self.assertEqual(view.permission_classes, [IsAuthenticated])
                self.assertEqual(view.throttle_classes, [ScopedRateThrottle])
                self.assertEqual(view.throttle_scope, scope)

    def test_all_oauth_routes_require_authentication(self):
        for name, method in (
            ("drive-oauth-start", self.client.get),
            ("drive-oauth-callback", self.client.get),
            ("drive-oauth-status", self.client.get),
            ("drive-oauth-disconnect", self.client.post),
            ("drive-visibility-sync", self.client.post),
        ):
            with self.subTest(name=name):
                response = method(reverse(name))
                self.assertIn(response.status_code, {401, 403})

    def test_start_redirects_without_exposing_status_data(self):
        self.client.force_login(self.user)
        with patch(
            "integrations.drive_oauth_views.begin_authorization",
            return_value="https://accounts.google.test/o/oauth2/auth",
        ) as begin:
            response = self.client.get(reverse("drive-oauth-start"))

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["Referrer-Policy"], "no-referrer")
        begin.assert_called_once()
        self.assertEqual(begin.call_args.kwargs["user_email"], "pilot@example.com")

    def test_status_returns_only_controlled_fields(self):
        self.client.force_login(self.user)
        with patch(
            "integrations.drive_oauth_views.authorization_status",
            return_value=OAuthStatus(configured=True, connected=False, status="disconnected"),
        ):
            response = self.client.get(reverse("drive-oauth-status"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"configured": True, "connected": False, "status": "disconnected"},
        )
        self.assertEqual(response["Cache-Control"], "no-store")

    def test_provider_error_callback_consumes_state_through_service_boundary(self):
        self.client.force_login(self.user)
        with patch(
            "integrations.drive_oauth_views.complete_authorization",
            side_effect=UserDriveOAuthError("authorization_response_invalid"),
        ) as complete:
            response = self.client.get(
                reverse("drive-oauth-callback"),
                {"state": "test-state", "error": "access_denied"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertTrue(complete.call_args.kwargs["provider_error"])
        self.assertNotContains(response, "access_denied", status_code=400)

    def test_disconnect_returns_success_after_local_service_completes(self):
        self.client.force_login(self.user)
        with patch("integrations.drive_oauth_views.disconnect_authorization") as disconnect:
            response = self.client.post(reverse("drive-oauth-disconnect"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"connected": False, "status": "disconnected"})
        disconnect.assert_called_once_with(user_email="pilot@example.com")

    def test_visibility_sync_queues_only_the_authenticated_identity(self):
        self.client.force_login(self.user)
        with patch(
            "integrations.drive_oauth_views.queue_user_visibility_sync",
            return_value=SimpleNamespace(pk=17, status="queued"),
        ) as queue:
            response = self.client.post(reverse("drive-visibility-sync"))

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json(), {"run_id": 17, "status": "queued"})
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(queue.call_args.kwargs["user_email"], "pilot@example.com")
        self.assertTrue(callable(queue.call_args.kwargs["dispatch"]))

    def test_visibility_sync_rejects_every_request_supplied_field(self):
        self.client.force_login(self.user)
        with patch("integrations.drive_oauth_views.queue_user_visibility_sync") as queue:
            response = self.client.post(
                reverse("drive-visibility-sync"),
                {"file_ids": ["attacker-selected-id"], "user_email": "other@example.com"},
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 400)
        queue.assert_not_called()
        self.assertNotContains(response, "attacker-selected-id", status_code=400)
        self.assertNotContains(response, "other@example.com", status_code=400)
