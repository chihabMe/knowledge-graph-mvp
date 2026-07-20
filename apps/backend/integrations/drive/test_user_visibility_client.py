import json
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet
from django.test import TestCase, override_settings
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError
from httplib2 import Response

from integrations.drive.token_encryption import encrypt_refresh_credential
from integrations.drive.user_oauth import REQUIRED_SCOPES
from integrations.drive.user_visibility_client import (
    IndexedDriveVisibilityClient,
    IndexedVisibilityResult,
    UserVisibilityCheckError,
    _build_user_drive_service,
)
from integrations.models import (
    DriveConnection,
    GoogleDriveAuthorization,
    SourceDocument,
    UserDocumentVisibility,
)


def http_error(status, *, reason=""):
    content = json.dumps({"error": {"errors": [{"reason": reason}]}}).encode() if reason else b"{}"
    return HttpError(Response({"status": str(status)}), content)


class FakeRequest:
    def __init__(self, outcome):
        self.outcome = outcome
        self.execute_kwargs = None

    def execute(self, **kwargs):
        self.execute_kwargs = kwargs
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


class FakeFiles:
    def __init__(self, outcomes):
        self.outcomes = {key: list(value) for key, value in outcomes.items()}
        self.get_calls = []

    def get(self, **kwargs):
        self.get_calls.append(kwargs)
        return FakeRequest(self.outcomes[kwargs["fileId"]].pop(0))

    def list(self, **kwargs):
        raise AssertionError("files.list must never be called")

    def export(self, **kwargs):
        raise AssertionError("files.export must never be called")

    def get_media(self, **kwargs):
        raise AssertionError("files.get_media must never be called")


class FakeService:
    def __init__(self, outcomes):
        self.files_resource = FakeFiles(outcomes)

    def files(self):
        return self.files_resource


@override_settings(
    GOOGLE_PERMISSION_AUTHORITY=DriveConnection.PermissionAuthority.PER_USER_OAUTH,
    GOOGLE_USER_OAUTH_ALLOWED_DOMAIN="example.com",
    GOOGLE_USER_VISIBILITY_MAX_DOCUMENTS=10,
)
class IndexedDriveVisibilityClientTests(TestCase):
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
            root_folder_id="selected-root",
            permission_authority=DriveConnection.PermissionAuthority.PER_USER_OAUTH,
        )
        encrypted = encrypt_refresh_credential("test-refresh-credential")
        self.authorization = GoogleDriveAuthorization.objects.create(
            connection=self.connection,
            google_issuer="https://accounts.google.com",
            google_subject="subject-1",
            normalized_email="pilot@example.com",
            workspace_domain="example.com",
            encrypted_refresh_credential=encrypted.ciphertext,
            encryption_key_version=encrypted.key_version,
            granted_scopes=sorted(REQUIRED_SCOPES),
            connection_generation=self.connection.authorization_generation,
            status=GoogleDriveAuthorization.Status.ACTIVE,
        )

    def tearDown(self):
        self.settings_override.disable()
        self.temporary_directory.cleanup()

    def document(self, drive_file_id, *, active=True):
        return SourceDocument.objects.create(
            connection=self.connection,
            drive_file_id=drive_file_id,
            title="Indexed",
            mime_type="text/plain",
            active_in_scope=active,
        )

    def test_checks_only_active_database_rows_with_files_get_and_shared_drive_support(self):
        visible = self.document("visible-id")
        self.document("inactive-id", active=False)
        service = FakeService({"visible-id": [{"id": "visible-id", "trashed": False}]})

        batch = IndexedDriveVisibilityClient(service=service).check_authorization(
            self.authorization.pk
        )

        self.assertEqual(
            batch.results,
            (
                IndexedVisibilityResult(
                    source_document_id=visible.pk,
                    state=UserDocumentVisibility.State.VERIFIED_VISIBLE,
                    reason_code="",
                ),
            ),
        )
        self.assertEqual(
            service.files_resource.get_calls,
            [
                {
                    "fileId": "visible-id",
                    "supportsAllDrives": True,
                    "fields": "id,trashed",
                }
            ],
        )

    def test_denied_unknown_and_visible_results_are_fail_closed(self):
        documents = {
            key: self.document(key)
            for key in ("visible", "trashed", "missing", "malformed", "failed")
        }
        service = FakeService(
            {
                "visible": [{"id": "visible", "trashed": False}],
                "trashed": [{"id": "trashed", "trashed": True}],
                "missing": [http_error(404)],
                "malformed": [{"id": "different", "trashed": False}],
                "failed": [OSError(), OSError(), OSError()],
            }
        )
        sleep = Mock()

        batch = IndexedDriveVisibilityClient(service=service, sleep=sleep).check_authorization(
            self.authorization.pk
        )

        by_id = {result.source_document_id: result for result in batch.results}
        self.assertEqual(
            by_id[documents["visible"].pk].state,
            UserDocumentVisibility.State.VERIFIED_VISIBLE,
        )
        self.assertEqual(by_id[documents["trashed"].pk].state, UserDocumentVisibility.State.DENIED)
        self.assertEqual(by_id[documents["missing"].pk].state, UserDocumentVisibility.State.DENIED)
        self.assertEqual(
            by_id[documents["malformed"].pk].state, UserDocumentVisibility.State.UNKNOWN
        )
        self.assertEqual(by_id[documents["failed"].pk].state, UserDocumentVisibility.State.UNKNOWN)
        self.assertEqual(sleep.call_count, 2)

    def test_rate_limits_retry_with_bounded_backoff_then_deny_unknown(self):
        self.document("quota")
        service = FakeService(
            {
                "quota": [
                    http_error(403, reason="userRateLimitExceeded"),
                    http_error(429),
                    http_error(503),
                ]
            }
        )
        sleep = Mock()

        batch = IndexedDriveVisibilityClient(service=service, sleep=sleep).check_authorization(
            self.authorization.pk
        )

        self.assertEqual(batch.results[0].state, UserDocumentVisibility.State.UNKNOWN)
        self.assertEqual(batch.results[0].reason_code, "transient_failure")
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.25, 0.5])
        self.assertEqual(len(service.files_resource.get_calls), 3)

    @override_settings(GOOGLE_USER_VISIBILITY_MAX_DOCUMENTS=1)
    def test_document_cap_is_enforced_before_any_remote_call(self):
        self.document("one")
        self.document("two")
        service = FakeService({})

        with self.assertRaisesRegex(UserVisibilityCheckError, "document_cap_exceeded"):
            IndexedDriveVisibilityClient(service=service).check_authorization(self.authorization.pk)

        self.assertEqual(service.files_resource.get_calls, [])

    def test_stale_generation_status_or_scope_is_denied_before_remote_call(self):
        self.document("one")
        cases = (
            {"status": GoogleDriveAuthorization.Status.DISCONNECTED},
            {"granted_scopes": []},
            {"connection_generation": uuid.uuid4()},
        )
        for update in cases:
            with self.subTest(update=update):
                GoogleDriveAuthorization.objects.filter(pk=self.authorization.pk).update(**update)
                service = FakeService({})
                with self.assertRaisesRegex(UserVisibilityCheckError, "authorization_unavailable"):
                    IndexedDriveVisibilityClient(service=service).check_authorization(
                        self.authorization.pk
                    )
                self.assertEqual(service.files_resource.get_calls, [])
                GoogleDriveAuthorization.objects.filter(pk=self.authorization.pk).update(
                    status=GoogleDriveAuthorization.Status.ACTIVE,
                    granted_scopes=sorted(REQUIRED_SCOPES),
                    connection_generation=self.connection.authorization_generation,
                )

    def test_real_service_builder_uses_decrypted_user_refresh_credential_only_in_memory(self):
        client_file = Path(self.temporary_directory.name) / "client.json"
        client_file.write_text(
            json.dumps(
                {
                    "web": {
                        "client_id": "test-client.apps.googleusercontent.com",
                        "client_secret": "test-client-secret",
                    }
                }
            ),
            encoding="utf-8",
        )
        credentials = Mock()
        with (
            override_settings(
                GOOGLE_USER_OAUTH_CLIENT_ID="test-client.apps.googleusercontent.com",
                GOOGLE_USER_OAUTH_CLIENT_SECRET_FILE=str(client_file),
            ),
            patch(
                "integrations.drive.user_visibility_client.Credentials",
                return_value=credentials,
            ) as credential_class,
            patch(
                "integrations.drive.user_visibility_client.build", return_value="service"
            ) as build,
        ):
            service = _build_user_drive_service(self.authorization)

        self.assertEqual(service, "service")
        self.assertEqual(
            credential_class.call_args.kwargs["refresh_token"], "test-refresh-credential"
        )
        self.assertEqual(credential_class.call_args.kwargs["scopes"], sorted(REQUIRED_SCOPES))
        credentials.refresh.assert_called_once()
        build.assert_called_once_with("drive", "v3", credentials=credentials, cache_discovery=False)

    def test_only_explicit_invalid_grant_is_classified_as_terminal(self):
        client_file = Path(self.temporary_directory.name) / "client.json"
        client_file.write_text(
            json.dumps(
                {
                    "web": {
                        "client_id": "test-client.apps.googleusercontent.com",
                        "client_secret": "test-client-secret",
                    }
                }
            ),
            encoding="utf-8",
        )
        with override_settings(
            GOOGLE_USER_OAUTH_CLIENT_ID="test-client.apps.googleusercontent.com",
            GOOGLE_USER_OAUTH_CLIENT_SECRET_FILE=str(client_file),
        ):
            for provider_error, expected_code in (
                ("invalid_grant", "credential_invalid_grant"),
                ("temporarily_unavailable", "credential_refresh_failed"),
            ):
                with self.subTest(provider_error=provider_error):
                    credentials = Mock()
                    credentials.refresh.side_effect = RefreshError(
                        "controlled refresh error",
                        {"error": provider_error},
                    )
                    with patch(
                        "integrations.drive.user_visibility_client.Credentials",
                        return_value=credentials,
                    ):
                        with self.assertRaisesRegex(UserVisibilityCheckError, expected_code):
                            _build_user_drive_service(self.authorization)
