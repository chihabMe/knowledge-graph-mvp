from unittest.mock import patch

from django.test import SimpleTestCase, override_settings
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIRequestFactory

from retrieval.open_webui_auth import (
    OpenWebUIServiceAuthentication,
    OpenWebUIServicePrincipal,
    OpenWebUIUserAuthentication,
    verify_service_bearer,
)
from retrieval.open_webui_identity import OpenWebUIUserPrincipal

SERVICE_KEY = "service-" + ("a" * 40)


@override_settings(
    OPEN_WEBUI_COMPATIBLE_API_ENABLED=True,
    OPEN_WEBUI_BACKEND_API_KEY=SERVICE_KEY,
)
class OpenWebUIServiceAuthenticationTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()

    def request(self, authorization=None):
        headers = {}
        if authorization is not None:
            headers["HTTP_AUTHORIZATION"] = authorization
        return self.factory.get("/v1/models", **headers)

    def test_correct_bearer_key_authenticates_only_the_service(self):
        principal, auth = OpenWebUIServiceAuthentication().authenticate(
            self.request(f"Bearer {SERVICE_KEY}")
        )

        self.assertEqual(principal, OpenWebUIServicePrincipal())
        self.assertTrue(principal.is_authenticated)
        self.assertFalse(principal.is_anonymous)
        self.assertEqual(principal.pk, "open-webui")
        self.assertFalse(hasattr(principal, "email"))
        self.assertIsNone(auth)

    def test_scheme_is_case_insensitive(self):
        principal = verify_service_bearer(self.request(f"bEaReR {SERVICE_KEY}"))
        self.assertEqual(principal, OpenWebUIServicePrincipal())

    def test_missing_malformed_and_incorrect_credentials_are_indistinguishable(self):
        invalid_headers = (
            None,
            "Basic credentials",
            "Bearer",
            f"Bearer {SERVICE_KEY} extra",
            "Bearer wrong-service-key",
        )
        for authorization in invalid_headers:
            with self.subTest(authorization=authorization):
                with self.assertRaisesMessage(AuthenticationFailed, "Invalid service credentials."):
                    verify_service_bearer(self.request(authorization))

    @override_settings(OPEN_WEBUI_COMPATIBLE_API_ENABLED=False)
    def test_disabled_endpoint_rejects_even_the_correct_key(self):
        with self.assertRaises(AuthenticationFailed):
            verify_service_bearer(self.request(f"Bearer {SERVICE_KEY}"))

    @patch("retrieval.open_webui_auth.compare_digest", return_value=True)
    def test_key_comparison_uses_constant_time_primitive(self, compare_digest_mock):
        verify_service_bearer(self.request(f"Bearer {SERVICE_KEY}"))
        compare_digest_mock.assert_called_once_with(SERVICE_KEY.encode(), SERVICE_KEY.encode())

    def test_authentication_challenge_uses_bearer_scheme(self):
        self.assertEqual(OpenWebUIServiceAuthentication().authenticate_header(None), "Bearer")

    @patch("retrieval.open_webui_auth.verify_identity_jwt")
    @patch("retrieval.open_webui_auth.verify_service_bearer")
    def test_user_authentication_verifies_service_before_identity(
        self, verify_service_mock, verify_identity_mock
    ):
        calls = []
        principal = OpenWebUIUserPrincipal("user-123", "reader@example.com")
        verify_service_mock.side_effect = lambda _request: calls.append("service")
        verify_identity_mock.side_effect = lambda _request: calls.append("identity") or principal

        authenticated, auth = OpenWebUIUserAuthentication().authenticate(self.request())

        self.assertEqual(authenticated, principal)
        self.assertIsNone(auth)
        self.assertEqual(calls, ["service", "identity"])
        verify_service_mock.assert_called_once()
        verify_identity_mock.assert_called_once()

    @patch("retrieval.open_webui_auth.verify_identity_jwt")
    @patch(
        "retrieval.open_webui_auth.verify_service_bearer",
        side_effect=AuthenticationFailed("service failure"),
    )
    def test_service_failure_prevents_identity_verification_and_uses_generic_error(
        self, verify_service_mock, verify_identity_mock
    ):
        with self.assertRaisesMessage(AuthenticationFailed, "Invalid credentials."):
            OpenWebUIUserAuthentication().authenticate(self.request())

        verify_service_mock.assert_called_once()
        verify_identity_mock.assert_not_called()
