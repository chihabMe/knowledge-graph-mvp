import uuid
from unittest.mock import Mock

from django.test import TestCase, override_settings
from django.utils import timezone

from authorization.client import PermissionTuple
from integrations.models import (
    DriveConnection,
    GoogleDriveAuthorization,
    SourceDocument,
    UserDocumentVisibility,
)
from integrations.permission_authority import (
    PermissionAuthoritySwitchError,
    switch_permission_authority,
)


class FakeSpiceDB:
    def __init__(self, tuples=(), *, fail=False):
        self.tuples = set(tuples)
        self.fail = fail
        self.reads = []

    def read_managed_tuples(self, prefix, *, revision=""):
        self.reads.append((prefix, revision))
        if self.fail:
            raise OSError("unavailable")
        return set(self.tuples)

    def write_updates(self, *, touches, deletes):
        if self.fail:
            raise OSError("unavailable")
        self.tuples.difference_update(deletes)
        self.tuples.update(touches)
        return "revision-1"


@override_settings(GOOGLE_PERMISSION_AUTHORITY="per_user_oauth")
class PermissionAuthoritySwitchTests(TestCase):
    def setUp(self):
        self.connection = DriveConnection.objects.create(
            workspace_domain="example.com",
            root_folder_id="root-1",
            permission_authority=DriveConnection.PermissionAuthority.DELEGATED_ACL,
        )
        self.document = SourceDocument.objects.create(
            connection=self.connection,
            drive_file_id="file-1",
            title="Notes",
            mime_type="text/plain",
            content_hash="content-hash",
            graph_extraction_status=SourceDocument.GraphExtractionStatus.SUCCEEDED,
            retrieval_eligible=True,
            source_permissions_version="legacy-version",
            spicedb_permissions_version="legacy-version",
            spicedb_revision="legacy-revision",
            spicedb_verified_at=timezone.now(),
        )
        self.authorization = GoogleDriveAuthorization.objects.create(
            connection=self.connection,
            google_issuer="https://accounts.google.com",
            google_subject="subject-1",
            normalized_email="pilot@example.com",
            workspace_domain="example.com",
            connection_generation=self.connection.authorization_generation,
            granted_scopes=["openid"],
            status=GoogleDriveAuthorization.Status.ACTIVE,
            encrypted_refresh_credential=b"ciphertext",
            encryption_key_version="v1",
        )
        UserDocumentVisibility.objects.create(
            authorization=self.authorization,
            source_document=self.document,
            connection_generation=self.connection.authorization_generation,
            authorization_generation=self.authorization.authorization_generation,
            state=UserDocumentVisibility.State.VERIFIED_VISIBLE,
            checked_at=timezone.now(),
        )
        self.relationship = PermissionTuple(
            resource_type="kgm/document",
            resource_id=f"c{self.connection.pk}_d{self.document.pk}",
            relation="reader",
            subject_type="kgm/user",
            subject_id="opaque-user",
        )

    def test_switch_denies_cleans_and_activates_target_authority(self):
        previous_generation = self.connection.authorization_generation
        spicedb = FakeSpiceDB([self.relationship])

        result = switch_permission_authority(
            connection_id=self.connection.pk,
            target_authority=DriveConnection.PermissionAuthority.PER_USER_OAUTH,
            spicedb=spicedb,
        )

        self.connection.refresh_from_db()
        self.document.refresh_from_db()
        self.authorization.refresh_from_db()
        self.assertTrue(result.changed)
        self.assertEqual(result.relationships_deleted, 1)
        self.assertEqual(
            self.connection.permission_authority,
            DriveConnection.PermissionAuthority.PER_USER_OAUTH,
        )
        self.assertTrue(self.connection.enabled)
        self.assertNotEqual(self.connection.authorization_generation, previous_generation)
        self.assertFalse(self.document.retrieval_eligible)
        self.assertEqual(self.document.source_permissions_version, "")
        self.assertEqual(
            self.document.graph_extraction_status,
            SourceDocument.GraphExtractionStatus.PENDING,
        )
        self.assertIsNone(self.document.graph_extraction_queued_at)
        self.assertEqual(self.authorization.status, GoogleDriveAuthorization.Status.DISCONNECTED)
        self.assertEqual(bytes(self.authorization.encrypted_refresh_credential), b"")
        self.assertFalse(UserDocumentVisibility.objects.exists())
        self.assertEqual(spicedb.tuples, set())
        self.assertEqual(spicedb.reads[-1][1], "revision-1")

    def test_spicedb_failure_leaves_connection_disabled_and_denied(self):
        with self.assertRaisesRegex(PermissionAuthoritySwitchError, "spicedb_cleanup_failed"):
            switch_permission_authority(
                connection_id=self.connection.pk,
                target_authority=DriveConnection.PermissionAuthority.PER_USER_OAUTH,
                spicedb=FakeSpiceDB(fail=True),
            )

        self.connection.refresh_from_db()
        self.document.refresh_from_db()
        self.assertFalse(self.connection.enabled)
        self.assertEqual(
            self.connection.permission_authority,
            DriveConnection.PermissionAuthority.DELEGATED_ACL,
        )
        self.assertFalse(self.document.retrieval_eligible)

    @override_settings(GOOGLE_PERMISSION_AUTHORITY="delegated_acl")
    def test_target_must_match_the_deployment_setting(self):
        generation = self.connection.authorization_generation

        with self.assertRaisesRegex(
            PermissionAuthoritySwitchError, "configured_authority_mismatch"
        ):
            switch_permission_authority(
                connection_id=self.connection.pk,
                target_authority=DriveConnection.PermissionAuthority.PER_USER_OAUTH,
                spicedb=FakeSpiceDB(),
            )

        self.connection.refresh_from_db()
        self.assertTrue(self.connection.enabled)
        self.assertEqual(self.connection.authorization_generation, generation)

    def test_already_active_target_is_idempotent(self):
        self.connection.permission_authority = DriveConnection.PermissionAuthority.PER_USER_OAUTH
        self.connection.save(update_fields=["permission_authority", "updated_at"])
        spicedb = Mock()

        result = switch_permission_authority(
            connection_id=self.connection.pk,
            target_authority=DriveConnection.PermissionAuthority.PER_USER_OAUTH,
            spicedb=spicedb,
        )

        self.assertFalse(result.changed)
        spicedb.read_managed_tuples.assert_not_called()

    def test_connection_change_during_cleanup_stays_disabled(self):
        class ConcurrentChangeSpiceDB(FakeSpiceDB):
            def write_updates(inner_self, *, touches, deletes):
                DriveConnection.objects.filter(pk=self.connection.pk).update(
                    authorization_generation=uuid.uuid4()
                )
                return super().write_updates(touches=touches, deletes=deletes)

        with self.assertRaisesRegex(
            PermissionAuthoritySwitchError, "connection_changed_during_cutover"
        ):
            switch_permission_authority(
                connection_id=self.connection.pk,
                target_authority=DriveConnection.PermissionAuthority.PER_USER_OAUTH,
                spicedb=ConcurrentChangeSpiceDB([self.relationship]),
            )

        self.connection.refresh_from_db()
        self.assertFalse(self.connection.enabled)
        self.assertEqual(
            self.connection.permission_authority,
            DriveConnection.PermissionAuthority.DELEGATED_ACL,
        )
