import dataclasses
import time
from unittest.mock import patch

import jwt
from django.test import SimpleTestCase, override_settings
from rest_framework.exceptions import AuthenticationFailed
from rest_framework.test import APIRequestFactory

from retrieval.open_webui_identity import (
    MAX_IDENTITY_TOKEN_CHARS,
    OpenWebUIUserPrincipal,
    verify_identity_jwt,
)

IDENTITY_SIGNING_KEY = "identity-" + ("b" * 40)


@override_settings(
    OPEN_WEBUI_IDENTITY_JWT_SECRET=IDENTITY_SIGNING_KEY,
    OPEN_WEBUI_IDENTITY_JWT_HEADER="X-OpenWebUI-User-Jwt",
    OPEN_WEBUI_IDENTITY_JWT_ISSUER="open-webui",
    OPEN_WEBUI_IDENTITY_JWT_MAX_LIFETIME_SECONDS=300,
    OPEN_WEBUI_IDENTITY_JWT_CLOCK_SKEW_SECONDS=10,
)
class OpenWebUIIdentityTests(SimpleTestCase):
    def setUp(self):
        self.factory = APIRequestFactory()
        self.now = int(time.time())

    def claims(self, **overrides):
        values = {
            "sub": "user-123",
            "email": "  Trusted.Reader@Example.COM  ",
            "name": "Trusted Reader",
            "role": "user",
            "iss": "open-webui",
            "iat": self.now,
            "exp": self.now + 300,
        }
        values.update(overrides)
        return values

    def token(self, claims=None, *, signing_key=IDENTITY_SIGNING_KEY, algorithm="HS256"):
        return jwt.encode(claims or self.claims(), signing_key, algorithm=algorithm)

    def request(self, token=None):
        headers = {}
        if token is not None:
            headers["HTTP_X_OPENWEBUI_USER_JWT"] = token
        return self.factory.post("/v1/chat/completions", **headers)

    def assert_invalid(self, token):
        with self.assertRaisesMessage(AuthenticationFailed, "Invalid user identity assertion."):
            verify_identity_jwt(self.request(token))

    def test_valid_assertion_returns_an_immutable_normalized_principal(self):
        principal = verify_identity_jwt(self.request(self.token()))

        self.assertEqual(
            principal,
            OpenWebUIUserPrincipal(
                subject="user-123",
                email="trusted.reader@example.com",
            ),
        )
        self.assertTrue(principal.is_authenticated)
        self.assertFalse(principal.is_anonymous)
        self.assertEqual(principal.pk, "user-123")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            principal.email = "attacker@example.com"

    def test_missing_malformed_and_oversized_tokens_are_rejected(self):
        for token in (None, "", "not-a-jwt", " token", "x" * (MAX_IDENTITY_TOKEN_CHARS + 1)):
            with self.subTest(token=token):
                self.assert_invalid(token)

    def test_none_and_wrong_algorithms_are_rejected(self):
        none_token = jwt.encode(self.claims(), key="", algorithm="none")
        wrong_algorithm_token = self.token(algorithm="HS384")

        self.assert_invalid(none_token)
        self.assert_invalid(wrong_algorithm_token)

    def test_incorrect_signature_and_issuer_are_rejected(self):
        self.assert_invalid(self.token(signing_key="incorrect-" + ("z" * 40)))
        self.assert_invalid(self.token(self.claims(iss="another-service")))

    def test_every_authorization_claim_is_required(self):
        for claim in ("sub", "email", "iss", "iat", "exp"):
            values = self.claims()
            del values[claim]
            with self.subTest(claim=claim):
                self.assert_invalid(self.token(values))

    def test_subject_must_be_a_bounded_non_empty_string(self):
        for subject in ("", " ", 123, "x" * 256):
            with self.subTest(subject=subject):
                self.assert_invalid(self.token(self.claims(sub=subject)))

    def test_expired_future_and_unreasonably_long_tokens_are_rejected(self):
        invalid_claims = (
            self.claims(iat=self.now - 400, exp=self.now - 100),
            self.claims(iat=self.now + 60, exp=self.now + 100),
            self.claims(exp=self.now + 301),
            self.claims(exp=self.now),
        )
        for claims in invalid_claims:
            with self.subTest(claims=claims):
                self.assert_invalid(self.token(claims))

    def test_numeric_dates_reject_booleans_strings_and_non_finite_values(self):
        for claim, value in (
            ("iat", True),
            ("iat", str(self.now)),
            ("exp", False),
            ("exp", float("inf")),
        ):
            with self.subTest(claim=claim, value=value):
                self.assert_invalid(self.token(self.claims(**{claim: value})))

    def test_missing_and_malformed_email_are_rejected(self):
        for email in ("", "not-an-email", 123):
            with self.subTest(email=email):
                self.assert_invalid(self.token(self.claims(email=email)))

    @patch("retrieval.open_webui_identity.normalize_trusted_email")
    def test_email_is_not_processed_before_signature_verification(self, normalize_mock):
        self.assert_invalid(self.token(signing_key="incorrect-" + ("z" * 40)))
        normalize_mock.assert_not_called()
