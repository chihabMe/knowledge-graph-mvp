import hashlib
from datetime import UTC, datetime, timedelta
from tempfile import NamedTemporaryFile
from unittest.mock import patch

from celery.exceptions import Retry
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from google.auth.exceptions import RefreshError
from neo4j.exceptions import ServiceUnavailable
from rest_framework.throttling import ScopedRateThrottle

from graph.pipeline import get_retryable_extraction_exceptions
from integrations.drive.client import (
    DriveFileMetadata,
    DrivePermissionAccessReport,
    DriveRootCandidate,
)
from integrations.drive.export import content_sha256, export_file_content
from integrations.drive.google_client import (
    GoogleDriveApiError,
    GoogleDriveMetadataClient,
    MissingServiceAccountKeyError,
    build_drive_service,
)
from integrations.drive.permissions import source_permissions_version
from integrations.drive.sync import sync_drive_metadata
from integrations.models import (
    DriveConnection,
    DrivePermissionSnapshot,
    DriveSyncRun,
    SourceDocument,
    SourceDocumentContent,
)
from integrations.tasks import (
    queue_document_extraction,
    run_drive_sync,
    sweep_stale_drive_sync_runs,
    sweep_stale_graph_extractions,
)


class FakeDriveMetadataClient:
    def __init__(self, files):
        self.files = files
        self.connections = []

    def list_files(self, connection):
        self.connections.append(connection)
        return self.files


class FakeDriveRootClient:
    def __init__(self, candidates=None, error=None):
        self.error = error
        self.candidates = candidates
        self.connections = []

    def list_root_candidates(self, connection):
        self.connections.append(connection)
        if self.error is not None:
            raise self.error
        return self.candidates

    def check_permission_access(self, connection):
        self.connections.append(connection)
        if self.error is not None:
            raise self.error
        return DrivePermissionAccessReport(
            sampled_files=3,
            readable_files=2,
            unreadable_files=1,
            checked_all_available_files=True,
        )


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
        self.assertEqual(
            document.graph_extraction_status,
            SourceDocument.GraphExtractionStatus.PENDING,
        )

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

    def test_sync_excludes_files_with_failed_permission_fetch_and_continues(self):
        connection = DriveConnection.objects.create(
            workspace_domain="example.com",
            root_folder_id="folder-123",
        )
        client = FakeDriveMetadataClient(
            [
                DriveFileMetadata(
                    drive_file_id="drive-file-3",
                    title="Restricted File",
                    mime_type="application/pdf",
                    permissions=[],
                    permissions_fetch_failed=True,
                ),
                DriveFileMetadata(
                    drive_file_id="drive-file-4",
                    title="Readable File",
                    mime_type="application/pdf",
                    permissions=[{"id": "perm-1", "type": "user", "role": "reader"}],
                ),
            ]
        )

        run = sync_drive_metadata(connection=connection, client=client)

        self.assertEqual(run.status, DriveSyncRun.Status.SUCCEEDED)
        restricted = SourceDocument.objects.get(drive_file_id="drive-file-3")
        self.assertFalse(restricted.retrieval_eligible)
        self.assertEqual(
            restricted.exclusion_reason,
            SourceDocument.ExclusionReason.PERMISSION_METADATA_INCOMPLETE,
        )
        readable = SourceDocument.objects.get(drive_file_id="drive-file-4")
        self.assertEqual(readable.exclusion_reason, "")
        self.assertEqual(run.total_files, 2)
        self.assertEqual(run.stored_files, 1)
        self.assertEqual(run.skipped_files, 1)


class FakeApiCall:
    def __init__(self, result):
        self._result = result

    def execute(self):
        if isinstance(self._result, BaseException):
            raise self._result
        return self._result


class FakeFilesResource:
    def __init__(self, service):
        self._service = service

    def list(self, *, q, pageToken=None, **_kwargs):
        if "sharedWithMe" in q:
            pages = self._service.shared_folder_pages
        else:
            folder_id = q.split("'")[1]
            if folder_id in self._service.children_errors:
                return FakeApiCall(self._service.children_errors[folder_id])
            pages = self._service.children_pages[folder_id]
        index = int(pageToken or 0)
        page = {"files": pages[index]}
        if index + 1 < len(pages):
            page["nextPageToken"] = str(index + 1)
        return FakeApiCall(page)

    def get(self, *, fileId, **_kwargs):
        return FakeApiCall({"id": fileId, "name": self._service.folder_names[fileId]})

    def export(self, *, fileId, mimeType):
        self._service.export_calls.append((fileId, mimeType))
        return FakeApiCall(self._service.export_data[fileId])

    def get_media(self, *, fileId, **_kwargs):
        self._service.media_calls.append(fileId)
        return FakeApiCall(self._service.media_data[fileId])


class FakePermissionsResource:
    def __init__(self, service):
        self._service = service

    def list(self, *, fileId, pageToken=None, **_kwargs):
        if fileId in self._service.permission_errors:
            return FakeApiCall(self._service.permission_errors[fileId])
        pages = self._service.permission_pages.get(fileId, [[]])
        index = int(pageToken or 0)
        page = {"permissions": pages[index]}
        if index + 1 < len(pages):
            page["nextPageToken"] = str(index + 1)
        return FakeApiCall(page)


class FakeDrivesResource:
    def __init__(self, service):
        self._service = service

    def get(self, *, driveId, **_kwargs):
        return FakeApiCall({"id": driveId, "name": self._service.drive_names[driveId]})

    def list(self, *, pageToken=None, **_kwargs):
        pages = self._service.shared_drive_pages
        index = int(pageToken or 0)
        page = {"drives": pages[index]}
        if index + 1 < len(pages):
            page["nextPageToken"] = str(index + 1)
        return FakeApiCall(page)


class FakeGoogleDriveService:
    """Offline stand-in for the Drive v3 discovery client.

    Children and permission listings are keyed by id and split into pages so
    pagination is exercised the same way the real API paginates.
    """

    def __init__(
        self,
        *,
        folder_names=None,
        children_pages=None,
        children_errors=None,
        permission_pages=None,
        permission_errors=None,
        drive_names=None,
        shared_folder_pages=None,
        shared_drive_pages=None,
        export_data=None,
        media_data=None,
    ):
        self.folder_names = folder_names or {}
        self.children_pages = children_pages or {}
        self.children_errors = children_errors or {}
        self.permission_pages = permission_pages or {}
        self.permission_errors = permission_errors or {}
        self.drive_names = drive_names or {}
        self.shared_folder_pages = shared_folder_pages or [[]]
        self.shared_drive_pages = shared_drive_pages or [[]]
        self.export_data = export_data or {}
        self.media_data = media_data or {}
        self.export_calls = []
        self.media_calls = []

    def files(self):
        return FakeFilesResource(self)

    def permissions(self):
        return FakePermissionsResource(self)

    def drives(self):
        return FakeDrivesResource(self)


def _folder_entry(folder_id, name):
    return {
        "id": folder_id,
        "name": name,
        "mimeType": "application/vnd.google-apps.folder",
    }


def _file_entry(file_id, name, *, parents, mime_type="application/pdf", **extra):
    return {"id": file_id, "name": name, "mimeType": mime_type, "parents": parents, **extra}


class GoogleDriveMetadataClientTests(TestCase):
    def _folder_connection(self):
        return DriveConnection.objects.create(
            workspace_domain="example.com",
            root_folder_id="folder-root",
        )

    def test_walks_nested_folders_and_builds_ancestry_paths(self):
        service = FakeGoogleDriveService(
            folder_names={"folder-root": "Pilot"},
            children_pages={
                "folder-root": [
                    [
                        _folder_entry("folder-sub", "Reports"),
                        _file_entry("file-1", "Overview.pdf", parents=["folder-root"]),
                    ]
                ],
                "folder-sub": [
                    [_file_entry("file-2", "Q2.pdf", parents=["folder-sub"])],
                ],
            },
        )

        files = GoogleDriveMetadataClient(service=service).list_files(self._folder_connection())

        by_id = {item.drive_file_id: item for item in files}
        self.assertEqual(set(by_id), {"file-1", "file-2"})
        self.assertEqual(by_id["file-1"].folder_path, "/Pilot")
        self.assertEqual(by_id["file-2"].folder_path, "/Pilot/Reports")
        self.assertEqual(by_id["file-1"].parent_folder_ids, ["folder-root"])
        self.assertEqual(by_id["file-2"].parent_folder_ids, ["folder-sub"])

    def test_permission_scan_reuses_the_walk_without_refetching_folders(self):
        # folder_names deliberately covers only the root: a files().get for
        # any other folder would KeyError, proving the scan builds folder
        # resources from the listing entries the walk already has.
        service = FakeGoogleDriveService(
            folder_names={"folder-root": "Pilot"},
            children_pages={
                "folder-root": [
                    [
                        {**_folder_entry("folder-sub", "Reports"), "parents": ["folder-root"]},
                        _file_entry(
                            "file-1", "Overview.pdf", parents=["folder-root", "folder-sub"]
                        ),
                    ]
                ],
                "folder-sub": [
                    [
                        _file_entry(
                            "file-1", "Overview.pdf", parents=["folder-root", "folder-sub"]
                        ),
                        _file_entry("file-2", "Q2.pdf", parents=["folder-sub"]),
                    ],
                ],
            },
            permission_pages={
                "file-2": [[{"id": "perm-1", "type": "user", "role": "reader"}]],
            },
        )

        resources = GoogleDriveMetadataClient(service=service).list_permission_resources(
            self._folder_connection()
        )

        self.assertEqual(
            {(resource.resource_type, resource.drive_id) for resource in resources},
            {
                ("folder", "folder-root"),
                ("folder", "folder-sub"),
                ("document", "file-1"),
                ("document", "file-2"),
            },
        )
        self.assertEqual(len(resources), 4)  # multi-parent file-1 emitted once
        by_id = {resource.drive_id: resource for resource in resources}
        self.assertEqual(by_id["folder-sub"].parent_folder_ids, ["folder-root"])
        self.assertEqual(
            [permission["id"] for permission in by_id["file-2"].permissions],
            ["perm-1"],
        )

    def test_paginates_file_listings_and_permission_listings(self):
        service = FakeGoogleDriveService(
            folder_names={"folder-root": "Pilot"},
            children_pages={
                "folder-root": [
                    [_file_entry("file-1", "A.pdf", parents=["folder-root"])],
                    [_file_entry("file-2", "B.pdf", parents=["folder-root"])],
                ],
            },
            permission_pages={
                "file-1": [
                    [{"id": "perm-1", "type": "user", "role": "reader"}],
                    [{"id": "perm-2", "type": "user", "role": "writer"}],
                ],
            },
        )

        files = GoogleDriveMetadataClient(service=service).list_files(self._folder_connection())

        by_id = {item.drive_file_id: item for item in files}
        self.assertEqual(set(by_id), {"file-1", "file-2"})
        self.assertEqual(
            [permission["id"] for permission in by_id["file-1"].permissions],
            ["perm-1", "perm-2"],
        )

    def test_permission_fetch_failure_is_isolated_to_that_file(self):
        # Reproduces a real Drive response: a file listable via files.list()
        # can still 403 on permissions.list() (e.g. folder-level sharing
        # without "manage permissions" rights) — this must not abort the
        # whole walk or lose the other file.
        service = FakeGoogleDriveService(
            folder_names={"folder-root": "Pilot"},
            children_pages={
                "folder-root": [
                    [
                        _file_entry("file-1", "Restricted.pdf", parents=["folder-root"]),
                        _file_entry("file-2", "Readable.pdf", parents=["folder-root"]),
                    ],
                ],
            },
            permission_pages={
                "file-2": [[{"id": "perm-1", "type": "user", "role": "reader"}]],
            },
            permission_errors={"file-1": TimeoutError("network timeout")},
        )

        files = GoogleDriveMetadataClient(service=service).list_files(self._folder_connection())

        by_id = {item.drive_file_id: item for item in files}
        self.assertEqual(set(by_id), {"file-1", "file-2"})
        self.assertTrue(by_id["file-1"].permissions_fetch_failed)
        self.assertEqual(by_id["file-1"].permissions, [])
        self.assertFalse(by_id["file-2"].permissions_fetch_failed)
        self.assertEqual(
            [permission["id"] for permission in by_id["file-2"].permissions],
            ["perm-1"],
        )

    def test_permission_access_check_counts_unreadable_acl_samples(self):
        service = FakeGoogleDriveService(
            folder_names={"folder-root": "Pilot"},
            children_pages={
                "folder-root": [
                    [
                        _file_entry("file-1", "Restricted.pdf", parents=["folder-root"]),
                        _file_entry("file-2", "Readable.pdf", parents=["folder-root"]),
                    ],
                ],
            },
            permission_pages={
                "file-2": [[{"id": "perm-1", "type": "user", "role": "reader"}]],
            },
            permission_errors={"file-1": TimeoutError("network timeout")},
        )

        report = GoogleDriveMetadataClient(service=service).check_permission_access(
            self._folder_connection()
        )

        self.assertEqual(report.sampled_files, 2)
        self.assertEqual(report.readable_files, 1)
        self.assertEqual(report.unreadable_files, 1)
        self.assertTrue(report.checked_all_available_files)

    def test_permission_access_check_stops_after_sample_limit(self):
        service = FakeGoogleDriveService(
            children_pages={
                "folder-root": [
                    [
                        _file_entry("file-1", "A.pdf", parents=["folder-root"]),
                        _file_entry("file-2", "B.pdf", parents=["folder-root"]),
                    ],
                ],
            },
            permission_pages={
                "file-1": [[{"id": "perm-1", "type": "user", "role": "reader"}]],
                "file-2": [[{"id": "perm-2", "type": "user", "role": "reader"}]],
            },
        )

        report = GoogleDriveMetadataClient(service=service).check_permission_access(
            self._folder_connection(),
            max_files=1,
        )

        self.assertEqual(report.sampled_files, 1)
        self.assertEqual(report.readable_files, 1)
        self.assertEqual(report.unreadable_files, 0)
        self.assertFalse(report.checked_all_available_files)

    def test_permission_access_check_degrades_folder_listing_errors(self):
        service = FakeGoogleDriveService(
            children_pages={
                "folder-root": [
                    [
                        _folder_entry("folder-sub", "Restricted Folder"),
                        _file_entry("file-1", "Readable.pdf", parents=["folder-root"]),
                    ],
                ],
            },
            children_errors={"folder-sub": TimeoutError("network timeout")},
            permission_pages={
                "file-1": [[{"id": "perm-1", "type": "user", "role": "reader"}]],
            },
        )

        report = GoogleDriveMetadataClient(service=service).check_permission_access(
            self._folder_connection()
        )

        self.assertEqual(report.sampled_files, 1)
        self.assertEqual(report.readable_files, 1)
        self.assertEqual(report.unreadable_files, 0)
        self.assertEqual(report.folder_listing_errors, 1)
        self.assertFalse(report.checked_all_available_files)

    def test_multi_parented_files_are_emitted_once(self):
        entry = _file_entry("file-1", "Shared.pdf", parents=["folder-root", "folder-sub"])
        service = FakeGoogleDriveService(
            folder_names={"folder-root": "Pilot"},
            children_pages={
                "folder-root": [[_folder_entry("folder-sub", "Reports"), entry]],
                "folder-sub": [[entry]],
            },
        )

        files = GoogleDriveMetadataClient(service=service).list_files(self._folder_connection())

        self.assertEqual([item.drive_file_id for item in files], ["file-1"])

    def test_shared_drive_scope_roots_the_walk_at_the_drive(self):
        connection = DriveConnection.objects.create(
            workspace_domain="example.com",
            scope_type=DriveConnection.ScopeType.SHARED_DRIVE,
            shared_drive_id="drive-9",
        )
        service = FakeGoogleDriveService(
            drive_names={"drive-9": "Client Space"},
            children_pages={
                "drive-9": [
                    [
                        _file_entry(
                            "file-1",
                            "Brief.pdf",
                            parents=["drive-9"],
                            driveId="drive-9",
                        )
                    ]
                ],
            },
        )

        files = GoogleDriveMetadataClient(service=service).list_files(connection)

        self.assertEqual(files[0].folder_path, "/Client Space")
        self.assertEqual(files[0].shared_drive_id, "drive-9")

    def test_maps_drive_fields_and_never_fabricates_creator(self):
        service = FakeGoogleDriveService(
            folder_names={"folder-root": "Pilot"},
            children_pages={
                "folder-root": [
                    [
                        _file_entry(
                            "file-1",
                            "Notes",
                            parents=["folder-root"],
                            mime_type="application/vnd.google-apps.document",
                            webViewLink="https://docs.google.com/document/d/file-1",
                            createdTime="2026-07-01T10:00:00Z",
                            modifiedTime="2026-07-02T11:30:00Z",
                            md5Checksum="abc123",
                            owners=[{"emailAddress": "owner@example.com"}],
                        )
                    ]
                ],
            },
        )

        files = GoogleDriveMetadataClient(service=service).list_files(self._folder_connection())

        metadata = files[0]
        self.assertEqual(metadata.title, "Notes")
        self.assertEqual(metadata.drive_url, "https://docs.google.com/document/d/file-1")
        self.assertEqual(metadata.created_time, datetime(2026, 7, 1, 10, 0, tzinfo=UTC))
        self.assertEqual(metadata.modified_time, datetime(2026, 7, 2, 11, 30, tzinfo=UTC))
        self.assertEqual(metadata.md5_checksum, "abc123")
        self.assertEqual(metadata.owner_email, "owner@example.com")
        # Drive v3 exposes no creator field; the client must leave it empty
        # rather than guessing from owners or modifying users.
        self.assertEqual(metadata.creator_email, "")

    def test_lists_visible_root_folders_and_shared_drives(self):
        service = FakeGoogleDriveService(
            shared_folder_pages=[
                [
                    {
                        "id": "folder-b",
                        "name": "Beta",
                        "webViewLink": "https://drive.google.com/folders/folder-b",
                    }
                ],
                [
                    {
                        "id": "folder-a",
                        "name": "Alpha",
                        "webViewLink": "https://drive.google.com/folders/folder-a",
                        "driveId": "drive-1",
                    }
                ],
            ],
            shared_drive_pages=[
                [{"id": "drive-2", "name": "Client Space"}],
            ],
        )

        candidates = GoogleDriveMetadataClient(service=service).list_root_candidates(
            self._folder_connection()
        )

        self.assertEqual(
            [(candidate.scope_type, candidate.root_id, candidate.name) for candidate in candidates],
            [
                (DriveConnection.ScopeType.FOLDER, "folder-a", "Alpha"),
                (DriveConnection.ScopeType.FOLDER, "folder-b", "Beta"),
                (DriveConnection.ScopeType.SHARED_DRIVE, "drive-2", "Client Space"),
            ],
        )
        self.assertEqual(candidates[0].drive_url, "https://drive.google.com/folders/folder-a")
        self.assertEqual(candidates[0].shared_drive_id, "drive-1")

    def test_root_candidates_are_deduplicated_by_scope_and_id(self):
        service = FakeGoogleDriveService(
            shared_folder_pages=[
                [
                    {
                        "id": "folder-a",
                        "name": "Alpha",
                        "webViewLink": "https://drive.google.com/folders/folder-a",
                    },
                    {
                        "id": "folder-a",
                        "name": "Alpha duplicate",
                        "webViewLink": "https://drive.google.com/folders/folder-a",
                    },
                ]
            ],
            shared_drive_pages=[
                [
                    {"id": "drive-1", "name": "Client Space"},
                    {"id": "drive-1", "name": "Client Space duplicate"},
                ]
            ],
        )

        candidates = GoogleDriveMetadataClient(service=service).list_root_candidates(
            self._folder_connection()
        )

        self.assertEqual(
            [(candidate.scope_type, candidate.root_id) for candidate in candidates],
            [
                (DriveConnection.ScopeType.FOLDER, "folder-a"),
                (DriveConnection.ScopeType.SHARED_DRIVE, "drive-1"),
            ],
        )

    def test_root_candidate_drive_failures_raise_controlled_error(self):
        class FailingFilesResource:
            def list(self, **_kwargs):
                return FakeApiCall(TimeoutError("network timeout"))

        class FailingRootService:
            def files(self):
                return FailingFilesResource()

        with self.assertRaises(GoogleDriveApiError):
            GoogleDriveMetadataClient(service=FailingRootService()).list_root_candidates(
                self._folder_connection()
            )

    def test_root_candidate_auth_failures_raise_controlled_error(self):
        class FailingFilesResource:
            def list(self, **_kwargs):
                return FakeApiCall(RefreshError("revoked service-account key"))

        class FailingRootService:
            def files(self):
                return FailingFilesResource()

        with self.assertRaises(GoogleDriveApiError):
            GoogleDriveMetadataClient(service=FailingRootService()).list_root_candidates(
                self._folder_connection()
            )


class BuildDriveServiceTests(SimpleTestCase):
    # connection=None works only because the key check runs before anything
    # touches the connection — these tests deliberately pin that ordering.
    @override_settings(GOOGLE_SERVICE_ACCOUNT_FILE="")
    def test_unconfigured_key_fails_with_a_named_error(self):
        with self.assertRaises(MissingServiceAccountKeyError):
            build_drive_service(connection=None)

    def test_empty_key_file_fails_with_a_named_error(self):
        # /dev/null is what the compose bootstrap default actually mounts.
        with override_settings(GOOGLE_SERVICE_ACCOUNT_FILE="/dev/null"):
            with self.assertRaises(MissingServiceAccountKeyError):
                build_drive_service(connection=None)

    def test_malformed_key_file_fails_with_a_named_error(self):
        with NamedTemporaryFile(mode="w", encoding="utf-8") as key_file:
            key_file.write("not json")
            key_file.flush()
            with override_settings(GOOGLE_SERVICE_ACCOUNT_FILE=key_file.name):
                with self.assertRaises(MissingServiceAccountKeyError):
                    build_drive_service(connection=None)

    @patch("googleapiclient.discovery.build")
    @patch("google.oauth2.service_account.Credentials.from_service_account_file")
    def test_blank_connection_subject_does_not_delegate_even_with_env_subject(
        self, mock_from_file, mock_build
    ):
        # The env var must not silently re-enable delegation once the connection
        # field has been cleared — otherwise the delegated-subject endpoint's
        # "cleared" response would lie about the effective auth identity.
        credentials = mock_from_file.return_value
        connection = DriveConnection(delegated_subject_email="")
        with NamedTemporaryFile(mode="w", encoding="utf-8") as key_file:
            key_file.write("{}")
            key_file.flush()
            with override_settings(
                GOOGLE_SERVICE_ACCOUNT_FILE=key_file.name,
                GOOGLE_DRIVE_DELEGATED_SUBJECT="env-admin@example.com",
            ):
                build_drive_service(connection)

        credentials.with_subject.assert_not_called()
        _, build_kwargs = mock_build.call_args
        self.assertIs(build_kwargs["credentials"], credentials)

    @patch("googleapiclient.discovery.build")
    @patch("google.oauth2.service_account.Credentials.from_service_account_file")
    def test_connection_subject_is_applied(self, mock_from_file, mock_build):
        credentials = mock_from_file.return_value
        connection = DriveConnection(delegated_subject_email="delegated@example.com")
        with NamedTemporaryFile(mode="w", encoding="utf-8") as key_file:
            key_file.write("{}")
            key_file.flush()
            with override_settings(
                GOOGLE_SERVICE_ACCOUNT_FILE=key_file.name,
                GOOGLE_DRIVE_DELEGATED_SUBJECT="env-admin@example.com",
            ):
                build_drive_service(connection)

        credentials.with_subject.assert_called_once_with("delegated@example.com")
        _, build_kwargs = mock_build.call_args
        self.assertIs(build_kwargs["credentials"], credentials.with_subject.return_value)


class ExportFileContentTests(SimpleTestCase):
    def test_google_doc_exports_to_plain_text(self):
        service = FakeGoogleDriveService(export_data={"doc-1": "hello world"})

        data, mime = export_file_content(
            service,
            drive_file_id="doc-1",
            mime_type="application/vnd.google-apps.document",
        )

        self.assertEqual(service.export_calls, [("doc-1", "text/plain")])
        self.assertEqual(data, b"hello world")
        self.assertEqual(mime, "text/plain")

    def test_google_sheet_exports_to_csv(self):
        service = FakeGoogleDriveService(export_data={"sheet-1": b"a,b\n1,2\n"})

        data, mime = export_file_content(
            service,
            drive_file_id="sheet-1",
            mime_type="application/vnd.google-apps.spreadsheet",
        )

        self.assertEqual(service.export_calls, [("sheet-1", "text/csv")])
        self.assertEqual(data, b"a,b\n1,2\n")
        self.assertEqual(mime, "text/csv")

    def test_uploaded_files_download_unchanged(self):
        service = FakeGoogleDriveService(media_data={"pdf-1": b"%PDF-1.7 fake"})

        data, mime = export_file_content(
            service,
            drive_file_id="pdf-1",
            mime_type="application/pdf",
        )

        self.assertEqual(service.media_calls, ["pdf-1"])
        self.assertEqual(service.export_calls, [])
        self.assertEqual(data, b"%PDF-1.7 fake")
        self.assertEqual(mime, "application/pdf")

    def test_unexpected_payload_types_raise_instead_of_storing_a_repr(self):
        service = FakeGoogleDriveService(media_data={"pdf-1": {"unexpected": "dict"}})

        with self.assertRaises(TypeError):
            export_file_content(service, drive_file_id="pdf-1", mime_type="application/pdf")

    def test_content_sha256_matches_hashlib(self):
        self.assertEqual(content_sha256(b"payload"), hashlib.sha256(b"payload").hexdigest())


class FakeContentExporter:
    def __init__(self, payload=b"exported-bytes", mime="text/plain", error=None):
        self.payload = payload
        self.mime = mime
        self.error = error
        self.calls = []

    def __call__(self, file_metadata):
        self.calls.append(file_metadata.drive_file_id)
        if self.error is not None:
            raise self.error
        return self.payload, self.mime


class DriveContentSyncTests(TestCase):
    def setUp(self):
        self.connection = DriveConnection.objects.create(
            workspace_domain="example.com",
            root_folder_id="folder-123",
        )

    def _doc_metadata(self, modified_time):
        return DriveFileMetadata(
            drive_file_id="drive-file-1",
            title="Pilot Notes",
            mime_type="application/vnd.google-apps.document",
            modified_time=modified_time,
            permissions=[
                {"id": "perm-1", "type": "user", "role": "reader", "emailAddress": "a@example.com"}
            ],
        )

    @staticmethod
    def _collect_queued(queued):
        return lambda document_id, content_hash: queued.append((document_id, content_hash))

    def test_sync_stores_content_and_queues_extraction(self):
        exporter = FakeContentExporter()
        queued = []
        client = FakeDriveMetadataClient([self._doc_metadata(datetime(2026, 7, 2, tzinfo=UTC))])

        sync_drive_metadata(
            connection=self.connection,
            client=client,
            content_exporter=exporter,
            queue_extraction=self._collect_queued(queued),
        )

        document = SourceDocument.objects.get(drive_file_id="drive-file-1")
        content = SourceDocumentContent.objects.get(source_document=document)
        self.assertEqual(bytes(content.content), b"exported-bytes")
        self.assertEqual(content.exported_mime_type, "text/plain")
        self.assertEqual(content.content_hash, hashlib.sha256(b"exported-bytes").hexdigest())
        self.assertEqual(document.content_hash, content.content_hash)
        self.assertEqual(queued, [(document.pk, content.content_hash)])
        self.assertEqual(
            document.graph_extraction_status,
            SourceDocument.GraphExtractionStatus.PENDING,
        )
        # Content storage alone must never make a document retrievable —
        # eligibility is granted later, once permissions are in SpiceDB.
        self.assertFalse(document.retrieval_eligible)

    def test_sync_skips_reexport_when_file_is_unchanged_and_extraction_succeeded(self):
        modified_time = datetime(2026, 7, 2, tzinfo=UTC)
        exporter = FakeContentExporter()
        queued = []
        client = FakeDriveMetadataClient([self._doc_metadata(modified_time)])

        sync_drive_metadata(
            connection=self.connection,
            client=client,
            content_exporter=exporter,
            queue_extraction=self._collect_queued(queued),
        )
        SourceDocument.objects.update(
            graph_extraction_status=SourceDocument.GraphExtractionStatus.SUCCEEDED
        )
        sync_drive_metadata(
            connection=self.connection,
            client=client,
            content_exporter=exporter,
            queue_extraction=self._collect_queued(queued),
        )

        self.assertEqual(exporter.calls, ["drive-file-1"])
        self.assertEqual(len(queued), 1)

    def test_sync_requeues_failed_extraction_without_reexporting_content(self):
        modified_time = datetime(2026, 7, 2, tzinfo=UTC)
        exporter = FakeContentExporter()
        queued = []
        client = FakeDriveMetadataClient([self._doc_metadata(modified_time)])

        sync_drive_metadata(
            connection=self.connection,
            client=client,
            content_exporter=exporter,
            queue_extraction=self._collect_queued(queued),
        )
        SourceDocument.objects.update(
            graph_extraction_status=SourceDocument.GraphExtractionStatus.FAILED
        )
        sync_drive_metadata(
            connection=self.connection,
            client=client,
            content_exporter=exporter,
            queue_extraction=self._collect_queued(queued),
        )

        self.assertEqual(exporter.calls, ["drive-file-1"])
        self.assertEqual(len(queued), 2)

    def test_sync_stops_requeueing_failed_extraction_after_the_attempts_budget(self):
        modified_time = datetime(2026, 7, 2, tzinfo=UTC)
        exporter = FakeContentExporter()
        queued = []
        client = FakeDriveMetadataClient([self._doc_metadata(modified_time)])

        sync_drive_metadata(
            connection=self.connection,
            client=client,
            content_exporter=exporter,
            queue_extraction=self._collect_queued(queued),
        )
        SourceDocument.objects.update(
            graph_extraction_status=SourceDocument.GraphExtractionStatus.FAILED,
            graph_extraction_attempts=settings.GRAPH_EXTRACTION_MAX_SYNC_ATTEMPTS,
        )
        sync_drive_metadata(
            connection=self.connection,
            client=client,
            content_exporter=exporter,
            queue_extraction=self._collect_queued(queued),
        )

        self.assertEqual(len(queued), 1)

    def test_sync_does_not_requeue_a_pending_extraction_that_was_just_queued(self):
        modified_time = datetime(2026, 7, 2, tzinfo=UTC)
        exporter = FakeContentExporter()
        queued = []
        client = FakeDriveMetadataClient([self._doc_metadata(modified_time)])

        sync_drive_metadata(
            connection=self.connection,
            client=client,
            content_exporter=exporter,
            queue_extraction=self._collect_queued(queued),
        )
        # The row is PENDING with a fresh updated_at: its first task message
        # is still in the queue, so a second overlapping sync must not
        # duplicate the extraction.
        sync_drive_metadata(
            connection=self.connection,
            client=client,
            content_exporter=exporter,
            queue_extraction=self._collect_queued(queued),
        )

        self.assertEqual(len(queued), 1)

    def test_sync_requeues_a_pending_extraction_older_than_the_requeue_window(self):
        modified_time = datetime(2026, 7, 2, tzinfo=UTC)
        exporter = FakeContentExporter()
        queued = []
        client = FakeDriveMetadataClient([self._doc_metadata(modified_time)])

        sync_drive_metadata(
            connection=self.connection,
            client=client,
            content_exporter=exporter,
            queue_extraction=self._collect_queued(queued),
        )
        SourceDocument.objects.update(
            graph_extraction_queued_at=timezone.now()
            - timedelta(minutes=settings.GRAPH_EXTRACTION_PENDING_REQUEUE_AFTER_MINUTES + 1)
        )
        sync_drive_metadata(
            connection=self.connection,
            client=client,
            content_exporter=exporter,
            queue_extraction=self._collect_queued(queued),
        )

        self.assertEqual(len(queued), 2)

    def test_sync_reexports_when_modified_time_changes(self):
        exporter = FakeContentExporter()
        queued = []
        client = FakeDriveMetadataClient([self._doc_metadata(datetime(2026, 7, 2, tzinfo=UTC))])
        sync_drive_metadata(
            connection=self.connection,
            client=client,
            content_exporter=exporter,
            queue_extraction=self._collect_queued(queued),
        )

        client.files = [self._doc_metadata(datetime(2026, 7, 3, tzinfo=UTC))]
        sync_drive_metadata(
            connection=self.connection,
            client=client,
            content_exporter=exporter,
            queue_extraction=self._collect_queued(queued),
        )

        self.assertEqual(exporter.calls, ["drive-file-1", "drive-file-1"])
        self.assertEqual(len(queued), 2)

    def test_second_sync_of_unchanged_doc_keeps_the_exported_content_hash(self):
        # Google-native files carry no md5Checksum, so metadata upserts must
        # not wipe the sha256 the content stage stored on the first sync.
        exporter = FakeContentExporter()
        client = FakeDriveMetadataClient([self._doc_metadata(datetime(2026, 7, 2, tzinfo=UTC))])
        expected_hash = hashlib.sha256(b"exported-bytes").hexdigest()

        for _ in range(2):
            sync_drive_metadata(
                connection=self.connection,
                client=client,
                content_exporter=exporter,
                queue_extraction=lambda _document_id, _content_hash: None,
            )
            document = SourceDocument.objects.get(drive_file_id="drive-file-1")
            self.assertEqual(document.content_hash, expected_hash)

    def test_metadata_only_sync_preserves_the_exported_content_hash(self):
        exporter = FakeContentExporter()
        client = FakeDriveMetadataClient([self._doc_metadata(datetime(2026, 7, 2, tzinfo=UTC))])
        sync_drive_metadata(
            connection=self.connection,
            client=client,
            content_exporter=exporter,
            queue_extraction=lambda _document_id, _content_hash: None,
        )

        # A later metadata-only sync (no exporter) must not touch the hash.
        sync_drive_metadata(connection=self.connection, client=client)

        document = SourceDocument.objects.get(drive_file_id="drive-file-1")
        self.assertEqual(document.content_hash, hashlib.sha256(b"exported-bytes").hexdigest())

    def test_exclusion_round_trip_keeps_hash_and_content_consistent(self):
        exporter = FakeContentExporter()
        modified_time = datetime(2026, 7, 2, tzinfo=UTC)
        private = self._doc_metadata(modified_time)
        public = DriveFileMetadata(
            drive_file_id=private.drive_file_id,
            title=private.title,
            mime_type=private.mime_type,
            modified_time=modified_time,
            permissions=[{"id": "anyone", "type": "anyone", "role": "reader"}],
        )
        client = FakeDriveMetadataClient([private])
        expected_hash = hashlib.sha256(b"exported-bytes").hexdigest()

        for files in ([private], [public], [private]):
            client.files = files
            sync_drive_metadata(
                connection=self.connection,
                client=client,
                content_exporter=exporter,
                queue_extraction=lambda _document_id, _content_hash: None,
            )

        document = SourceDocument.objects.get(drive_file_id="drive-file-1")
        content = SourceDocumentContent.objects.get(source_document=document)
        self.assertEqual(document.content_hash, expected_hash)
        self.assertEqual(content.content_hash, expected_hash)
        self.assertEqual(bytes(content.content), b"exported-bytes")

    def test_sync_stores_drive_md5_separately_from_content_hash(self):
        exporter = FakeContentExporter()
        client = FakeDriveMetadataClient(
            [
                DriveFileMetadata(
                    drive_file_id="pdf-1",
                    title="Upload.pdf",
                    mime_type="application/pdf",
                    md5_checksum="drive-md5",
                    modified_time=datetime(2026, 7, 2, tzinfo=UTC),
                    permissions=[{"id": "perm-1", "type": "user", "role": "reader"}],
                )
            ]
        )

        sync_drive_metadata(
            connection=self.connection,
            client=client,
            content_exporter=exporter,
            queue_extraction=lambda _document_id, _content_hash: None,
        )

        document = SourceDocument.objects.get(drive_file_id="pdf-1")
        self.assertEqual(document.drive_md5_checksum, "drive-md5")
        self.assertEqual(document.content_hash, hashlib.sha256(b"exported-bytes").hexdigest())

    def test_sync_never_exports_excluded_files(self):
        exporter = FakeContentExporter()
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

        sync_drive_metadata(
            connection=self.connection,
            client=client,
            content_exporter=exporter,
            queue_extraction=lambda _document_id, _content_hash: self.fail(
                "must not queue excluded files"
            ),
        )

        self.assertEqual(exporter.calls, [])
        self.assertFalse(SourceDocumentContent.objects.exists())

    def test_extraction_is_not_queued_when_the_sync_fails(self):
        exporter = FakeContentExporter(error=RuntimeError("boom"))
        queued = []
        client = FakeDriveMetadataClient([self._doc_metadata(datetime(2026, 7, 2, tzinfo=UTC))])

        with self.assertRaises(RuntimeError):
            sync_drive_metadata(
                connection=self.connection,
                client=client,
                content_exporter=exporter,
                queue_extraction=self._collect_queued(queued),
            )

        self.assertEqual(queued, [])
        run = DriveSyncRun.objects.get()
        self.assertEqual(run.status, DriveSyncRun.Status.FAILED)
        self.assertEqual(run.error_summary, "builtins.RuntimeError")


class DriveRootApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.admin = get_user_model().objects.create_user(
            username="admin",
            email="admin@example.com",
            password="test-password",
            is_staff=True,
        )
        self.connection = DriveConnection.objects.create(
            workspace_domain="example.com",
            root_folder_id="folder-old",
        )

    def _candidates(self):
        return [
            DriveRootCandidate(
                scope_type=DriveConnection.ScopeType.FOLDER,
                root_id="folder-123",
                name="Client Folder",
                drive_url="https://drive.google.com/folders/folder-123",
            ),
            DriveRootCandidate(
                scope_type=DriveConnection.ScopeType.SHARED_DRIVE,
                root_id="drive-123",
                name="Client Shared Drive",
                shared_drive_id="drive-123",
            ),
        ]

    def _login_member(self):
        member = get_user_model().objects.create_user(
            username="member",
            email="member@example.com",
            password="test-password",
        )
        self.client.force_login(member)

    def test_root_list_rejects_anonymous_requests(self):
        response = self.client.get("/api/ingest/drive/roots/")

        self.assertEqual(response.status_code, 403)

    def test_root_list_rejects_non_admin_users(self):
        self._login_member()

        response = self.client.get("/api/ingest/drive/roots/")

        self.assertEqual(response.status_code, 403)

    def test_permission_check_rejects_anonymous_requests(self):
        response = self.client.get("/api/ingest/drive/permissions/check/")

        self.assertEqual(response.status_code, 403)

    def test_permission_check_rejects_non_admin_users(self):
        self._login_member()

        response = self.client.get("/api/ingest/drive/permissions/check/")

        self.assertEqual(response.status_code, 403)

    def test_root_selection_rejects_anonymous_requests(self):
        response = self.client.post(
            "/api/ingest/drive/connection/root/",
            data={"scope_type": "folder", "root_id": "folder-123"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)

    def test_root_selection_rejects_non_admin_users(self):
        self._login_member()

        response = self.client.post(
            "/api/ingest/drive/connection/root/",
            data={"scope_type": "folder", "root_id": "folder-123"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)

    def test_delegated_subject_rejects_anonymous_requests(self):
        response = self.client.post(
            "/api/ingest/drive/connection/delegated-subject/",
            data={"delegated_subject_email": "admin@example.com"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)

    def test_delegated_subject_rejects_non_admin_users(self):
        self._login_member()

        response = self.client.post(
            "/api/ingest/drive/connection/delegated-subject/",
            data={"delegated_subject_email": "admin@example.com"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)

    def test_admin_can_set_delegated_subject_and_invalidate_retrievable_documents(self):
        other_connection = DriveConnection.objects.create(
            workspace_domain="other.example.com",
            root_folder_id="other-folder",
        )
        stale_document = SourceDocument.objects.create(
            connection=self.connection,
            drive_file_id="drive-file-1",
            title="Old Delegation Notes",
            mime_type="application/pdf",
            retrieval_eligible=True,
        )
        unrelated_document = SourceDocument.objects.create(
            connection=other_connection,
            drive_file_id="drive-file-2",
            title="Other Client Notes",
            mime_type="application/pdf",
            retrieval_eligible=True,
        )
        self.client.force_login(self.admin)

        response = self.client.post(
            "/api/ingest/drive/connection/delegated-subject/",
            data={"delegated_subject_email": "delegated-admin@example.com"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "connection_id": self.connection.pk,
                "delegated_subject_email": "delegated-admin@example.com",
                "invalidated_document_count": 1,
            },
        )
        self.connection.refresh_from_db()
        stale_document.refresh_from_db()
        unrelated_document.refresh_from_db()
        self.assertEqual(self.connection.delegated_subject_email, "delegated-admin@example.com")
        self.assertFalse(stale_document.retrieval_eligible)
        self.assertTrue(unrelated_document.retrieval_eligible)

    def test_admin_can_clear_delegated_subject(self):
        self.connection.delegated_subject_email = "delegated-admin@example.com"
        self.connection.save(update_fields=["delegated_subject_email"])
        self.client.force_login(self.admin)

        response = self.client.post(
            "/api/ingest/drive/connection/delegated-subject/",
            data={"delegated_subject_email": ""},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["delegated_subject_email"], "")
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.delegated_subject_email, "")

    def test_reposting_same_delegated_subject_does_not_invalidate_documents(self):
        self.connection.delegated_subject_email = "delegated-admin@example.com"
        self.connection.save(update_fields=["delegated_subject_email"])
        document = SourceDocument.objects.create(
            connection=self.connection,
            drive_file_id="drive-file-1",
            title="Current Delegation Notes",
            mime_type="application/pdf",
            retrieval_eligible=True,
        )
        self.client.force_login(self.admin)

        response = self.client.post(
            "/api/ingest/drive/connection/delegated-subject/",
            data={"delegated_subject_email": "delegated-admin@example.com"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["invalidated_document_count"], 0)
        document.refresh_from_db()
        self.assertTrue(document.retrieval_eligible)

    def test_invalid_delegated_subject_email_is_rejected(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            "/api/ingest/drive/connection/delegated-subject/",
            data={"delegated_subject_email": "not-an-email"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("delegated_subject_email", response.json())
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.delegated_subject_email, "")

    def test_delegated_subject_endpoint_ignores_drive_scope_fields(self):
        self.client.force_login(self.admin)

        response = self.client.post(
            "/api/ingest/drive/connection/delegated-subject/",
            data={
                "delegated_subject_email": "delegated-admin@example.com",
                "scope_type": "shared_drive",
                "root_id": "drive-123",
                "root_folder_id": "attacker-folder",
                "shared_drive_id": "attacker-drive",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.scope_type, DriveConnection.ScopeType.FOLDER)
        self.assertEqual(self.connection.root_folder_id, "folder-old")
        self.assertEqual(self.connection.shared_drive_id, "")

    @patch("integrations.views.GoogleDriveMetadataClient")
    def test_admin_can_list_visible_drive_roots(self, mock_client_class):
        fake_client = FakeDriveRootClient(self._candidates())
        mock_client_class.return_value = fake_client
        self.client.force_login(self.admin)

        response = self.client.get("/api/ingest/drive/roots/")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["connection_id"], self.connection.pk)
        self.assertEqual(
            [(root["scope_type"], root["root_id"], root["name"]) for root in payload["roots"]],
            [
                ("folder", "folder-123", "Client Folder"),
                ("shared_drive", "drive-123", "Client Shared Drive"),
            ],
        )
        self.assertEqual(fake_client.connections, [self.connection])

    @patch("integrations.views.GoogleDriveMetadataClient")
    def test_admin_can_select_visible_folder_root(self, mock_client_class):
        self.connection.scope_type = DriveConnection.ScopeType.SHARED_DRIVE
        self.connection.shared_drive_id = "drive-old"
        self.connection.save(update_fields=["scope_type", "shared_drive_id"])
        mock_client_class.return_value = FakeDriveRootClient(self._candidates())
        self.client.force_login(self.admin)

        response = self.client.post(
            "/api/ingest/drive/connection/root/",
            data={"scope_type": "folder", "root_id": "folder-123"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.scope_type, DriveConnection.ScopeType.FOLDER)
        self.assertEqual(self.connection.root_folder_id, "folder-123")
        self.assertEqual(self.connection.shared_drive_id, "")
        self.assertEqual(response.json()["selected_root"]["name"], "Client Folder")
        self.assertEqual(response.json()["rescoped_document_count"], 0)

    @patch("integrations.views.GoogleDriveMetadataClient")
    def test_admin_can_select_visible_shared_drive_root(self, mock_client_class):
        mock_client_class.return_value = FakeDriveRootClient(self._candidates())
        self.client.force_login(self.admin)

        response = self.client.post(
            "/api/ingest/drive/connection/root/",
            data={"scope_type": "shared_drive", "root_id": "drive-123"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.scope_type, DriveConnection.ScopeType.SHARED_DRIVE)
        self.assertEqual(self.connection.root_folder_id, "")
        self.assertEqual(self.connection.shared_drive_id, "drive-123")

    @patch("integrations.views.GoogleDriveMetadataClient")
    def test_root_change_marks_existing_documents_retrieval_ineligible(self, mock_client_class):
        other_connection = DriveConnection.objects.create(
            workspace_domain="other.example.com",
            root_folder_id="other-folder",
        )
        stale_document = SourceDocument.objects.create(
            connection=self.connection,
            drive_file_id="drive-file-1",
            title="Old Scope Notes",
            mime_type="application/pdf",
            retrieval_eligible=True,
        )
        unrelated_document = SourceDocument.objects.create(
            connection=other_connection,
            drive_file_id="drive-file-2",
            title="Other Client Notes",
            mime_type="application/pdf",
            retrieval_eligible=True,
        )
        mock_client_class.return_value = FakeDriveRootClient(self._candidates())
        self.client.force_login(self.admin)

        response = self.client.post(
            "/api/ingest/drive/connection/root/",
            data={"scope_type": "folder", "root_id": "folder-123"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["rescoped_document_count"], 1)
        stale_document.refresh_from_db()
        unrelated_document.refresh_from_db()
        self.assertFalse(stale_document.retrieval_eligible)
        self.assertTrue(unrelated_document.retrieval_eligible)

    @patch("integrations.views.GoogleDriveMetadataClient")
    def test_reselecting_same_root_does_not_rescope_existing_documents(self, mock_client_class):
        document = SourceDocument.objects.create(
            connection=self.connection,
            drive_file_id="drive-file-1",
            title="Current Scope Notes",
            mime_type="application/pdf",
            retrieval_eligible=True,
        )
        mock_client_class.return_value = FakeDriveRootClient(
            [
                DriveRootCandidate(
                    scope_type=DriveConnection.ScopeType.FOLDER,
                    root_id="folder-old",
                    name="Current Folder",
                )
            ]
        )
        self.client.force_login(self.admin)

        response = self.client.post(
            "/api/ingest/drive/connection/root/",
            data={"scope_type": "folder", "root_id": "folder-old"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["rescoped_document_count"], 0)
        document.refresh_from_db()
        self.assertTrue(document.retrieval_eligible)

    @patch("integrations.views.GoogleDriveMetadataClient")
    def test_unknown_root_selection_is_rejected(self, mock_client_class):
        mock_client_class.return_value = FakeDriveRootClient(self._candidates())
        self.client.force_login(self.admin)

        response = self.client.post(
            "/api/ingest/drive/connection/root/",
            data={"scope_type": "folder", "root_id": "attacker-folder"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.connection.refresh_from_db()
        self.assertEqual(self.connection.scope_type, DriveConnection.ScopeType.FOLDER)
        self.assertEqual(self.connection.root_folder_id, "folder-old")

    @override_settings(
        GOOGLE_WORKSPACE_DOMAIN="example.com",
        GOOGLE_DRIVE_DELEGATED_SUBJECT="admin@example.com",
        GOOGLE_DRIVE_SCOPE_TYPE="folder",
        GOOGLE_DRIVE_ROOT_ID="",
        GOOGLE_SHARED_DRIVE_ID="",
    )
    @patch("integrations.views.GoogleDriveMetadataClient")
    def test_root_listing_does_not_persist_a_connection_when_none_exists(self, mock_client_class):
        # Root discovery is a read: it answers from a transient connection built
        # from settings and must not create a DriveConnection row on a GET.
        self.connection.delete()
        fake_client = FakeDriveRootClient(self._candidates())
        mock_client_class.return_value = fake_client
        self.client.force_login(self.admin)

        response = self.client.get("/api/ingest/drive/roots/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["connection_id"], None)
        self.assertEqual(DriveConnection.objects.count(), 0)
        # The transient connection still carries the settings-derived values the
        # Drive client needs (delegated subject, scope) even though it is unsaved.
        self.assertEqual(len(fake_client.connections), 1)
        transient = fake_client.connections[0]
        self.assertIsNone(transient.pk)
        self.assertEqual(transient.workspace_domain, "example.com")
        self.assertEqual(transient.delegated_subject_email, "admin@example.com")
        self.assertEqual(transient.credential_reference, "GOOGLE_SERVICE_ACCOUNT_FILE")

    @patch("integrations.views.GoogleDriveMetadataClient")
    def test_drive_api_errors_return_controlled_bad_gateway(self, mock_client_class):
        mock_client_class.return_value = FakeDriveRootClient(
            error=GoogleDriveApiError("Google Drive API request failed while listing Drive roots.")
        )
        self.client.force_login(self.admin)

        response = self.client.get("/api/ingest/drive/roots/")

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.json(),
            {"detail": "Google Drive API request failed while listing Drive roots."},
        )

        response = self.client.post(
            "/api/ingest/drive/connection/root/",
            data={"scope_type": "folder", "root_id": "folder-123"},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.json(),
            {"detail": "Google Drive API request failed while listing Drive roots."},
        )

    @patch(
        "integrations.drive.google_client.build_drive_service",
        side_effect=RefreshError("revoked service-account key"),
    )
    def test_service_construction_failures_return_controlled_bad_gateway(self, mock_build):
        # No injected fake service here: the real client must wrap failures
        # raised while *building* the service, not just while listing.
        self.client.force_login(self.admin)

        response = self.client.get("/api/ingest/drive/roots/")

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.json(),
            {"detail": "Google Drive API request failed while listing Drive roots."},
        )

    @patch("integrations.views.GoogleDriveMetadataClient")
    def test_admin_can_check_permission_metadata_access(self, mock_client_class):
        fake_client = FakeDriveRootClient(self._candidates())
        mock_client_class.return_value = fake_client
        self.client.force_login(self.admin)

        response = self.client.get("/api/ingest/drive/permissions/check/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "connection_id": self.connection.pk,
                "selected_root": {
                    "scope_type": "folder",
                    "root_folder_id": "folder-old",
                    "shared_drive_id": "",
                },
                "permission_metadata_access": "partial",
                "sampled_files": 3,
                "permissions_readable": 2,
                "permissions_unreadable": 1,
                "folder_listing_errors": 0,
                "checked_all_available_files": True,
            },
        )
        self.assertEqual(fake_client.connections, [self.connection])

    @patch("integrations.views.GoogleDriveMetadataClient")
    def test_permission_check_reports_ok_when_all_acl_samples_are_readable(self, mock_client_class):
        mock_client_class.return_value = FakeDriveRootClient(self._candidates())
        mock_client_class.return_value.check_permission_access = lambda connection: (
            DrivePermissionAccessReport(
                sampled_files=2,
                readable_files=2,
                unreadable_files=0,
                checked_all_available_files=True,
            )
        )
        self.client.force_login(self.admin)

        response = self.client.get("/api/ingest/drive/permissions/check/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["permission_metadata_access"], "ok")

    @patch("integrations.views.GoogleDriveMetadataClient")
    def test_permission_check_reports_no_files_for_empty_selected_root(self, mock_client_class):
        mock_client_class.return_value = FakeDriveRootClient(self._candidates())
        mock_client_class.return_value.check_permission_access = lambda connection: (
            DrivePermissionAccessReport(
                sampled_files=0,
                readable_files=0,
                unreadable_files=0,
                checked_all_available_files=True,
            )
        )
        self.client.force_login(self.admin)

        response = self.client.get("/api/ingest/drive/permissions/check/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["permission_metadata_access"], "no_files")

    @patch("integrations.views.GoogleDriveMetadataClient")
    def test_permission_check_reports_blocked_when_no_acl_sample_is_readable(
        self, mock_client_class
    ):
        mock_client_class.return_value = FakeDriveRootClient(self._candidates())
        mock_client_class.return_value.check_permission_access = lambda connection: (
            DrivePermissionAccessReport(
                sampled_files=2,
                readable_files=0,
                unreadable_files=2,
                checked_all_available_files=True,
            )
        )
        self.client.force_login(self.admin)

        response = self.client.get("/api/ingest/drive/permissions/check/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["permission_metadata_access"], "blocked")

    @patch("integrations.views.GoogleDriveMetadataClient")
    def test_permission_check_reports_listing_failed_when_only_folder_listing_fails(
        self, mock_client_class
    ):
        # Nothing could be sampled because folder listing itself failed — a
        # distinct signal from "blocked" (files sampled but ACLs unreadable).
        mock_client_class.return_value = FakeDriveRootClient(self._candidates())
        mock_client_class.return_value.check_permission_access = lambda connection: (
            DrivePermissionAccessReport(
                sampled_files=0,
                readable_files=0,
                unreadable_files=0,
                checked_all_available_files=False,
                folder_listing_errors=1,
            )
        )
        self.client.force_login(self.admin)

        response = self.client.get("/api/ingest/drive/permissions/check/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["permission_metadata_access"], "listing_failed")
        self.assertEqual(response.json()["folder_listing_errors"], 1)

    @patch("integrations.views.GoogleDriveMetadataClient")
    def test_permission_check_requires_selected_root(self, mock_client_class):
        self.connection.root_folder_id = ""
        self.connection.save(update_fields=["root_folder_id"])
        self.client.force_login(self.admin)

        response = self.client.get("/api/ingest/drive/permissions/check/")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json(),
            {"detail": "No Drive root has been selected for this connection."},
        )
        mock_client_class.assert_not_called()

    @patch("integrations.views.GoogleDriveMetadataClient")
    def test_permission_check_requires_a_connection_and_never_bootstraps(self, mock_client_class):
        # The diagnostic is a GET: with no connection it reports the missing
        # precondition instead of creating a DriveConnection row as a side effect.
        self.connection.delete()
        self.client.force_login(self.admin)

        response = self.client.get("/api/ingest/drive/permissions/check/")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json(),
            {"detail": "No enabled Drive connection is configured."},
        )
        self.assertEqual(DriveConnection.objects.count(), 0)
        mock_client_class.assert_not_called()

    @patch("integrations.views.GoogleDriveMetadataClient")
    def test_permission_check_missing_service_account_key_returns_conflict(self, mock_client_class):
        mock_client_class.return_value = FakeDriveRootClient(
            error=MissingServiceAccountKeyError("GOOGLE_SERVICE_ACCOUNT_FILE is not configured.")
        )
        self.client.force_login(self.admin)

        response = self.client.get("/api/ingest/drive/permissions/check/")

        self.assertEqual(response.status_code, 409)
        self.assertEqual(
            response.json(),
            {"detail": "GOOGLE_SERVICE_ACCOUNT_FILE is not configured."},
        )

    @patch("integrations.views.GoogleDriveMetadataClient")
    def test_permission_check_drive_api_errors_return_controlled_bad_gateway(
        self, mock_client_class
    ):
        mock_client_class.return_value = FakeDriveRootClient(
            error=GoogleDriveApiError(
                "Google Drive API request failed while checking Drive permission metadata access."
            )
        )
        self.client.force_login(self.admin)

        response = self.client.get("/api/ingest/drive/permissions/check/")

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.json(),
            {
                "detail": (
                    "Google Drive API request failed while checking Drive permission "
                    "metadata access."
                )
            },
        )


class DriveSyncApiTests(TestCase):
    def setUp(self):
        # Throttle counters live in the shared cache; clear them before AND
        # after each test so the 429 test can't bleed into other classes.
        cache.clear()
        self.addCleanup(cache.clear)
        self.admin = get_user_model().objects.create_user(
            username="admin",
            email="admin@example.com",
            password="test-password",
            is_staff=True,
        )
        self.connection = DriveConnection.objects.create(
            workspace_domain="example.com",
            root_folder_id="folder-123",
        )

    def test_anonymous_requests_are_rejected(self):
        response = self.client.post("/api/ingest/drive/sync/")

        self.assertEqual(response.status_code, 403)
        self.assertFalse(DriveSyncRun.objects.exists())

    def test_non_admin_users_are_rejected(self):
        member = get_user_model().objects.create_user(
            username="member",
            email="member@example.com",
            password="test-password",
        )
        self.client.force_login(member)

        response = self.client.post("/api/ingest/drive/sync/")

        self.assertEqual(response.status_code, 403)
        self.assertFalse(DriveSyncRun.objects.exists())

    @patch("integrations.views.run_drive_sync")
    def test_admin_sync_creates_audit_run_and_dispatches_task(self, mock_task):
        self.client.force_login(self.admin)

        response = self.client.post("/api/ingest/drive/sync/")

        self.assertEqual(response.status_code, 202)
        run = DriveSyncRun.objects.get()
        mock_task.delay.assert_called_once_with(run.pk)
        self.assertEqual(run.triggered_by, self.admin)
        self.assertEqual(run.actor_email, "admin@example.com")
        self.assertEqual(run.status, DriveSyncRun.Status.QUEUED)
        self.assertEqual(response.json()["run_id"], run.pk)

    @patch("integrations.views.run_drive_sync")
    def test_request_body_scope_is_ignored(self, mock_task):
        self.client.force_login(self.admin)

        response = self.client.post(
            "/api/ingest/drive/sync/",
            data={
                "scope_type": "shared_drive",
                "root_folder_id": "attacker-folder",
                "shared_drive_id": "attacker-drive",
            },
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 202)
        run = DriveSyncRun.objects.get()
        self.assertEqual(run.scope_type, DriveConnection.ScopeType.FOLDER)
        self.assertEqual(run.root_folder_id, "folder-123")
        self.assertEqual(run.shared_drive_id, "")
        self.assertEqual(response.json()["root_folder_id"], "folder-123")

    def test_missing_enabled_connection_returns_conflict(self):
        self.connection.enabled = False
        self.connection.save(update_fields=["enabled"])
        self.client.force_login(self.admin)

        response = self.client.post("/api/ingest/drive/sync/")

        self.assertEqual(response.status_code, 409)
        self.assertFalse(DriveSyncRun.objects.exists())

    def test_sync_without_selected_root_returns_conflict(self):
        self.connection.root_folder_id = ""
        self.connection.save(update_fields=["root_folder_id"])
        self.client.force_login(self.admin)

        response = self.client.post("/api/ingest/drive/sync/")

        self.assertEqual(response.status_code, 409)
        self.assertFalse(DriveSyncRun.objects.exists())

    # DRF resolves DEFAULT_THROTTLE_RATES at import time (the dict lands on
    # SimpleRateThrottle.THROTTLE_RATES), so override_settings can't reach it —
    # the shared rates dict has to be patched directly.
    @patch.dict(ScopedRateThrottle.THROTTLE_RATES, {"drive-sync": "2/hour"})
    @patch("integrations.views.run_drive_sync")
    def test_sync_endpoint_is_rate_limited(self, mock_task):
        self.client.force_login(self.admin)

        for _ in range(2):
            self.assertEqual(self.client.post("/api/ingest/drive/sync/").status_code, 202)

        self.assertEqual(self.client.post("/api/ingest/drive/sync/").status_code, 429)


class RunDriveSyncTaskTests(TestCase):
    def setUp(self):
        self.connection = DriveConnection.objects.create(
            workspace_domain="example.com",
            root_folder_id="folder-root",
        )
        self.run = DriveSyncRun.create_for_connection(self.connection)

    # The extraction pipeline is patched because eager Celery would run it
    # inline against a real Neo4j connection; it has its own tests in graph/.
    @patch("integrations.tasks.extract_document_to_graph")
    @patch("integrations.tasks.build_drive_service")
    def test_task_syncs_metadata_and_content_through_one_service(self, mock_build, mock_extract):
        mock_extract.return_value = {"source_document_id": 1, "status": "extracted", "chunks": 1}
        mock_build.return_value = FakeGoogleDriveService(
            folder_names={"folder-root": "Pilot"},
            children_pages={
                "folder-root": [
                    [
                        _file_entry(
                            "doc-1",
                            "Notes",
                            parents=["folder-root"],
                            mime_type="application/vnd.google-apps.document",
                            modifiedTime="2026-07-02T11:30:00Z",
                        )
                    ]
                ],
            },
            permission_pages={
                "doc-1": [
                    [
                        {
                            "id": "perm-1",
                            "type": "user",
                            "role": "reader",
                            "emailAddress": "a@example.com",
                        }
                    ]
                ],
            },
            export_data={"doc-1": "hello"},
        )

        result = run_drive_sync(self.run.pk)

        self.run.refresh_from_db()
        self.assertEqual(self.run.status, DriveSyncRun.Status.SUCCEEDED)
        self.assertEqual(self.run.stored_files, 1)
        document = SourceDocument.objects.get(drive_file_id="doc-1")
        self.assertEqual(bytes(document.content.content), b"hello")
        self.assertFalse(document.retrieval_eligible)
        self.assertEqual(result["status"], DriveSyncRun.Status.SUCCEEDED)
        # Eager Celery runs the queued extraction inline, so the handoff into
        # the graph pipeline is observable here.
        mock_extract.assert_called_once_with(document.pk, document.content_hash)

    @patch("integrations.tasks.build_drive_service", side_effect=RuntimeError("boom"))
    def test_setup_failure_marks_the_audit_run_failed(self, _mock_build):
        with self.assertRaises(RuntimeError):
            run_drive_sync(self.run.pk)

        self.run.refresh_from_db()
        self.assertEqual(self.run.status, DriveSyncRun.Status.FAILED)
        self.assertEqual(self.run.error_summary, "builtins.RuntimeError")
        self.assertIsNotNone(self.run.finished_at)

    @patch("integrations.tasks.build_drive_service")
    def test_finished_runs_are_never_reexecuted(self, mock_build):
        self.run.status = DriveSyncRun.Status.SUCCEEDED
        self.run.save(update_fields=["status"])

        result = run_drive_sync(self.run.pk)

        mock_build.assert_not_called()
        self.assertEqual(result["status"], DriveSyncRun.Status.SUCCEEDED)

    @patch("integrations.tasks.build_drive_service")
    def test_a_run_already_claimed_by_another_worker_is_not_reexecuted(self, mock_build):
        # Simulates the duplicate-delivery race: the first worker's atomic
        # claim moved the run to RUNNING, so the second worker must bail.
        self.run.status = DriveSyncRun.Status.RUNNING
        self.run.save(update_fields=["status"])

        result = run_drive_sync(self.run.pk)

        mock_build.assert_not_called()
        self.assertEqual(result["status"], DriveSyncRun.Status.RUNNING)


class QueueDocumentExtractionTaskTests(TestCase):
    def setUp(self):
        self.connection = DriveConnection.objects.create(
            workspace_domain="example.com",
            root_folder_id="folder-root",
        )
        self.document = SourceDocument.objects.create(
            connection=self.connection,
            drive_file_id="document-1",
            title="Notes",
            mime_type="text/plain",
            content_hash="hash",
        )
        SourceDocumentContent.objects.create(
            source_document=self.document,
            content=b"text",
            exported_mime_type="text/plain",
            content_hash="hash",
        )

    @patch("integrations.tasks.extract_document_to_graph")
    def test_delegates_to_the_graph_pipeline(self, mock_extract):
        mock_extract.return_value = {
            "source_document_id": self.document.pk,
            "status": "extracted",
            "chunks": 1,
        }

        result = queue_document_extraction(self.document.pk)

        mock_extract.assert_called_once_with(self.document.pk, "hash")
        self.assertEqual(result["status"], "extracted")
        self.document.refresh_from_db()
        self.assertEqual(
            self.document.graph_extraction_status,
            SourceDocument.GraphExtractionStatus.SUCCEEDED,
        )
        self.assertIsNotNone(self.document.graph_extraction_started_at)
        self.assertIsNotNone(self.document.graph_extraction_finished_at)

    @patch("integrations.tasks.extract_document_to_graph", side_effect=RuntimeError("boom"))
    def test_permanent_failure_is_recorded_without_error_text(self, _mock_extract):
        with self.assertRaises(RuntimeError):
            queue_document_extraction(self.document.pk)

        self.document.refresh_from_db()
        self.assertEqual(
            self.document.graph_extraction_status,
            SourceDocument.GraphExtractionStatus.FAILED,
        )
        self.assertEqual(self.document.graph_extraction_error_summary, "builtins.RuntimeError")
        self.assertEqual(self.document.graph_extraction_attempts, 1)
        self.assertIsNotNone(self.document.graph_extraction_finished_at)

    @patch(
        "integrations.tasks.extract_document_to_graph",
        side_effect=ServiceUnavailable("temporary Neo4j outage"),
    )
    @patch.object(queue_document_extraction, "retry", side_effect=Retry)
    def test_transient_failure_retries_without_marking_the_document_failed(
        self, mock_retry, _mock_extract
    ):
        with self.assertRaises(Retry):
            queue_document_extraction(self.document.pk)

        self.assertEqual(mock_retry.call_args.kwargs["countdown"], 2)
        self.document.refresh_from_db()
        self.assertEqual(
            self.document.graph_extraction_status,
            SourceDocument.GraphExtractionStatus.RUNNING,
        )
        self.assertEqual(self.document.graph_extraction_error_summary, "")
        self.assertIsNone(self.document.graph_extraction_finished_at)

    def test_retryable_errors_have_bounded_late_acknowledged_delivery(self):
        self.assertTrue(queue_document_extraction.acks_late)
        self.assertTrue(queue_document_extraction.reject_on_worker_lost)
        self.assertEqual(queue_document_extraction.max_retries, 3)
        self.assertIn(ServiceUnavailable, get_retryable_extraction_exceptions())

    @override_settings(GRAPH_EXTRACTION_ENGINE="neo4j_graphrag")
    def test_the_llm_engine_adds_its_own_transient_provider_failures(self):
        from openai import RateLimitError

        retryable = get_retryable_extraction_exceptions()

        self.assertIn(RateLimitError, retryable)
        self.assertIn(ServiceUnavailable, retryable)

    @patch("integrations.tasks.extract_document_to_graph")
    def test_stale_result_does_not_overwrite_the_newer_content_state(self, mock_extract):
        def replace_content_while_extracting(*_args):
            SourceDocumentContent.objects.filter(source_document=self.document).update(
                content=b"new text", content_hash="new-hash"
            )
            SourceDocument.objects.filter(pk=self.document.pk).update(
                content_hash="new-hash",
                graph_extraction_status=SourceDocument.GraphExtractionStatus.PENDING,
            )
            return {
                "source_document_id": self.document.pk,
                "status": "skipped_stale_content_version",
            }

        mock_extract.side_effect = replace_content_while_extracting

        result = queue_document_extraction(self.document.pk, "hash")

        self.assertEqual(result["status"], "skipped_stale_content_version")
        self.document.refresh_from_db()
        self.assertEqual(self.document.content_hash, "new-hash")
        self.assertEqual(
            self.document.graph_extraction_status,
            SourceDocument.GraphExtractionStatus.PENDING,
        )


class SweepStaleDriveSyncRunsTaskTests(TestCase):
    def setUp(self):
        self.connection = DriveConnection.objects.create(
            workspace_domain="example.com",
            root_folder_id="folder-root",
        )

    def _run(self, status, started_at):
        run = DriveSyncRun.create_for_connection(self.connection)
        DriveSyncRun.objects.filter(pk=run.pk).update(status=status, started_at=started_at)
        run.refresh_from_db()
        return run

    def test_running_past_the_timeout_is_marked_failed(self):
        stale_cutoff = timezone.now() - timedelta(
            minutes=settings.DRIVE_SYNC_STALE_RUN_TIMEOUT_MINUTES + 1
        )
        run = self._run(DriveSyncRun.Status.RUNNING, stale_cutoff)

        result = sweep_stale_drive_sync_runs()

        run.refresh_from_db()
        self.assertEqual(result, {"swept": 1})
        self.assertEqual(run.status, DriveSyncRun.Status.FAILED)
        self.assertEqual(run.error_summary, "stale_run_timeout")
        self.assertIsNotNone(run.finished_at)

    def test_running_within_the_timeout_is_left_alone(self):
        recent = timezone.now() - timedelta(
            minutes=settings.DRIVE_SYNC_STALE_RUN_TIMEOUT_MINUTES - 1
        )
        run = self._run(DriveSyncRun.Status.RUNNING, recent)

        result = sweep_stale_drive_sync_runs()

        run.refresh_from_db()
        self.assertEqual(result, {"swept": 0})
        self.assertEqual(run.status, DriveSyncRun.Status.RUNNING)

    def test_other_statuses_are_never_touched(self):
        stale_cutoff = timezone.now() - timedelta(
            minutes=settings.DRIVE_SYNC_STALE_RUN_TIMEOUT_MINUTES + 1
        )
        queued = self._run(DriveSyncRun.Status.QUEUED, stale_cutoff)
        succeeded = self._run(DriveSyncRun.Status.SUCCEEDED, stale_cutoff)

        result = sweep_stale_drive_sync_runs()

        queued.refresh_from_db()
        succeeded.refresh_from_db()
        self.assertEqual(result, {"swept": 0})
        self.assertEqual(queued.status, DriveSyncRun.Status.QUEUED)
        self.assertEqual(succeeded.status, DriveSyncRun.Status.SUCCEEDED)


class SweepStaleGraphExtractionsTaskTests(TestCase):
    def setUp(self):
        self.connection = DriveConnection.objects.create(
            workspace_domain="example.com",
            root_folder_id="folder-root",
        )

    def _document(self, drive_file_id, status, started_at):
        document = SourceDocument.objects.create(
            connection=self.connection,
            drive_file_id=drive_file_id,
            title="Notes",
            mime_type="text/plain",
            content_hash="hash",
        )
        SourceDocument.objects.filter(pk=document.pk).update(
            graph_extraction_status=status,
            graph_extraction_started_at=started_at,
        )
        document.refresh_from_db()
        return document

    def test_running_past_the_timeout_is_marked_failed_and_spends_an_attempt(self):
        stale = timezone.now() - timedelta(
            minutes=settings.GRAPH_EXTRACTION_STALE_RUNNING_TIMEOUT_MINUTES + 1
        )
        document = self._document("doc-1", SourceDocument.GraphExtractionStatus.RUNNING, stale)

        result = sweep_stale_graph_extractions()

        document.refresh_from_db()
        self.assertEqual(result, {"swept": 1})
        self.assertEqual(
            document.graph_extraction_status, SourceDocument.GraphExtractionStatus.FAILED
        )
        self.assertEqual(document.graph_extraction_error_summary, "stale_running_timeout")
        self.assertEqual(document.graph_extraction_attempts, 1)
        self.assertIsNotNone(document.graph_extraction_finished_at)

    def test_running_within_the_timeout_is_left_alone(self):
        recent = timezone.now() - timedelta(
            minutes=settings.GRAPH_EXTRACTION_STALE_RUNNING_TIMEOUT_MINUTES - 1
        )
        document = self._document("doc-1", SourceDocument.GraphExtractionStatus.RUNNING, recent)

        result = sweep_stale_graph_extractions()

        document.refresh_from_db()
        self.assertEqual(result, {"swept": 0})
        self.assertEqual(
            document.graph_extraction_status, SourceDocument.GraphExtractionStatus.RUNNING
        )

    def test_other_statuses_are_never_touched(self):
        stale = timezone.now() - timedelta(
            minutes=settings.GRAPH_EXTRACTION_STALE_RUNNING_TIMEOUT_MINUTES + 1
        )
        pending = self._document("doc-1", SourceDocument.GraphExtractionStatus.PENDING, stale)
        succeeded = self._document("doc-2", SourceDocument.GraphExtractionStatus.SUCCEEDED, stale)

        result = sweep_stale_graph_extractions()

        pending.refresh_from_db()
        succeeded.refresh_from_db()
        self.assertEqual(result, {"swept": 0})
        self.assertEqual(
            pending.graph_extraction_status, SourceDocument.GraphExtractionStatus.PENDING
        )
        self.assertEqual(
            succeeded.graph_extraction_status, SourceDocument.GraphExtractionStatus.SUCCEEDED
        )
