from unittest.mock import Mock, patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from authorization.client import AuthzedSpiceDB, PermissionTuple, schema_text
from authorization.identifiers import document_object_id, user_object_id
from authorization.oauth_viewer import (
    OAuthViewerRelationshipError,
    delete_oauth_viewer_relationships,
    reconcile_oauth_viewer_relationships,
)
from authorization.sync import PermissionSyncError, synchronize_permissions
from integrations.models import DriveConnection, PermissionSyncRun, SourceDocument
from integrations.tasks import schedule_permission_syncs


class FakeOAuthSpiceDB:
    def __init__(self, current=(), *, mismatch=False, fail=False):
        self.current = set(current)
        self.mismatch = mismatch
        self.fail = fail
        self.read_calls = []
        self.write_calls = []

    def read_oauth_viewer_tuples(self, prefix, user_id, *, revision=""):
        if self.fail:
            raise TimeoutError("provider payload")
        self.read_calls.append((prefix, user_id, revision))
        result = {
            item
            for item in self.current
            if item.resource_id.startswith(prefix)
            and item.relation == "oauth_viewer"
            and item.subject_id == user_id
        }
        if revision and self.mismatch:
            return set()
        return result

    def write_updates(self, *, touches, deletes):
        touches = set(touches)
        deletes = set(deletes)
        self.write_calls.append((touches, deletes))
        self.current.difference_update(deletes)
        self.current.update(touches)
        return "oauth-zed-token"


@override_settings(
    GOOGLE_PERMISSION_AUTHORITY=DriveConnection.PermissionAuthority.PER_USER_OAUTH,
    GOOGLE_USER_OAUTH_ALLOWED_DOMAIN="example.com",
    GOOGLE_USER_VISIBILITY_MAX_DOCUMENTS=10,
)
class OAuthViewerRelationshipTests(TestCase):
    def setUp(self):
        self.connection = DriveConnection.objects.create(
            workspace_domain="example.com",
            root_folder_id="selected-root",
            permission_authority=DriveConnection.PermissionAuthority.PER_USER_OAUTH,
        )
        self.first = SourceDocument.objects.create(
            connection=self.connection,
            drive_file_id="indexed-1",
            title="First",
            mime_type="text/plain",
        )
        self.second = SourceDocument.objects.create(
            connection=self.connection,
            drive_file_id="indexed-2",
            title="Second",
            mime_type="text/plain",
        )

    def tuple_for(self, document, email="pilot@example.com"):
        return PermissionTuple(
            "kgm/document",
            document_object_id(self.connection.pk, document.pk),
            "oauth_viewer",
            "kgm/user",
            user_object_id(self.connection.pk, email),
        )

    def test_reconcile_touches_only_intended_user_document_pairs_and_verifies_revision(self):
        spicedb = FakeOAuthSpiceDB()

        result = reconcile_oauth_viewer_relationships(
            connection=self.connection,
            user_email=" Pilot@Example.com ",
            source_document_ids=(self.first.pk, self.second.pk),
            spicedb=spicedb,
        )

        self.assertEqual(spicedb.current, {self.tuple_for(self.first), self.tuple_for(self.second)})
        self.assertEqual(result.revision, "oauth-zed-token")
        self.assertEqual(result.relationships_touched, 2)
        self.assertEqual(result.relationships_deleted, 0)
        self.assertEqual(spicedb.read_calls[-1][2], "oauth-zed-token")
        serialized = repr(spicedb.current)
        self.assertNotIn("pilot@example.com", serialized)
        self.assertNotIn("indexed-1", serialized)

    def test_reconcile_deletes_only_current_users_stale_direct_tuple(self):
        other_user_tuple = self.tuple_for(self.second, "other@example.com")
        stale = self.tuple_for(self.second)
        keep = self.tuple_for(self.first)
        spicedb = FakeOAuthSpiceDB({stale, keep, other_user_tuple})

        result = reconcile_oauth_viewer_relationships(
            connection=self.connection,
            user_email="pilot@example.com",
            source_document_ids=(self.first.pk,),
            spicedb=spicedb,
        )

        self.assertEqual(spicedb.current, {keep, other_user_tuple})
        self.assertEqual(result.relationships_deleted, 1)
        self.assertIn(stale, spicedb.write_calls[0][1])
        self.assertNotIn(other_user_tuple, spicedb.write_calls[0][1])

    def test_delete_is_exact_and_preserves_other_users(self):
        own = self.tuple_for(self.first)
        other = self.tuple_for(self.first, "other@example.com")
        spicedb = FakeOAuthSpiceDB({own, other})

        result = delete_oauth_viewer_relationships(
            connection=self.connection,
            user_email="pilot@example.com",
            spicedb=spicedb,
        )

        self.assertEqual(spicedb.current, {other})
        self.assertEqual(result.relationships_deleted, 1)
        self.assertEqual(spicedb.read_calls[-1][2], "oauth-zed-token")

    def test_unchanged_positive_set_is_touched_to_obtain_causal_revision(self):
        existing = self.tuple_for(self.first)
        spicedb = FakeOAuthSpiceDB({existing})

        result = reconcile_oauth_viewer_relationships(
            connection=self.connection,
            user_email="pilot@example.com",
            source_document_ids=(self.first.pk,),
            spicedb=spicedb,
        )

        self.assertEqual(spicedb.write_calls, [({existing}, set())])
        self.assertEqual(result.relationships_touched, 1)

    def test_verification_mismatch_and_spicedb_failure_fail_closed(self):
        for spicedb, code in (
            (FakeOAuthSpiceDB(mismatch=True), "relationship_verification_mismatch"),
            (FakeOAuthSpiceDB(fail=True), "spicedb_operation_failed"),
        ):
            with self.subTest(code=code):
                with self.assertRaisesRegex(OAuthViewerRelationshipError, code):
                    reconcile_oauth_viewer_relationships(
                        connection=self.connection,
                        user_email="pilot@example.com",
                        source_document_ids=(self.first.pk,),
                        spicedb=spicedb,
                    )

    def test_unindexed_inactive_invalid_and_over_cap_ids_never_reach_spicedb(self):
        SourceDocument.objects.filter(pk=self.second.pk).update(active_in_scope=False)
        cases = (
            (self.second.pk,),
            (999_999,),
            (True,),
            tuple(range(1, 12)),
        )
        for document_ids in cases:
            with self.subTest(document_ids=document_ids):
                spicedb = FakeOAuthSpiceDB()
                with self.assertRaises(OAuthViewerRelationshipError):
                    reconcile_oauth_viewer_relationships(
                        connection=self.connection,
                        user_email="pilot@example.com",
                        source_document_ids=document_ids,
                        spicedb=spicedb,
                    )
                self.assertEqual(spicedb.read_calls, [])
                self.assertEqual(spicedb.write_calls, [])

    def test_wrong_authority_or_domain_never_reaches_spicedb(self):
        for authority, email in (
            (DriveConnection.PermissionAuthority.DELEGATED_ACL, "pilot@example.com"),
            (DriveConnection.PermissionAuthority.PER_USER_OAUTH, "pilot@other.example"),
        ):
            with self.subTest(authority=authority, email=email):
                DriveConnection.objects.filter(pk=self.connection.pk).update(
                    permission_authority=authority
                )
                self.connection.refresh_from_db()
                spicedb = FakeOAuthSpiceDB()
                with self.assertRaises(OAuthViewerRelationshipError):
                    reconcile_oauth_viewer_relationships(
                        connection=self.connection,
                        user_email=email,
                        source_document_ids=(self.first.pk,),
                        spicedb=spicedb,
                    )
                self.assertEqual(spicedb.read_calls, [])


class OAuthViewerClientAndSchemaTests(TestCase):
    def test_schema_has_a_distinct_direct_relation_in_the_view_permission(self):
        schema = " ".join(schema_text().split())
        self.assertIn("relation oauth_viewer: kgm/user", schema)
        self.assertIn("permission view = oauth_viewer + reader", schema)

    def test_client_read_is_server_scoped_to_connection_relation_and_exact_user(self):
        grpc_client = Mock()
        grpc_client.ReadRelationships.return_value = []
        client = AuthzedSpiceDB(client=grpc_client)
        user_id = user_object_id(1, "pilot@example.com")

        client.read_oauth_viewer_tuples("c1_", user_id, revision="zed-token")

        request = grpc_client.ReadRelationships.call_args.args[0]
        relationship_filter = request.relationship_filter
        self.assertEqual(relationship_filter.resource_type, "kgm/document")
        self.assertEqual(relationship_filter.optional_resource_id_prefix, "c1_")
        self.assertEqual(relationship_filter.optional_relation, "oauth_viewer")
        self.assertEqual(relationship_filter.optional_subject_filter.subject_type, "kgm/user")
        self.assertEqual(relationship_filter.optional_subject_filter.optional_subject_id, user_id)
        self.assertEqual(request.consistency.at_least_as_fresh.token, "zed-token")


class PermissionAuthorityIsolationTests(TestCase):
    def setUp(self):
        self.connection = DriveConnection.objects.create(
            workspace_domain="example.com",
            root_folder_id="selected-root",
            permission_authority=DriveConnection.PermissionAuthority.PER_USER_OAUTH,
        )

    def test_legacy_sync_refuses_per_user_connection_before_drive_or_spicedb_calls(self):
        run = PermissionSyncRun.objects.create(
            connection=self.connection,
            status=PermissionSyncRun.Status.RUNNING,
        )
        drive = Mock()
        spicedb = Mock()

        with self.assertRaisesRegex(PermissionSyncError, "permission_authority_mismatch"):
            synchronize_permissions(run, drive_client=drive, spicedb=spicedb)

        run.refresh_from_db()
        self.assertEqual(run.status, PermissionSyncRun.Status.FAILED)
        self.assertEqual(run.error_code, "permission_authority_mismatch")
        drive.list_permission_resources.assert_not_called()
        spicedb.read_managed_tuples.assert_not_called()

    def test_legacy_scheduler_and_admin_endpoint_skip_per_user_connection(self):
        with patch("integrations.tasks.run_permission_sync.delay") as delay:
            result = schedule_permission_syncs.run()
        self.assertEqual(result, {"scheduled": 0, "redispatched": 0})
        delay.assert_not_called()

        admin = get_user_model().objects.create_user(
            username="admin",
            email="admin@example.com",
            is_staff=True,
        )
        client = APIClient()
        client.force_authenticate(admin)
        response = client.post("/api/permissions/sync/")
        self.assertEqual(response.status_code, 409)
        self.assertFalse(PermissionSyncRun.objects.exists())
