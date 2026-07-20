import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from cryptography.fernet import Fernet
from django.test import TestCase, override_settings

from integrations.drive import user_oauth
from integrations.drive.token_encryption import decrypt_refresh_credential
from integrations.models import (
    DriveConnection,
    GoogleDriveAuthorization,
    SourceDocument,
    UserDocumentVisibility,
)


class FakeFlow:
    def __init__(
        self,
        *,
        state=None,
        refresh_credential="test-refresh-credential",
        scopes=None,
        exchange_error=False,
    ):
        self.state = state
        self.code_verifier = "v" * 64
        self.redirect_uri = ""
        self.client_config = {"client_id": "test-client.apps.googleusercontent.com"}
        self.credentials = SimpleNamespace(
            id_token="test-id-token",
            refresh_token=refresh_credential,
            granted_scopes=None,
        )
        self.oauth2session = SimpleNamespace(
            token={"scope": scopes or " ".join(sorted(user_oauth.REQUIRED_SCOPES))}
        )
        self.exchange_error = exchange_error

    def authorization_url(self, **kwargs):
        self.authorization_kwargs = kwargs
        return f"https://accounts.google.test/o/oauth2/auth?state={self.state}", self.state

    def fetch_token(self, **kwargs):
        self.fetch_kwargs = kwargs
        if self.exchange_error:
            raise RuntimeError("provider payload that must not escape")


@override_settings(
    GOOGLE_PERMISSION_AUTHORITY=DriveConnection.PermissionAuthority.PER_USER_OAUTH,
    GOOGLE_USER_OAUTH_CLIENT_ID="test-client.apps.googleusercontent.com",
    GOOGLE_USER_OAUTH_ALLOWED_DOMAIN="example.com",
    GOOGLE_USER_OAUTH_REDIRECT_URI="https://api.example.com/api/drive/oauth/callback",
    GOOGLE_USER_OAUTH_STATE_MAX_AGE_SECONDS=600,
)
class UserDriveOAuthTests(TestCase):
    user_email = "pilot@example.com"

    def setUp(self):
        self.temporary_directory = TemporaryDirectory()
        directory = Path(self.temporary_directory.name)
        self.keyring_file = directory / "keyring.json"
        self.keyring_file.write_text(
            json.dumps(
                {
                    "active_version": "test-v1",
                    "keys": {"test-v1": Fernet.generate_key().decode("ascii")},
                }
            ),
            encoding="utf-8",
        )
        self.settings_override = override_settings(
            GOOGLE_USER_TOKEN_ENCRYPTION_KEY_FILE=str(self.keyring_file)
        )
        self.settings_override.enable()
        self.connection = DriveConnection.objects.create(
            workspace_domain="example.com",
            permission_authority=DriveConnection.PermissionAuthority.PER_USER_OAUTH,
            root_folder_id="selected-root",
        )

    def tearDown(self):
        self.settings_override.disable()
        self.temporary_directory.cleanup()

    @staticmethod
    def valid_claims(**overrides):
        return {
            "iss": "https://accounts.google.com",
            "aud": "test-client.apps.googleusercontent.com",
            "sub": "google-subject-1",
            "email": "pilot@example.com",
            "email_verified": True,
            "hd": "example.com",
            **overrides,
        }

    def begin(self, *, flow_factory=None):
        session = self.client.session
        flows = []

        def factory(state=None, **_kwargs):
            flow = flow_factory(state) if flow_factory else FakeFlow(state=state)
            flows.append(flow)
            return flow

        with patch.object(user_oauth, "_new_flow", side_effect=factory):
            authorization_url = user_oauth.begin_authorization(
                session=session,
                user_email=self.user_email,
            )
        state = parse_qs(urlsplit(authorization_url).query)["state"][0]
        return session, state, flows[0]

    def complete(self, *, session, state, flow=None, claims=None, provider_error=False):
        callback_flow = flow or FakeFlow(state=state)
        with (
            patch.object(user_oauth, "_new_flow", return_value=callback_flow),
            patch.object(
                user_oauth,
                "_verified_claims",
                return_value=claims or self.valid_claims(),
            ),
        ):
            return user_oauth.complete_authorization(
                session=session,
                user_email=self.user_email,
                state=state,
                authorization_code="test-code",
                provider_error=provider_error,
            )

    def test_start_uses_offline_consent_and_stores_only_state_digest(self):
        session, state, flow = self.begin()

        stored = session[user_oauth._SESSION_KEY]
        self.assertNotEqual(stored["state_digest"], state)
        self.assertNotIn(state, json.dumps(stored))
        self.assertEqual(stored["code_verifier"], "v" * 64)
        self.assertEqual(flow.authorization_kwargs["access_type"], "offline")
        self.assertEqual(flow.authorization_kwargs["prompt"], "consent")
        self.assertEqual(flow.authorization_kwargs["include_granted_scopes"], "false")
        self.assertEqual(flow.authorization_kwargs["login_hint"], self.user_email)
        self.assertEqual(flow.authorization_kwargs["hd"], "example.com")

    def test_state_is_required_correct_short_lived_and_single_use(self):
        session, state, _ = self.begin()
        with self.assertRaisesRegex(user_oauth.UserDriveOAuthError, "invalid_oauth_state"):
            self.complete(session=session, state="wrong-state")
        with self.assertRaisesRegex(user_oauth.UserDriveOAuthError, "invalid_oauth_state"):
            self.complete(session=session, state=state)

        session, state, _ = self.begin()
        stored = session[user_oauth._SESSION_KEY]
        stored["created_at"] -= 601
        session[user_oauth._SESSION_KEY] = stored
        session.save()
        with self.assertRaisesRegex(user_oauth.UserDriveOAuthError, "invalid_oauth_state"):
            self.complete(session=session, state=state)

    def test_provider_error_consumes_state_before_any_exchange(self):
        session, state, _ = self.begin()
        with self.assertRaisesRegex(
            user_oauth.UserDriveOAuthError, "authorization_response_invalid"
        ):
            self.complete(session=session, state=state, provider_error=True)
        with self.assertRaisesRegex(user_oauth.UserDriveOAuthError, "invalid_oauth_state"):
            self.complete(session=session, state=state)

    def test_exchange_failure_is_controlled_and_persists_nothing(self):
        session, state, _ = self.begin()
        with self.assertLogs("integrations.drive.user_oauth", level="WARNING") as captured:
            with self.assertRaisesRegex(
                user_oauth.UserDriveOAuthError, "authorization_exchange_failed"
            ):
                self.complete(
                    session=session,
                    state=state,
                    flow=FakeFlow(state=state, exchange_error=True),
                )
        self.assertFalse(GoogleDriveAuthorization.objects.exists())
        self.assertIn("RuntimeError", " ".join(captured.output))
        self.assertNotIn("provider payload", " ".join(captured.output))

    def test_exchange_uses_only_code_after_state_validation(self):
        session, state, _ = self.begin()
        callback_flow = FakeFlow(state=state)
        self.complete(session=session, state=state, flow=callback_flow)

        self.assertEqual(callback_flow.fetch_kwargs, {"code": "test-code"})

    def test_claims_are_bound_to_issuer_audience_verified_email_and_domain(self):
        invalid_claims = (
            self.valid_claims(iss="https://attacker.example"),
            self.valid_claims(aud="another-client.apps.googleusercontent.com"),
            self.valid_claims(email_verified=False),
            self.valid_claims(email="another@example.com"),
            self.valid_claims(hd="another.example"),
            self.valid_claims(sub=""),
        )
        for claims in invalid_claims:
            with self.subTest(claims=claims):
                session, state, _ = self.begin()
                with self.assertRaises(user_oauth.UserDriveOAuthError):
                    self.complete(session=session, state=state, claims=claims)
        self.assertFalse(GoogleDriveAuthorization.objects.exists())

    def test_expired_or_invalid_id_token_is_denied(self):
        session, state, _ = self.begin()
        with (
            patch.object(user_oauth, "_new_flow", return_value=FakeFlow(state=state)),
            patch.object(
                user_oauth.id_token,
                "verify_oauth2_token",
                side_effect=ValueError("expired provider token payload"),
            ),
        ):
            with self.assertRaisesRegex(user_oauth.UserDriveOAuthError, "identity_token_invalid"):
                user_oauth.complete_authorization(
                    session=session,
                    user_email=self.user_email,
                    state=state,
                    authorization_code="test-code",
                )

    def test_missing_drive_scope_or_first_refresh_credential_is_denied(self):
        session, state, _ = self.begin()
        scopes = " ".join(sorted(user_oauth.REQUIRED_SCOPES - {user_oauth.DRIVE_METADATA_SCOPE}))
        with self.assertRaisesRegex(user_oauth.UserDriveOAuthError, "required_scope_missing"):
            self.complete(
                session=session,
                state=state,
                flow=FakeFlow(state=state, scopes=scopes),
            )

        session, state, _ = self.begin()
        with self.assertRaisesRegex(user_oauth.UserDriveOAuthError, "refresh_credential_missing"):
            self.complete(
                session=session,
                state=state,
                flow=FakeFlow(state=state, refresh_credential=None),
            )

    def test_overbroad_drive_scope_is_denied(self):
        session, state, _ = self.begin()
        scopes = " ".join(
            sorted(user_oauth.REQUIRED_SCOPES | {"https://www.googleapis.com/auth/drive.readonly"})
        )
        with self.assertRaisesRegex(user_oauth.UserDriveOAuthError, "overbroad_drive_scope"):
            self.complete(
                session=session,
                state=state,
                flow=FakeFlow(state=state, scopes=scopes),
            )

    def test_first_connect_persists_ciphertext_but_creates_no_document_grant(self):
        session, state, _ = self.begin()
        authorization = self.complete(session=session, state=state)

        authorization.refresh_from_db()
        ciphertext = bytes(authorization.encrypted_refresh_credential)
        self.assertNotIn(b"test-refresh-credential", ciphertext)
        self.assertEqual(
            decrypt_refresh_credential(
                ciphertext=ciphertext,
                key_version=authorization.encryption_key_version,
            ),
            "test-refresh-credential",
        )
        self.assertEqual(authorization.status, GoogleDriveAuthorization.Status.ACTIVE)
        self.assertFalse(UserDocumentVisibility.objects.exists())

    def test_reconnect_preserves_existing_refresh_credential_and_invalidates_evidence(self):
        session, state, _ = self.begin()
        authorization = self.complete(session=session, state=state)
        original_ciphertext = bytes(authorization.encrypted_refresh_credential)
        original_generation = authorization.authorization_generation
        document = SourceDocument.objects.create(
            connection=self.connection,
            drive_file_id="indexed-file",
            title="Indexed file",
            mime_type="application/pdf",
        )
        UserDocumentVisibility.objects.create(
            authorization=authorization,
            source_document=document,
            connection_generation=self.connection.authorization_generation,
            authorization_generation=authorization.authorization_generation,
            state=UserDocumentVisibility.State.VERIFIED_VISIBLE,
        )

        session, state, _ = self.begin()
        reconnected = self.complete(
            session=session,
            state=state,
            flow=FakeFlow(state=state, refresh_credential=None),
        )

        self.assertEqual(bytes(reconnected.encrypted_refresh_credential), original_ciphertext)
        self.assertNotEqual(reconnected.authorization_generation, original_generation)
        self.assertFalse(UserDocumentVisibility.objects.exists())

    def test_changed_google_subject_disconnects_old_authorization(self):
        session, state, _ = self.begin()
        old_authorization = self.complete(session=session, state=state)

        session, state, _ = self.begin()
        new_authorization = self.complete(
            session=session,
            state=state,
            claims=self.valid_claims(sub="google-subject-2"),
        )

        old_authorization.refresh_from_db()
        self.assertEqual(old_authorization.status, GoogleDriveAuthorization.Status.DISCONNECTED)
        self.assertEqual(bytes(old_authorization.encrypted_refresh_credential), b"")
        self.assertEqual(new_authorization.status, GoogleDriveAuthorization.Status.ACTIVE)

    def test_disconnect_denies_and_wipes_locally_when_remote_revocation_fails(self):
        session, state, _ = self.begin()
        authorization = self.complete(session=session, state=state)
        document = SourceDocument.objects.create(
            connection=self.connection,
            drive_file_id="indexed-file",
            title="Indexed file",
            mime_type="application/pdf",
        )
        UserDocumentVisibility.objects.create(
            authorization=authorization,
            source_document=document,
            connection_generation=self.connection.authorization_generation,
            authorization_generation=authorization.authorization_generation,
            state=UserDocumentVisibility.State.VERIFIED_VISIBLE,
        )

        with (
            patch.object(
                user_oauth,
                "_revoke_refresh_credential",
                side_effect=OSError("provider unavailable"),
            ) as revoke,
            patch.object(user_oauth, "delete_oauth_viewer_relationships") as delete,
        ):
            user_oauth.disconnect_authorization(user_email=self.user_email)

        authorization.refresh_from_db()
        self.assertEqual(authorization.status, GoogleDriveAuthorization.Status.DISCONNECTED)
        self.assertEqual(bytes(authorization.encrypted_refresh_credential), b"")
        self.assertEqual(authorization.encryption_key_version, "")
        self.assertFalse(UserDocumentVisibility.objects.exists())
        delete.assert_called_once_with(
            connection=self.connection,
            user_email=self.user_email,
        )
        revoke.assert_called_once_with("test-refresh-credential")

    def test_disconnect_logs_warning_when_spicedb_cleanup_fails(self):
        session, state, _ = self.begin()
        authorization = self.complete(session=session, state=state)

        with (
            patch.object(user_oauth, "_revoke_refresh_credential"),
            patch.object(
                user_oauth,
                "delete_oauth_viewer_relationships",
                side_effect=RuntimeError("spicedb unavailable"),
            ),
            self.assertLogs("integrations.drive.user_oauth", level="WARNING") as logs,
        ):
            user_oauth.disconnect_authorization(user_email=self.user_email)

        authorization.refresh_from_db()
        self.assertEqual(authorization.status, GoogleDriveAuthorization.Status.DISCONNECTED)
        self.assertEqual(bytes(authorization.encrypted_refresh_credential), b"")
        self.assertEqual(len(logs.output), 1)
        self.assertIn("RuntimeError", logs.output[0])
        self.assertNotIn(self.user_email, logs.output[0])
        self.assertNotIn("spicedb unavailable", logs.output[0])

    def test_status_contains_no_identity_scope_or_credential_material(self):
        session, state, _ = self.begin()
        self.complete(session=session, state=state)

        payload = user_oauth.authorization_status(user_email=self.user_email).as_payload()

        self.assertEqual(
            payload,
            {
                "configured": True,
                "connected": True,
                "status": "active",
                "state": "temporarily_unavailable",
            },
        )
        serialized = json.dumps(payload)
        self.assertNotIn(self.user_email, serialized)
        self.assertNotIn("scope", serialized)
        self.assertNotIn("credential", serialized)
