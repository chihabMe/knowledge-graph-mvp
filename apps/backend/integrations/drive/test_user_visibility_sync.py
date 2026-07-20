import datetime
import uuid
from unittest.mock import Mock, patch

from django.test import TestCase, override_settings
from django.utils import timezone

from authorization.client import PermissionTuple
from authorization.identifiers import document_object_id, user_object_id
from integrations.drive.user_oauth import REQUIRED_SCOPES
from integrations.drive.user_visibility_client import (
    IndexedVisibilityBatch,
    IndexedVisibilityResult,
    UserVisibilityCheckError,
)
from integrations.drive.user_visibility_sync import (
    UserVisibilitySyncError,
    queue_user_visibility_sync,
    synchronize_user_visibility,
)
from integrations.models import (
    DriveConnection,
    GoogleDriveAuthorization,
    SourceDocument,
    UserDocumentVisibility,
    UserVisibilitySyncRun,
)
from integrations.tasks import (
    run_user_visibility_sync,
    schedule_user_visibility_syncs,
    sweep_stale_user_visibility_sync_runs,
)


class FakeSpiceDB:
    def __init__(self, tuples=()):
        self.tuples = set(tuples)
        self.write_calls = []

    def read_oauth_viewer_tuples(self, resource_prefix, user_id, *, revision=None):
        return {
            item
            for item in self.tuples
            if item.resource_id.startswith(resource_prefix) and item.subject_id == user_id
        }

    def write_updates(self, *, touches, deletes):
        self.write_calls.append((set(touches), set(deletes)))
        self.tuples.difference_update(deletes)
        self.tuples.update(touches)
        return "causal-revision"


class FakeVisibilityClient:
    def __init__(self, batch, *, before_return=None):
        self.batch = batch
        self.before_return = before_return
        self.authorization_ids = []

    def check_authorization(self, authorization_id):
        self.authorization_ids.append(authorization_id)
        if self.before_return:
            self.before_return()
        return self.batch


@override_settings(
    GOOGLE_PERMISSION_AUTHORITY=DriveConnection.PermissionAuthority.PER_USER_OAUTH,
    GOOGLE_USER_OAUTH_ALLOWED_DOMAIN="example.com",
    GOOGLE_USER_VISIBILITY_MAX_DOCUMENTS=10,
    GOOGLE_USER_VISIBILITY_MAX_USERS=10,
    GOOGLE_USER_VISIBILITY_STALE_RUN_TIMEOUT_MINUTES=120,
)
class UserVisibilitySynchronizationTests(TestCase):
    def setUp(self):
        self.connection = DriveConnection.objects.create(
            workspace_domain="example.com",
            root_folder_id="selected-root",
            permission_authority=DriveConnection.PermissionAuthority.PER_USER_OAUTH,
        )
        self.authorization = self.authorization_for("pilot@example.com", "subject-1")

    def authorization_for(self, email, subject):
        return GoogleDriveAuthorization.objects.create(
            connection=self.connection,
            google_issuer="https://accounts.google.com",
            google_subject=subject,
            normalized_email=email,
            workspace_domain="example.com",
            encrypted_refresh_credential=b"encrypted-test-value",
            encryption_key_version="test-v1",
            granted_scopes=sorted(REQUIRED_SCOPES),
            connection_generation=self.connection.authorization_generation,
            status=GoogleDriveAuthorization.Status.ACTIVE,
        )

    def document(self, suffix):
        return SourceDocument.objects.create(
            connection=self.connection,
            drive_file_id=f"indexed-{suffix}",
            title="Indexed",
            mime_type="text/plain",
        )

    def sync_run(self, authorization=None):
        run = UserVisibilitySyncRun.create_for_authorization(authorization or self.authorization)
        run.status = UserVisibilitySyncRun.Status.RUNNING
        run.started_at = timezone.now()
        run.save(update_fields=["status", "started_at"])
        return run

    def batch(self, results, authorization=None):
        authorization = authorization or self.authorization
        return IndexedVisibilityBatch(
            authorization_id=authorization.pk,
            connection_generation=str(authorization.connection_generation),
            authorization_generation=str(authorization.authorization_generation),
            results=tuple(results),
        )

    def test_preinvalidates_then_commits_only_causally_verified_positive_evidence(self):
        visible = self.document("visible")
        denied = self.document("denied")
        unknown = self.document("unknown")
        old_evidence = UserDocumentVisibility.objects.create(
            authorization=self.authorization,
            source_document=visible,
            connection_generation=self.connection.authorization_generation,
            authorization_generation=self.authorization.authorization_generation,
            state=UserDocumentVisibility.State.VERIFIED_VISIBLE,
            spicedb_revision="old-revision",
            spicedb_verified_at=timezone.now(),
        )
        other_authorization = self.authorization_for("other@example.com", "subject-2")
        other_tuple = PermissionTuple(
            "kgm/document",
            document_object_id(self.connection.pk, denied.pk),
            "oauth_viewer",
            "kgm/user",
            user_object_id(self.connection.pk, other_authorization.normalized_email),
        )
        spicedb = FakeSpiceDB({other_tuple})

        def assert_preinvalidated():
            old_evidence.refresh_from_db()
            self.assertEqual(old_evidence.state, UserDocumentVisibility.State.UNKNOWN)
            self.assertEqual(old_evidence.spicedb_revision, "")
            self.assertIsNone(old_evidence.spicedb_verified_at)

        client = FakeVisibilityClient(
            self.batch(
                [
                    IndexedVisibilityResult(
                        visible.pk,
                        UserDocumentVisibility.State.VERIFIED_VISIBLE,
                        "",
                    ),
                    IndexedVisibilityResult(
                        denied.pk,
                        UserDocumentVisibility.State.DENIED,
                        "inaccessible",
                    ),
                    IndexedVisibilityResult(
                        unknown.pk,
                        UserDocumentVisibility.State.UNKNOWN,
                        "transient_failure",
                    ),
                ]
            ),
            before_return=assert_preinvalidated,
        )

        completed = synchronize_user_visibility(
            self.sync_run(),
            visibility_client=client,
            spicedb=spicedb,
        )

        evidence = {
            item.source_document_id: item
            for item in UserDocumentVisibility.objects.filter(authorization=self.authorization)
        }
        self.assertEqual(completed.status, UserVisibilitySyncRun.Status.PARTIAL)
        self.assertEqual(completed.documents_verified_visible, 1)
        self.assertEqual(completed.documents_denied, 1)
        self.assertEqual(completed.documents_unknown, 1)
        self.assertEqual(
            evidence[visible.pk].state,
            UserDocumentVisibility.State.VERIFIED_VISIBLE,
        )
        self.assertEqual(evidence[visible.pk].spicedb_revision, "causal-revision")
        self.assertIsNotNone(evidence[visible.pk].spicedb_verified_at)
        self.assertEqual(evidence[denied.pk].spicedb_revision, "")
        self.assertIsNone(evidence[denied.pk].spicedb_verified_at)
        self.assertEqual(evidence[unknown.pk].spicedb_revision, "")
        self.assertIsNone(evidence[unknown.pk].spicedb_verified_at)
        self.assertIn(other_tuple, spicedb.tuples)
        pilot_tuples = {
            item for item in spicedb.tuples if item.subject_id != other_tuple.subject_id
        }
        self.assertEqual(len(pilot_tuples), 1)
        self.authorization.refresh_from_db()
        self.assertIsNone(self.authorization.last_successful_visibility_sync_at)

    def test_generation_change_after_remote_check_blocks_evidence_commit(self):
        document = self.document("visible")
        run = self.sync_run()

        def rotate_generation():
            GoogleDriveAuthorization.objects.filter(pk=self.authorization.pk).update(
                authorization_generation=uuid.uuid4()
            )

        client = FakeVisibilityClient(
            self.batch(
                [
                    IndexedVisibilityResult(
                        document.pk,
                        UserDocumentVisibility.State.VERIFIED_VISIBLE,
                        "",
                    )
                ]
            ),
            before_return=rotate_generation,
        )

        with self.assertRaisesRegex(UserVisibilitySyncError, "authorization_unavailable"):
            synchronize_user_visibility(run, visibility_client=client, spicedb=FakeSpiceDB())

        run.refresh_from_db()
        evidence = UserDocumentVisibility.objects.get(
            authorization=self.authorization,
            source_document=document,
        )
        self.assertEqual(run.status, UserVisibilitySyncRun.Status.FAILED)
        self.assertEqual(evidence.state, UserDocumentVisibility.State.UNKNOWN)
        self.assertIsNone(evidence.spicedb_verified_at)

    @override_settings(GOOGLE_USER_VISIBILITY_MAX_DOCUMENTS=1)
    def test_document_cap_preinvalidates_old_evidence_before_failing(self):
        first = self.document("one")
        self.document("two")
        evidence = UserDocumentVisibility.objects.create(
            authorization=self.authorization,
            source_document=first,
            connection_generation=self.connection.authorization_generation,
            authorization_generation=self.authorization.authorization_generation,
            state=UserDocumentVisibility.State.VERIFIED_VISIBLE,
            spicedb_revision="old-revision",
            spicedb_verified_at=timezone.now(),
        )
        client = Mock()

        with self.assertRaisesRegex(UserVisibilitySyncError, "document_cap_exceeded"):
            synchronize_user_visibility(self.sync_run(), visibility_client=client)

        evidence.refresh_from_db()
        self.assertEqual(evidence.state, UserDocumentVisibility.State.UNKNOWN)
        self.assertIsNone(evidence.spicedb_verified_at)
        client.check_authorization.assert_not_called()

    def test_manual_queue_resolves_only_the_trusted_email_and_deduplicates(self):
        dispatch = Mock()

        first = queue_user_visibility_sync(
            user_email="PILOT@example.com",
            dispatch=dispatch,
        )

        dispatch.assert_called_once_with(first.pk)
        self.assertEqual(first.authorization_id, self.authorization.pk)
        first.status = UserVisibilitySyncRun.Status.RUNNING
        first.save(update_fields=["status"])
        dispatch.reset_mock()

        second = queue_user_visibility_sync(
            user_email="pilot@example.com",
            dispatch=dispatch,
        )

        self.assertEqual(second.pk, first.pk)
        dispatch.assert_not_called()

    def test_invalid_grant_wipes_credential_and_requires_reauthorization(self):
        document = self.document("visible")
        UserDocumentVisibility.objects.create(
            authorization=self.authorization,
            source_document=document,
            connection_generation=self.connection.authorization_generation,
            authorization_generation=self.authorization.authorization_generation,
            state=UserDocumentVisibility.State.VERIFIED_VISIBLE,
            spicedb_revision="old-revision",
            spicedb_verified_at=timezone.now(),
        )
        old_generation = self.authorization.authorization_generation
        client = Mock()
        client.check_authorization.side_effect = UserVisibilityCheckError(
            "credential_invalid_grant"
        )

        with patch("integrations.drive.user_oauth.delete_oauth_viewer_relationships") as cleanup:
            with self.assertRaisesRegex(UserVisibilitySyncError, "credential_invalid_grant"):
                synchronize_user_visibility(self.sync_run(), visibility_client=client)

        self.authorization.refresh_from_db()
        self.assertEqual(
            self.authorization.status,
            GoogleDriveAuthorization.Status.REFRESH_FAILED,
        )
        self.assertEqual(bytes(self.authorization.encrypted_refresh_credential), b"")
        self.assertEqual(self.authorization.encryption_key_version, "")
        self.assertNotEqual(self.authorization.authorization_generation, old_generation)
        self.assertFalse(UserDocumentVisibility.objects.exists())
        cleanup.assert_called_once_with(
            connection=self.connection,
            user_email="pilot@example.com",
        )


@override_settings(
    GOOGLE_PERMISSION_AUTHORITY=DriveConnection.PermissionAuthority.PER_USER_OAUTH,
    GOOGLE_USER_OAUTH_ALLOWED_DOMAIN="example.com",
    GOOGLE_USER_VISIBILITY_MAX_USERS=10,
    GOOGLE_USER_VISIBILITY_STALE_RUN_TIMEOUT_MINUTES=120,
)
class UserVisibilityTaskTests(TestCase):
    def setUp(self):
        self.connection = DriveConnection.objects.create(
            workspace_domain="example.com",
            root_folder_id="selected-root",
            permission_authority=DriveConnection.PermissionAuthority.PER_USER_OAUTH,
        )
        self.authorization = GoogleDriveAuthorization.objects.create(
            connection=self.connection,
            google_issuer="https://accounts.google.com",
            google_subject="subject-1",
            normalized_email="pilot@example.com",
            workspace_domain="example.com",
            encrypted_refresh_credential=b"encrypted-test-value",
            encryption_key_version="test-v1",
            granted_scopes=sorted(REQUIRED_SCOPES),
            connection_generation=self.connection.authorization_generation,
            status=GoogleDriveAuthorization.Status.ACTIVE,
        )

    def test_duplicate_completed_task_delivery_is_idempotent(self):
        run = UserVisibilitySyncRun.create_for_authorization(self.authorization)
        run.status = UserVisibilitySyncRun.Status.SUCCEEDED
        run.save(update_fields=["status"])
        with patch("integrations.tasks.synchronize_user_visibility") as synchronize:
            result = run_user_visibility_sync.run(run.pk)
        self.assertEqual(result, {"run_id": run.pk, "status": "succeeded"})
        synchronize.assert_not_called()

    def test_beat_registers_refresh_and_stale_run_recovery(self):
        from django.conf import settings

        tasks = {entry["task"] for entry in settings.CELERY_BEAT_SCHEDULE.values()}
        self.assertIn("integrations.schedule_user_visibility_syncs", tasks)
        self.assertIn("integrations.sweep_stale_user_visibility_sync_runs", tasks)
        self.assertLess(
            settings.GOOGLE_USER_VISIBILITY_SYNC_INTERVAL_SECONDS,
            settings.GOOGLE_USER_VISIBILITY_MAX_AGE_SECONDS,
        )

    def test_retryable_failure_returns_durable_run_to_queue(self):
        run = UserVisibilitySyncRun.create_for_authorization(self.authorization)
        with (
            patch(
                "integrations.tasks.synchronize_user_visibility",
                side_effect=UserVisibilitySyncError("spicedb_operation_failed"),
            ),
            patch.object(
                run_user_visibility_sync,
                "retry",
                side_effect=RuntimeError("retry"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "retry"):
                run_user_visibility_sync.run(run.pk)

        run.refresh_from_db()
        self.assertEqual(run.status, UserVisibilitySyncRun.Status.QUEUED)
        self.assertEqual(run.error_code, "")

    def test_scheduler_creates_and_redispatches_only_one_user_scoped_run(self):
        with patch("integrations.tasks.run_user_visibility_sync.delay") as delay:
            first = schedule_user_visibility_syncs.run()
        run = UserVisibilitySyncRun.objects.get(authorization=self.authorization)
        self.assertEqual(
            first,
            {"scheduled": 1, "redispatched": 0, "skipped_user_cap": 0},
        )
        delay.assert_called_once_with(run.pk)

        with patch("integrations.tasks.run_user_visibility_sync.delay") as delay:
            second = schedule_user_visibility_syncs.run()
        self.assertEqual(
            second,
            {"scheduled": 0, "redispatched": 1, "skipped_user_cap": 0},
        )
        delay.assert_called_once_with(run.pk)
        self.assertEqual(UserVisibilitySyncRun.objects.count(), 1)

    @override_settings(GOOGLE_USER_VISIBILITY_MAX_USERS=1)
    def test_scheduler_user_cap_invalidates_evidence_without_remote_work(self):
        second = GoogleDriveAuthorization.objects.create(
            connection=self.connection,
            google_issuer="https://accounts.google.com",
            google_subject="subject-2",
            normalized_email="second@example.com",
            workspace_domain="example.com",
            encrypted_refresh_credential=b"encrypted-test-value",
            encryption_key_version="test-v1",
            granted_scopes=sorted(REQUIRED_SCOPES),
            connection_generation=self.connection.authorization_generation,
            status=GoogleDriveAuthorization.Status.ACTIVE,
        )
        document = SourceDocument.objects.create(
            connection=self.connection,
            drive_file_id="indexed-file",
            title="Indexed",
            mime_type="text/plain",
        )
        evidence = UserDocumentVisibility.objects.create(
            authorization=second,
            source_document=document,
            connection_generation=self.connection.authorization_generation,
            authorization_generation=second.authorization_generation,
            state=UserDocumentVisibility.State.VERIFIED_VISIBLE,
            spicedb_revision="old-revision",
            spicedb_verified_at=timezone.now(),
        )

        with patch("integrations.tasks.run_user_visibility_sync.delay") as delay:
            result = schedule_user_visibility_syncs.run()

        evidence.refresh_from_db()
        self.assertEqual(
            result,
            {"scheduled": 0, "redispatched": 0, "skipped_user_cap": 1},
        )
        delay.assert_not_called()
        self.assertEqual(evidence.state, UserDocumentVisibility.State.UNKNOWN)
        self.assertIsNone(evidence.spicedb_verified_at)

    def test_stale_run_sweep_invalidates_previous_positive_evidence(self):
        document = SourceDocument.objects.create(
            connection=self.connection,
            drive_file_id="indexed-file",
            title="Indexed",
            mime_type="text/plain",
        )
        evidence = UserDocumentVisibility.objects.create(
            authorization=self.authorization,
            source_document=document,
            connection_generation=self.connection.authorization_generation,
            authorization_generation=self.authorization.authorization_generation,
            state=UserDocumentVisibility.State.VERIFIED_VISIBLE,
            spicedb_revision="old-revision",
            spicedb_verified_at=timezone.now(),
        )
        run = UserVisibilitySyncRun.create_for_authorization(self.authorization)
        run.status = UserVisibilitySyncRun.Status.RUNNING
        run.started_at = timezone.now() - datetime.timedelta(hours=3)
        run.save(update_fields=["status", "started_at"])

        result = sweep_stale_user_visibility_sync_runs.run()

        run.refresh_from_db()
        evidence.refresh_from_db()
        self.assertEqual(result, {"swept": 1})
        self.assertEqual(run.status, UserVisibilitySyncRun.Status.FAILED)
        self.assertEqual(run.error_code, "stale_run_timeout")
        self.assertEqual(evidence.state, UserDocumentVisibility.State.UNKNOWN)
        self.assertIsNone(evidence.spicedb_verified_at)
