import datetime

from django.test import TestCase, override_settings
from django.utils import timezone

from integrations.drive.onboarding import (
    NOT_CONNECTED,
    READY,
    REAUTHORIZATION_REQUIRED,
    SYNCING,
    TEMPORARILY_UNAVAILABLE,
    connection_state,
    session_onboarding_url,
    webui_return_url,
)
from integrations.drive.user_oauth import REQUIRED_SCOPES
from integrations.models import DriveConnection, GoogleDriveAuthorization, UserVisibilitySyncRun


@override_settings(
    GOOGLE_PERMISSION_AUTHORITY=DriveConnection.PermissionAuthority.PER_USER_OAUTH,
    GOOGLE_USER_OAUTH_ALLOWED_DOMAIN="example.com",
    GOOGLE_USER_VISIBILITY_MAX_AGE_SECONDS=1800,
    GOOGLE_SESSION_OAUTH_REDIRECT_URI=("https://api.example.com/api/session/google/callback"),
    WEBUI_URL="https://ai.example.com/",
)
class DriveOnboardingStateTests(TestCase):
    def setUp(self):
        self.connection = DriveConnection.objects.create(
            workspace_domain="example.com",
            root_folder_id="selected-root",
            permission_authority=DriveConnection.PermissionAuthority.PER_USER_OAUTH,
        )

    def authorization(self, **overrides):
        values = {
            "connection": self.connection,
            "google_issuer": "https://accounts.google.com",
            "google_subject": "subject-1",
            "normalized_email": "pilot@example.com",
            "workspace_domain": "example.com",
            "encrypted_refresh_credential": b"encrypted-test-value",
            "encryption_key_version": "test-v1",
            "granted_scopes": sorted(REQUIRED_SCOPES),
            "connection_generation": self.connection.authorization_generation,
            "status": GoogleDriveAuthorization.Status.ACTIVE,
        }
        values.update(overrides)
        return GoogleDriveAuthorization.objects.create(**values)

    def test_missing_or_disconnected_authorization_needs_connection(self):
        self.assertEqual(connection_state(user_email="pilot@example.com").state, NOT_CONNECTED)
        self.authorization(status=GoogleDriveAuthorization.Status.DISCONNECTED)
        self.assertEqual(connection_state(user_email="pilot@example.com").state, NOT_CONNECTED)

    def test_active_authorization_is_syncing_while_current_run_is_pending(self):
        authorization = self.authorization()
        UserVisibilitySyncRun.create_for_authorization(authorization)

        state = connection_state(user_email="pilot@example.com")

        self.assertTrue(state.connected)
        self.assertEqual(state.state, SYNCING)

    def test_fresh_success_is_ready_even_when_no_documents_are_visible(self):
        authorization = self.authorization(last_successful_visibility_sync_at=timezone.now())
        run = UserVisibilitySyncRun.create_for_authorization(authorization)
        run.status = UserVisibilitySyncRun.Status.SUCCEEDED
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "finished_at"])

        self.assertEqual(connection_state(user_email="pilot@example.com").state, READY)

    def test_stale_or_failed_current_evidence_is_temporarily_unavailable(self):
        authorization = self.authorization(
            last_successful_visibility_sync_at=timezone.now() - datetime.timedelta(hours=1)
        )
        self.assertEqual(
            connection_state(user_email="pilot@example.com").state,
            TEMPORARILY_UNAVAILABLE,
        )

        authorization.last_successful_visibility_sync_at = timezone.now()
        authorization.save(update_fields=["last_successful_visibility_sync_at"])
        run = UserVisibilitySyncRun.create_for_authorization(authorization)
        run.status = UserVisibilitySyncRun.Status.FAILED
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "finished_at"])
        self.assertEqual(
            connection_state(user_email="pilot@example.com").state,
            TEMPORARILY_UNAVAILABLE,
        )

    def test_terminal_authorization_requires_reauthorization(self):
        self.authorization(status=GoogleDriveAuthorization.Status.REFRESH_FAILED)

        state = connection_state(user_email="pilot@example.com")

        self.assertFalse(state.connected)
        self.assertEqual(state.state, REAUTHORIZATION_REQUIRED)

    def test_browser_urls_use_only_validated_public_origins(self):
        self.assertEqual(
            session_onboarding_url(),
            "https://api.example.com/api/session/google/start",
        )
        self.assertEqual(webui_return_url(), "https://ai.example.com/")
