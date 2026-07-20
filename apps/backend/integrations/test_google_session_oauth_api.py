from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework.authentication import SessionAuthentication
from rest_framework.permissions import AllowAny
from rest_framework.throttling import ScopedRateThrottle

from integrations.drive.google_session_oauth import GoogleSessionOAuthError
from integrations.google_session_oauth_views import (
    GoogleSessionOAuthCallbackView,
    GoogleSessionOAuthStartView,
)


class GoogleSessionOAuthApiTests(TestCase):
    def test_views_are_public_gets_with_explicit_throttles(self):
        expected = {
            GoogleSessionOAuthStartView: "google-session-start",
            GoogleSessionOAuthCallbackView: "google-session-callback",
        }
        for view, throttle_scope in expected.items():
            with self.subTest(view=view):
                self.assertEqual(view.authentication_classes, [SessionAuthentication])
                self.assertEqual(view.permission_classes, [AllowAny])
                self.assertEqual(view.throttle_classes, [ScopedRateThrottle])
                self.assertEqual(view.throttle_scope, throttle_scope)

    def test_start_redirects_with_no_store_headers(self):
        with patch(
            "integrations.google_session_oauth_views.begin_session_authorization",
            return_value="https://accounts.google.test/auth",
        ):
            response = self.client.get(reverse("google-session-oauth-start"))

        self.assertRedirects(
            response,
            "https://accounts.google.test/auth",
            fetch_redirect_response=False,
        )
        self.assertEqual(response["Cache-Control"], "no-store")
        self.assertEqual(response["Referrer-Policy"], "no-referrer")

    def test_callback_logs_in_verified_user_then_starts_drive_flow(self):
        user = get_user_model().objects.create_user(
            username="google-oidc-test",
            email="pilot@example.com",
            password=None,
        )
        with patch(
            "integrations.google_session_oauth_views.complete_session_authorization",
            return_value=user,
        ) as complete:
            response = self.client.get(
                reverse("google-session-oauth-callback"),
                {"state": "test-state", "code": "test-code"},
            )

        self.assertRedirects(response, reverse("drive-oauth-start"), fetch_redirect_response=False)
        self.assertEqual(int(self.client.session["_auth_user_id"]), user.pk)
        self.assertEqual(response["Cache-Control"], "no-store")
        complete.assert_called_once()

    def test_provider_error_is_generic_and_never_logged_in(self):
        with patch(
            "integrations.google_session_oauth_views.complete_session_authorization",
            side_effect=GoogleSessionOAuthError("authorization_response_invalid"),
        ) as complete:
            response = self.client.get(
                reverse("google-session-oauth-callback"),
                {"state": "test-state", "error": "provider-secret-payload"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertNotContains(response, "provider-secret-payload", status_code=400)
        self.assertNotIn("_auth_user_id", self.client.session)
        self.assertTrue(complete.call_args.kwargs["provider_error"])
