from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from integrations.drive.client import DriveFileMetadata
from integrations.drive.permissions import source_permissions_version
from integrations.drive.sync import sync_drive_metadata
from integrations.models import (
    DriveConnection,
    DrivePermissionSnapshot,
    DriveSyncRun,
    SourceDocument,
)


class FakeDriveMetadataClient:
    def __init__(self, files):
        self.files = files
        self.connections = []

    def list_files(self, connection):
        self.connections.append(connection)
        return self.files


class DriveIngestionModelTests(TestCase):
    def test_source_documents_default_to_not_retrieval_eligible(self):
        connection = DriveConnection.objects.create(
            workspace_domain="example.com",
            root_folder_id="folder-123",
        )

        document = SourceDocument.objects.create(
            connection=connection,
            drive_file_id="drive-file-1",
            title="Pilot Notes",
            mime_type="application/vnd.google-apps.document",
        )

        self.assertFalse(document.retrieval_eligible)
        self.assertEqual(document.exclusion_reason, "")

    def test_sync_run_snapshots_server_configured_scope_and_actor(self):
        user = get_user_model().objects.create_user(
            username="admin",
            email="admin@example.com",
        )
        connection = DriveConnection.objects.create(
            workspace_domain="example.com",
            scope_type=DriveConnection.ScopeType.SHARED_DRIVE,
            shared_drive_id="shared-drive-123",
        )

        run = DriveSyncRun.create_for_connection(connection, triggered_by=user)

        self.assertEqual(run.scope_type, DriveConnection.ScopeType.SHARED_DRIVE)
        self.assertEqual(run.shared_drive_id, "shared-drive-123")
        self.assertEqual(run.root_folder_id, "")
        self.assertEqual(run.actor_email, "admin@example.com")


class SourcePermissionsVersionTests(TestCase):
    def test_permissions_version_is_stable_for_order_and_volatile_fields(self):
        permissions = [
            {
                "id": "perm-2",
                "type": "user",
                "role": "reader",
                "emailAddress": "b@example.com",
                "fetched_at": "2026-07-07T01:00:00Z",
            },
            {
                "id": "perm-1",
                "type": "user",
                "role": "writer",
                "emailAddress": "a@example.com",
                "fetched_at": "2026-07-07T01:00:00Z",
            },
        ]
        same_permissions_different_order = [
            {**permissions[1], "fetched_at": "2026-07-07T02:00:00Z"},
            {**permissions[0], "fetched_at": "2026-07-07T02:00:00Z"},
        ]

        self.assertEqual(
            source_permissions_version(permissions),
            source_permissions_version(same_permissions_different_order),
        )

    def test_permissions_version_changes_when_access_changes(self):
        reader_permissions = [
            {
                "id": "perm-1",
                "type": "user",
                "role": "reader",
                "emailAddress": "a@example.com",
            }
        ]
        writer_permissions = [{**reader_permissions[0], "role": "writer"}]

        self.assertNotEqual(
            source_permissions_version(reader_permissions),
            source_permissions_version(writer_permissions),
        )

    def test_permissions_version_accepts_missing_permissions(self):
        self.assertEqual(
            source_permissions_version(None),
            source_permissions_version([]),
        )


class DriveMetadataSyncTests(TestCase):
    @override_settings(
        GOOGLE_SERVICE_ACCOUNT_FILE="/run/secrets/google-service-account.json",
        GOOGLE_DRIVE_ROOT_ID="folder-123",
    )
    def test_sync_stores_metadata_and_permission_snapshot_without_real_api_call(self):
        connection = DriveConnection.objects.create(
            workspace_domain="example.com",
            root_folder_id="folder-123",
        )
        file_metadata = DriveFileMetadata(
            drive_file_id="drive-file-1",
            title="Pilot Notes",
            mime_type="application/vnd.google-apps.document",
            drive_url="https://drive.google.com/file/d/drive-file-1/view",
            created_time=datetime(2026, 7, 1, tzinfo=UTC),
            modified_time=datetime(2026, 7, 2, tzinfo=UTC),
            folder_path="/Pilot",
            parent_folder_ids=["folder-123"],
            owner_email="owner@example.com",
            creator_email="creator@example.com",
            permissions=[
                {
                    "id": "perm-1",
                    "type": "user",
                    "role": "reader",
                    "emailAddress": "reader@example.com",
                }
            ],
        )
        client = FakeDriveMetadataClient([file_metadata])

        run = sync_drive_metadata(connection=connection, client=client)

        self.assertEqual(run.status, DriveSyncRun.Status.SUCCEEDED)
        self.assertEqual(client.connections, [connection])
        document = SourceDocument.objects.get(connection=connection, drive_file_id="drive-file-1")
        self.assertEqual(document.title, "Pilot Notes")
        self.assertEqual(document.parent_folder_ids, ["folder-123"])
        self.assertFalse(document.retrieval_eligible)
        self.assertEqual(document.exclusion_reason, "")
        snapshot = DrivePermissionSnapshot.objects.get(source_document=document)
        self.assertEqual(snapshot.raw_permissions, file_metadata.permissions)
        self.assertFalse(snapshot.has_public_link)
        self.assertFalse(snapshot.has_domain_visibility)

    def test_sync_excludes_public_link_files_from_retrieval(self):
        connection = DriveConnection.objects.create(
            workspace_domain="example.com",
            root_folder_id="folder-123",
        )
        client = FakeDriveMetadataClient(
            [
                DriveFileMetadata(
                    drive_file_id="drive-file-2",
                    title="Public Notes",
                    mime_type="application/pdf",
                    permissions=[{"id": "anyone", "type": "anyone", "role": "reader"}],
                )
            ]
        )

        run = sync_drive_metadata(connection=connection, client=client)

        document = SourceDocument.objects.get(drive_file_id="drive-file-2")
        self.assertFalse(document.retrieval_eligible)
        self.assertEqual(
            document.exclusion_reason,
            SourceDocument.ExclusionReason.PUBLIC_LINK_NOT_SUPPORTED,
        )
        self.assertEqual(run.total_files, 1)
        self.assertEqual(run.stored_files, 0)
        self.assertEqual(run.skipped_files, 1)
