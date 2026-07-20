from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from integrations.drive import google_session_oauth


class FakeFlow:
    def __init__(self, *, state=None, exchange_error=False):
        self.state = state
        self.code_verifier = "v" * 64
        self.credentials = SimpleNamespace(id_token="test-id-token")
        self.exchange_error = exchange_error

    def authorization_url(self, **kwargs):
        self.authorization_kwargs = kwargs
        return f"https://accounts.google.test/auth?state={self.state}", self.state

    def fetch_token(self, **kwargs):
        self.fetch_kwargs = kwargs
        if self.exchange_error:
            raise RuntimeError("provider token payload must not escape")


@override_settings(
    GOOGLE_SESSION_OAUTH_ENABLED=True,
    GOOGLE_CLIENT_ID="test-login.apps.googleusercontent.com",
    GOOGLE_CLIENT_SECRET="test-google-client-secret-value",
    GOOGLE_SESSION_OAUTH_REDIRECT_URI=("https://api.example.com/api/session/google/callback"),
    GOOGLE_SESSION_OAUTH_STATE_MAX_AGE_SECONDS=600,
    GOOGLE_USER_OAUTH_ALLOWED_DOMAIN="example.com",
)
class GoogleSessionOAuthTests(TestCase):
    @staticmethod
    def valid_claims(**overrides):
        return {
            "iss": "https://accounts.google.com",
            "aud": "test-login.apps.googleusercontent.com",
            "sub": "google-subject-1",
            "email": "pilot@example.com",
            "email_verified": True,
            "hd": "example.com",
            "nonce": "test-nonce",
            **overrides,
        }

    def begin(self):
        session = self.client.session
        flow = FakeFlow(state="test-state")
        with (
            patch.object(
                google_session_oauth.secrets,
                "token_urlsafe",
                side_effect=["test-state", "test-nonce"],
            ),
            patch.object(google_session_oauth, "_new_flow", return_value=flow),
        ):
            url = google_session_oauth.begin_session_authorization(session=session)
        return session, url, flow

    def complete(self, *, session, flow=None, claims=None, state="test-state"):
        callback_flow = flow or FakeFlow(state=state)
        with (
            patch.object(google_session_oauth, "_new_flow", return_value=callback_flow),
            patch.object(
                google_session_oauth,
                "_verified_claims",
                return_value=claims or self.valid_claims(),
            ),
        ):
            return google_session_oauth.complete_session_authorization(
                session=session,
                state=state,
                code="test-code",
            )

    def test_start_uses_identity_only_scopes_and_session_bound_pkce(self):
        session, url, flow = self.begin()

        self.assertIn("state=test-state", url)
        stored = session[google_session_oauth._SESSION_KEY]
        self.assertNotIn("test-state", str(stored))
        self.assertNotIn("test-nonce", str(stored))
        self.assertEqual(stored["code_verifier"], "v" * 64)
        self.assertEqual(flow.authorization_kwargs["access_type"], "online")
        self.assertEqual(flow.authorization_kwargs["include_granted_scopes"], "false")
        self.assertEqual(flow.authorization_kwargs["prompt"], "select_account")
        self.assertEqual(flow.authorization_kwargs["hd"], "example.com")
        self.assertEqual(flow.authorization_kwargs["nonce"], "test-nonce")
        self.assertNotIn("drive", " ".join(google_session_oauth.REQUIRED_SCOPES))

    def test_state_is_short_lived_single_use_and_consumed_before_provider_error(self):
        session, _, _ = self.begin()
        with self.assertRaisesRegex(
            google_session_oauth.GoogleSessionOAuthError, "invalid_oauth_state"
        ):
            self.complete(session=session, state="wrong-state")
        with self.assertRaisesRegex(
            google_session_oauth.GoogleSessionOAuthError, "invalid_oauth_state"
        ):
            self.complete(session=session)

        session, _, _ = self.begin()
        stored = session[google_session_oauth._SESSION_KEY]
        stored["created_at"] -= 601
        session[google_session_oauth._SESSION_KEY] = stored
        session.save()
        with self.assertRaisesRegex(
            google_session_oauth.GoogleSessionOAuthError, "invalid_oauth_state"
        ):
            self.complete(session=session)

        session, _, _ = self.begin()
        with self.assertRaisesRegex(
            google_session_oauth.GoogleSessionOAuthError,
            "authorization_response_invalid",
        ):
            google_session_oauth.complete_session_authorization(
                session=session,
                state="test-state",
                code=None,
                provider_error=True,
            )
        self.assertNotIn(google_session_oauth._SESSION_KEY, session)

    def test_claims_require_nonce_issuer_audience_verified_email_and_domain(self):
        invalid_claims = (
            self.valid_claims(nonce="wrong"),
            self.valid_claims(iss="https://attacker.example"),
            self.valid_claims(aud="another-client.apps.googleusercontent.com"),
            self.valid_claims(email_verified=False),
            self.valid_claims(email="pilot@another.example"),
            self.valid_claims(hd="another.example"),
            self.valid_claims(sub=""),
        )
        for claims in invalid_claims:
            with self.subTest(claims=claims):
                with self.assertRaises(google_session_oauth.GoogleSessionOAuthError):
                    google_session_oauth._validated_identity(
                        claims,
                        expected_nonce_digest=google_session_oauth._digest("test-nonce"),
                    )

    def test_success_creates_only_non_staff_unusable_password_user(self):
        session, _, _ = self.begin()
        user = self.complete(session=session)

        user.refresh_from_db()
        self.assertEqual(user.email, "pilot@example.com")
        self.assertFalse(user.has_usable_password())
        self.assertFalse(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertNotIn(google_session_oauth._SESSION_KEY, session)
        self.assertEqual(get_user_model().objects.count(), 1)

    def test_exchange_failure_is_controlled_and_creates_no_user(self):
        session, _, _ = self.begin()
        with self.assertLogs(
            "integrations.drive.google_session_oauth", level="WARNING"
        ) as captured:
            with self.assertRaisesRegex(
                google_session_oauth.GoogleSessionOAuthError,
                "authorization_exchange_failed",
            ):
                self.complete(
                    session=session,
                    flow=FakeFlow(state="test-state", exchange_error=True),
                )
        self.assertFalse(get_user_model().objects.exists())
        self.assertIn("RuntimeError", " ".join(captured.output))
        self.assertNotIn("provider token payload", " ".join(captured.output))

    def test_same_email_from_different_subject_is_denied(self):
        session, _, _ = self.begin()
        self.complete(session=session)
        session, _, _ = self.begin()
        with self.assertRaisesRegex(
            google_session_oauth.GoogleSessionOAuthError, "identity_conflict"
        ):
            self.complete(
                session=session,
                claims=self.valid_claims(sub="different-google-subject"),
            )
