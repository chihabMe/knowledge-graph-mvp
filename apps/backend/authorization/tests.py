import datetime
from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from authorization.client import PermissionTuple
from authorization.identifiers import (
    document_object_id,
    folder_object_id,
    group_object_id,
    user_object_id,
)
from authorization.lookup import allowed_source_document_ids
from authorization.sync import PermissionSyncError, synchronize_permissions
from integrations.drive.client import DriveFileMetadata, DrivePermissionResource
from integrations.drive.groups import GoogleGroupResolver, GroupMembership, GroupResolutionError
from integrations.drive.permissions import source_permissions_version
from integrations.drive.sync import sync_drive_metadata
from integrations.models import DriveConnection, DriveSyncRun, PermissionSyncRun, SourceDocument
from integrations.tasks import (
    run_permission_sync,
    schedule_permission_syncs,
    sweep_stale_permission_sync_runs,
)


class FakeDriveClient:
    def __init__(self, resources):
        self.resources = resources

    def list_permission_resources(self, connection):
        return self.resources


class FakeGroupResolver:
    def __init__(self, memberships=None, error=False):
        self.memberships = memberships or {}
        self.error = error

    def resolve(self, connection, group_emails):
        if self.error:
            raise GroupResolutionError("controlled")
        return {email: self.memberships[email] for email in group_emails}


class FakeSpiceDB:
    def __init__(self, current=None, *, mismatch=False, before_verify=None):
        self.current = set(current or ())
        self.mismatch = mismatch
        self.before_verify = before_verify
        self.write_calls = []
        self.lookup_result = ()
        self.fail_lookup = False
        self._reads = 0

    def read_managed_tuples(self, connection_prefix, *, revision=""):
        self._reads += 1
        if revision and self.before_verify:
            callback, self.before_verify = self.before_verify, None
            callback()
        if revision and self.mismatch:
            return set()
        return set(self.current)

    def write_updates(self, *, touches, deletes):
        touches = set(touches)
        deletes = set(deletes)
        self.write_calls.append((touches, deletes))
        self.current.difference_update(deletes)
        self.current.update(touches)
        return "zed-token-1"

    def lookup_documents(self, user_id):
        if self.fail_lookup:
            raise TimeoutError
        return self.lookup_result


def user_permission(email="reader@example.com", role="reader"):
    return {"id": f"p-{role}", "type": "user", "role": role, "emailAddress": email}


def group_permission(email="team@example.com", role="reader"):
    return {"id": f"g-{role}", "type": "group", "role": role, "emailAddress": email}


class IdentifierTests(TestCase):
    def test_identifiers_are_deterministic_connection_scoped_and_opaque(self):
        first = user_object_id(1, " Person@Example.com ")
        self.assertEqual(first, user_object_id(1, "person@example.com"))
        self.assertNotEqual(first, user_object_id(2, "person@example.com"))
        self.assertNotIn("person", first)


class VerifiedPredicateTests(TestCase):
    def test_queryset_and_instance_predicates_agree(self):
        connection = DriveConnection.objects.create(
            workspace_domain="example.com", root_folder_id="root"
        )
        document = SourceDocument.objects.create(
            connection=connection,
            drive_file_id="doc-1",
            title="Doc",
            mime_type="text/plain",
            active_in_scope=True,
            retrieval_eligible=True,
            source_permissions_version="v1",
            spicedb_permissions_version="v1",
            spicedb_verified_at=timezone.now(),
        )
        verified_pks = SourceDocument.objects.permission_verified().values_list("pk", flat=True)
        self.assertIn(document.pk, verified_pks)
        self.assertTrue(document.is_permission_verified("v1"))
        SourceDocument.objects.filter(pk=document.pk).update(spicedb_permissions_version="stale")
        document.refresh_from_db()
        verified_pks = SourceDocument.objects.permission_verified().values_list("pk", flat=True)
        self.assertNotIn(document.pk, verified_pks)
        self.assertFalse(document.is_permission_verified("v1"))


class ManagedTupleReadTests(TestCase):
    def test_managed_resource_types_track_the_schema(self):
        from authorization.client import MANAGED_RESOURCE_TYPES, canonical_schema, schema_text

        definitions = {name for name, _ in canonical_schema(schema_text())}
        self.assertEqual(set(MANAGED_RESOURCE_TYPES), definitions - {"kgm/user"})

    def test_reads_are_scoped_to_the_connection_prefix_server_side(self):
        from authorization.client import MANAGED_RESOURCE_TYPES, AuthzedSpiceDB

        grpc_client = Mock()
        grpc_client.ReadRelationships.return_value = []
        AuthzedSpiceDB(client=grpc_client).read_managed_tuples("c1_")
        requests = [call.args[0] for call in grpc_client.ReadRelationships.call_args_list]
        self.assertEqual(
            {request.relationship_filter.resource_type for request in requests},
            set(MANAGED_RESOURCE_TYPES),
        )
        for request in requests:
            self.assertEqual(request.relationship_filter.optional_resource_id_prefix, "c1_")


class PermissionSyncTests(TestCase):
    def setUp(self):
        self.connection = DriveConnection.objects.create(
            workspace_domain="example.com", root_folder_id="root"
        )
        self.document = SourceDocument.objects.create(
            connection=self.connection,
            drive_file_id="doc-1",
            title="Restricted title",
            mime_type="text/plain",
        )

    def run_sync(self, resources, *, groups=None, spicedb=None):
        run = PermissionSyncRun.objects.create(
            connection=self.connection, status=PermissionSyncRun.Status.RUNNING
        )
        return synchronize_permissions(
            run,
            drive_client=FakeDriveClient(resources),
            group_resolver=groups or FakeGroupResolver(),
            spicedb=spicedb or FakeSpiceDB(),
        )

    def test_folder_inheritance_and_every_drive_role(self):
        roles = ["reader", "commenter", "writer", "fileOrganizer", "organizer", "owner"]
        permissions = [user_permission(f"{role}@example.com", role) for role in roles]
        spicedb = FakeSpiceDB()
        run = self.run_sync(
            [
                DrivePermissionResource("folder", "root", permissions=permissions),
                DrivePermissionResource("document", "doc-1", ["root"], []),
            ],
            spicedb=spicedb,
        )
        self.document.refresh_from_db()
        self.assertEqual(run.status, PermissionSyncRun.Status.SUCCEEDED)
        self.assertTrue(self.document.retrieval_eligible)
        folder_id = folder_object_id(self.connection.pk, "root")
        relations = {
            item.relation
            for item in spicedb.current
            if item.resource_type == "kgm/folder" and item.resource_id == folder_id
        }
        self.assertEqual(
            relations,
            {"reader", "commenter", "writer", "file_organizer", "organizer", "owner"},
        )
        self.assertIn(
            PermissionTuple(
                "kgm/document",
                document_object_id(self.connection.pk, self.document.pk),
                "parent",
                "kgm/folder",
                folder_id,
            ),
            spicedb.current,
        )

    def test_nested_groups_create_recursive_subject_sets(self):
        memberships = {
            "parent@example.com": GroupMembership(
                frozenset({"direct@example.com"}), frozenset({"child@example.com"})
            ),
            "child@example.com": GroupMembership(frozenset({"nested@example.com"}), frozenset()),
        }
        spicedb = FakeSpiceDB()
        self.run_sync(
            [
                DrivePermissionResource("folder", "root", permissions=[]),
                DrivePermissionResource(
                    "document", "doc-1", ["root"], [group_permission("parent@example.com")]
                ),
            ],
            groups=FakeGroupResolver(memberships),
            spicedb=spicedb,
        )
        self.assertIn(
            PermissionTuple(
                "kgm/group",
                group_object_id(self.connection.pk, "parent@example.com"),
                "member",
                "kgm/group",
                group_object_id(self.connection.pk, "child@example.com"),
                "member",
            ),
            spicedb.current,
        )

    def test_public_unknown_and_unresolved_group_documents_fail_closed(self):
        cases = [
            ([{"id": "a", "type": "anyone", "role": "reader"}], "public_link_not_supported"),
            (
                [{"id": "x", "type": "user", "role": "unknown", "emailAddress": "a@b.c"}],
                "unsupported_permission",
            ),
            ([group_permission()], "group_membership_unresolved"),
        ]
        for permissions, reason in cases:
            with self.subTest(reason=reason):
                run = self.run_sync(
                    [
                        DrivePermissionResource("folder", "root", permissions=[]),
                        DrivePermissionResource("document", "doc-1", ["root"], permissions),
                    ],
                    groups=FakeGroupResolver(error=True),
                )
                self.document.refresh_from_db()
                self.assertEqual(run.status, PermissionSyncRun.Status.PARTIAL)
                self.assertFalse(self.document.retrieval_eligible)
                self.assertEqual(self.document.exclusion_reason, reason)

    def test_document_without_any_grant_path_is_not_eligible(self):
        run = self.run_sync(
            [
                DrivePermissionResource("folder", "root", permissions=[]),
                DrivePermissionResource("document", "doc-1", [], permissions=[]),
            ]
        )
        self.document.refresh_from_db()
        self.assertEqual(run.status, PermissionSyncRun.Status.PARTIAL)
        self.assertFalse(self.document.retrieval_eligible)
        self.assertEqual(self.document.exclusion_reason, "no_effective_grants")

    def test_null_email_principal_fails_closed_without_crash(self):
        permissions = [{"id": "p", "type": "user", "role": "reader", "emailAddress": None}]
        run = self.run_sync(
            [
                DrivePermissionResource("folder", "root", permissions=[]),
                DrivePermissionResource("document", "doc-1", ["root"], permissions),
            ]
        )
        self.document.refresh_from_db()
        self.assertEqual(run.status, PermissionSyncRun.Status.PARTIAL)
        self.assertFalse(self.document.retrieval_eligible)
        self.assertEqual(self.document.exclusion_reason, "unsupported_permission")

    def test_exact_stale_deletion_and_scope_revocation(self):
        stale = PermissionTuple(
            "kgm/document",
            document_object_id(self.connection.pk, self.document.pk),
            "reader",
            "kgm/user",
            user_object_id(self.connection.pk, "removed@example.com"),
        )
        spicedb = FakeSpiceDB({stale})
        self.run_sync([DrivePermissionResource("folder", "root", permissions=[])], spicedb=spicedb)
        self.document.refresh_from_db()
        self.assertFalse(self.document.active_in_scope)
        self.assertNotIn(stale, spicedb.current)
        self.assertIn(stale, spicedb.write_calls[0][1])

    def test_verification_mismatch_keeps_document_ineligible(self):
        spicedb = FakeSpiceDB(mismatch=True)
        with self.assertRaises(PermissionSyncError):
            self.run_sync(
                [
                    DrivePermissionResource("folder", "root", permissions=[]),
                    DrivePermissionResource("document", "doc-1", ["root"], [user_permission()]),
                ],
                spicedb=spicedb,
            )
        self.document.refresh_from_db()
        self.assertFalse(self.document.retrieval_eligible)

    def test_acl_version_cas_prevents_stale_reenable(self):
        def change_acl_version():
            SourceDocument.objects.filter(pk=self.document.pk).update(
                source_permissions_version="newer-version"
            )

        spicedb = FakeSpiceDB(before_verify=change_acl_version)
        run = self.run_sync(
            [
                DrivePermissionResource("folder", "root", permissions=[]),
                DrivePermissionResource("document", "doc-1", ["root"], [user_permission()]),
            ],
            spicedb=spicedb,
        )
        self.document.refresh_from_db()
        self.assertEqual(run.documents_verified, 0)
        self.assertFalse(self.document.retrieval_eligible)

    def test_failed_run_keeps_previously_verified_state(self):
        SourceDocument.objects.filter(pk=self.document.pk).update(
            retrieval_eligible=True,
            spicedb_verified_at=timezone.now(),
        )

        class FailingDrive:
            def list_permission_resources(self, connection):
                raise OSError

        run = PermissionSyncRun.objects.create(
            connection=self.connection, status=PermissionSyncRun.Status.RUNNING
        )
        with self.assertRaises(OSError):
            synchronize_permissions(
                run,
                drive_client=FailingDrive(),
                group_resolver=FakeGroupResolver(),
                spicedb=FakeSpiceDB(),
            )
        self.document.refresh_from_db()
        run.refresh_from_db()
        self.assertEqual(run.status, PermissionSyncRun.Status.FAILED)
        self.assertTrue(self.document.retrieval_eligible)
        self.assertIsNotNone(self.document.spicedb_verified_at)

    def test_partial_scan_exception_never_marks_unseen_document_inactive(self):
        class FailingDrive:
            def list_permission_resources(self, connection):
                raise OSError

        run = PermissionSyncRun.objects.create(
            connection=self.connection, status=PermissionSyncRun.Status.RUNNING
        )
        with self.assertRaises(OSError):
            synchronize_permissions(
                run,
                drive_client=FailingDrive(),
                group_resolver=FakeGroupResolver(),
                spicedb=FakeSpiceDB(),
            )
        self.document.refresh_from_db()
        self.assertTrue(self.document.active_in_scope)
        self.assertFalse(self.document.retrieval_eligible)


class AllowedDocumentLookupTests(TestCase):
    def setUp(self):
        self.connection = DriveConnection.objects.create(
            workspace_domain="example.com", root_folder_id="root"
        )
        version = source_permissions_version([user_permission()])
        self.document = SourceDocument.objects.create(
            connection=self.connection,
            drive_file_id="doc-1",
            title="Secret",
            mime_type="text/plain",
            source_permissions_version=version,
            spicedb_permissions_version=version,
            spicedb_revision="token",
            spicedb_verified_at=timezone.now(),
            retrieval_eligible=True,
            active_in_scope=True,
        )

    def test_lookup_maps_only_verified_active_rows(self):
        spicedb = FakeSpiceDB()
        spicedb.lookup_result = (
            document_object_id(self.connection.pk, self.document.pk),
            "c999_d999",
        )
        self.assertEqual(
            allowed_source_document_ids(" Reader@Example.com ", spicedb=spicedb),
            (self.document.pk,),
        )

    def test_outage_or_empty_lookup_denies_everything(self):
        spicedb = FakeSpiceDB()
        self.assertEqual(allowed_source_document_ids("reader@example.com", spicedb=spicedb), ())
        spicedb.fail_lookup = True
        self.assertEqual(allowed_source_document_ids("reader@example.com", spicedb=spicedb), ())


class NormalDriveSyncPermissionTests(TestCase):
    def test_unchanged_acl_preserves_verification_and_changed_acl_invalidates_without_export(self):
        connection = DriveConnection.objects.create(
            workspace_domain="example.com", root_folder_id="root"
        )
        initial = [user_permission()]
        version = source_permissions_version(initial)
        document = SourceDocument.objects.create(
            connection=connection,
            drive_file_id="doc-1",
            title="Title",
            mime_type="text/plain",
            source_permissions_version=version,
            spicedb_permissions_version=version,
            spicedb_revision="token",
            spicedb_verified_at=timezone.now(),
            retrieval_eligible=True,
        )

        class MetadataClient:
            permissions = initial

            def list_files(self, connection):
                return [
                    DriveFileMetadata(
                        drive_file_id="doc-1",
                        title="Title",
                        mime_type="text/plain",
                        permissions=self.permissions,
                    )
                ]

        client = MetadataClient()
        queue = Mock()
        sync_drive_metadata(
            connection=connection,
            client=client,
            run=DriveSyncRun.create_for_connection(connection),
            queue_extraction=queue,
        )
        document.refresh_from_db()
        self.assertTrue(document.retrieval_eligible)
        self.assertEqual(document.spicedb_revision, "token")
        queue.assert_not_called()

        client.permissions = [user_permission(role="writer")]
        sync_drive_metadata(
            connection=connection,
            client=client,
            run=DriveSyncRun.create_for_connection(connection),
            queue_extraction=queue,
        )
        document.refresh_from_db()
        self.assertFalse(document.retrieval_eligible)
        self.assertIsNone(document.spicedb_verified_at)
        queue.assert_not_called()


class PermissionApiTests(TestCase):
    def setUp(self):
        self.connection = DriveConnection.objects.create(
            workspace_domain="example.com", root_folder_id="root"
        )
        self.admin = get_user_model().objects.create_user(
            username="admin", email="admin@example.com", is_staff=True
        )
        self.client = APIClient()

    def test_sync_requires_admin_and_queues_server_scoped_audit_row(self):
        response = self.client.post("/api/permissions/sync/", {"root_folder_id": "attacker"})
        self.assertEqual(response.status_code, 403)
        self.client.force_authenticate(self.admin)
        with patch("authorization.views.run_permission_sync.delay") as delay:
            response = self.client.post(
                "/api/permissions/sync/", {"root_folder_id": "attacker"}, format="json"
            )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(set(response.data), {"run_id", "status", "connection_id"})
        run = PermissionSyncRun.objects.get(pk=response.data["run_id"])
        self.assertEqual(run.connection_id, self.connection.pk)
        delay.assert_called_once_with(run.pk)

    def test_scope_root_mismatch_is_rejected_before_dispatch(self):
        DriveConnection.objects.filter(pk=self.connection.pk).update(
            scope_type=DriveConnection.ScopeType.SHARED_DRIVE, shared_drive_id=""
        )
        self.client.force_authenticate(self.admin)
        with patch("authorization.views.run_permission_sync.delay") as delay:
            response = self.client.post("/api/permissions/sync/")
        self.assertEqual(response.status_code, 409)
        delay.assert_not_called()

    def test_detail_response_contains_controlled_fields_only(self):
        run = PermissionSyncRun.objects.create(
            connection=self.connection,
            status=PermissionSyncRun.Status.FAILED,
            error_code="safe_code",
        )
        self.client.force_authenticate(self.admin)
        response = self.client.get(f"/api/permissions/sync/{run.pk}/")
        self.assertEqual(response.status_code, 200)
        body = str(response.data).lower()
        self.assertNotIn("example.com", body)
        self.assertNotIn("root", body)


class PermissionTaskTests(TestCase):
    def setUp(self):
        self.connection = DriveConnection.objects.create(
            workspace_domain="example.com", root_folder_id="root"
        )

    @patch("integrations.tasks.synchronize_permissions")
    def test_duplicate_completed_delivery_is_idempotent(self, synchronize):
        run = PermissionSyncRun.objects.create(
            connection=self.connection, status=PermissionSyncRun.Status.SUCCEEDED
        )
        result = run_permission_sync.run(run.pk)
        self.assertEqual(result, {"run_id": run.pk, "status": "succeeded"})
        synchronize.assert_not_called()

    @patch("integrations.tasks.synchronize_permissions", side_effect=OSError)
    def test_transient_failure_returns_run_to_queue_before_retry(self, synchronize):
        run = PermissionSyncRun.objects.create(connection=self.connection)
        with patch.object(run_permission_sync, "retry", side_effect=RuntimeError("retry")):
            with self.assertRaisesRegex(RuntimeError, "retry"):
                run_permission_sync.run(run.pk)
        run.refresh_from_db()
        self.assertEqual(run.status, PermissionSyncRun.Status.QUEUED)
        self.assertEqual(run.error_code, "")

    def test_beat_schedules_permission_sync_and_sweeper(self):
        from django.conf import settings

        tasks = {entry["task"] for entry in settings.CELERY_BEAT_SCHEDULE.values()}
        self.assertIn("integrations.schedule_permission_syncs", tasks)
        self.assertIn("integrations.sweep_stale_permission_sync_runs", tasks)

    def test_scheduler_enqueues_one_run_per_ready_connection(self):
        DriveConnection.objects.create(workspace_domain="example.com")  # no root
        busy = DriveConnection.objects.create(
            workspace_domain="example.com", root_folder_id="busy-root"
        )
        PermissionSyncRun.objects.create(connection=busy, status=PermissionSyncRun.Status.RUNNING)
        with patch("integrations.tasks.run_permission_sync.delay") as delay:
            result = schedule_permission_syncs.run()
        self.assertEqual(result, {"scheduled": 1})
        run = PermissionSyncRun.objects.get(connection=self.connection)
        delay.assert_called_once_with(run.pk)
        self.assertEqual(run.status, PermissionSyncRun.Status.QUEUED)

    def test_stale_running_run_is_swept_failed(self):
        stale = PermissionSyncRun.objects.create(
            connection=self.connection,
            status=PermissionSyncRun.Status.RUNNING,
            started_at=timezone.now() - datetime.timedelta(hours=3),
        )
        fresh = PermissionSyncRun.objects.create(
            connection=self.connection,
            status=PermissionSyncRun.Status.RUNNING,
            started_at=timezone.now(),
        )
        result = sweep_stale_permission_sync_runs.run()
        stale.refresh_from_db()
        fresh.refresh_from_db()
        self.assertEqual(result, {"swept": 1})
        self.assertEqual(stale.status, PermissionSyncRun.Status.FAILED)
        self.assertEqual(stale.error_code, "stale_run_timeout")
        self.assertEqual(fresh.status, PermissionSyncRun.Status.RUNNING)


class SchemaCommandTests(TestCase):
    @patch("authorization.management.commands.spicedb_schema_apply.AuthzedSpiceDB")
    def test_apply_is_idempotent(self, client_class):
        client = client_class.return_value
        client.read_schema.return_value = "different"
        call_command("spicedb_schema_apply")
        client.apply_schema.assert_called_once()
        client.reset_mock()
        from authorization.client import schema_text

        client.read_schema.return_value = schema_text()
        call_command("spicedb_schema_apply")
        client.apply_schema.assert_not_called()

    @patch("authorization.management.commands.spicedb_schema_check.AuthzedSpiceDB")
    def test_check_rejects_mismatch(self, client_class):
        client_class.return_value.read_schema.return_value = "invalid"
        with self.assertRaises(CommandError):
            call_command("spicedb_schema_check")


class GroupResolverTests(TestCase):
    def test_cycle_is_rejected_without_logging_payloads(self):
        call = Mock()
        call.execute.side_effect = [
            {"members": [{"email": "b@example.com", "type": "GROUP"}]},
            {"members": [{"email": "a@example.com", "type": "GROUP"}]},
        ]
        members = Mock()
        members.list.return_value = call
        service = Mock()
        service.members.return_value = members
        connection = DriveConnection(workspace_domain="example.com")
        with self.assertRaises(GroupResolutionError):
            GoogleGroupResolver(service=service).resolve(connection, {"a@example.com"})
