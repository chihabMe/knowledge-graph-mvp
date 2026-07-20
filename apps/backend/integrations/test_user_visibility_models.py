import uuid

from django.forms.models import model_to_dict
from django.test import TestCase

from integrations.models import (
    DriveConnection,
    GoogleDriveAuthorization,
    SourceDocument,
    UserDocumentVisibility,
    UserVisibilitySyncRun,
)


class UserVisibilityModelTests(TestCase):
    def setUp(self):
        self.connection = DriveConnection.objects.create(
            workspace_domain="example.com",
            root_folder_id="root-opaque-id",
        )
        self.document = SourceDocument.objects.create(
            connection=self.connection,
            drive_file_id="drive-file-opaque-id",
            title="Indexed document",
            mime_type="application/pdf",
        )
        self.authorization = GoogleDriveAuthorization.objects.create(
            connection=self.connection,
            google_issuer="https://accounts.google.com",
            google_subject="google-subject-opaque-id",
            normalized_email="pilot.user@example.com",
            workspace_domain="example.com",
            encrypted_refresh_credential=b"ciphertext-not-a-token",
            encryption_key_version="v1",
            granted_scopes=[
                "openid",
                "email",
                "https://www.googleapis.com/auth/drive.metadata.readonly",
            ],
            connection_generation=self.connection.authorization_generation,
            status=GoogleDriveAuthorization.Status.ACTIVE,
        )

    def test_new_connections_default_to_per_user_authority_with_an_opaque_generation(self):
        self.assertEqual(
            self.connection.permission_authority,
            DriveConnection.PermissionAuthority.PER_USER_OAUTH,
        )
        self.assertIsInstance(self.connection.authorization_generation, uuid.UUID)

    def test_authorization_credential_is_noneditable_and_safe_to_represent(self):
        representation = str(self.authorization)
        serialized = model_to_dict(self.authorization)

        self.assertNotIn(self.authorization.normalized_email, representation)
        self.assertNotIn(self.authorization.google_subject, representation)
        self.assertNotIn("ciphertext", representation)
        self.assertNotIn("encrypted_refresh_credential", serialized)
        self.assertNotIn("encryption_key_version", serialized)

    def test_visibility_evidence_is_generation_scoped_and_safe_to_represent(self):
        evidence = UserDocumentVisibility.objects.create(
            authorization=self.authorization,
            source_document=self.document,
            connection_generation=self.connection.authorization_generation,
            authorization_generation=self.authorization.authorization_generation,
            state=UserDocumentVisibility.State.VERIFIED_VISIBLE,
            spicedb_revision="zedtoken-redacted-from-status-apis",
        )

        representation = str(evidence)
        self.assertNotIn(self.document.drive_file_id, representation)
        self.assertNotIn(self.authorization.normalized_email, representation)
        self.assertEqual(evidence.state, UserDocumentVisibility.State.VERIFIED_VISIBLE)

    def test_sync_run_keeps_only_controlled_counts_and_safe_representation(self):
        run = UserVisibilitySyncRun.objects.create(
            connection=self.connection,
            authorization=self.authorization,
            connection_generation=self.connection.authorization_generation,
            authorization_generation=self.authorization.authorization_generation,
            documents_considered=3,
            documents_verified_visible=1,
            documents_denied=1,
            documents_unknown=1,
            error_code="remote_uncertain",
        )

        representation = str(run)
        self.assertNotIn(self.authorization.normalized_email, representation)
        self.assertNotIn(self.document.drive_file_id, representation)
        self.assertEqual(run.documents_considered, 3)
