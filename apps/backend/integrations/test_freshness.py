import datetime
import uuid
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone

from authorization.lookup import fresh_authorized_documents
from integrations.drive.user_oauth import REQUIRED_SCOPES
from integrations.freshness import (
    FRESHNESS_HEARTBEAT_NAME,
    STATUS_ERROR,
    STATUS_OK,
    STATUS_WARN,
    build_freshness_report,
)
from integrations.models import (
    DriveConnection,
    GoogleDriveAuthorization,
    PermissionSyncRun,
    SchedulerHeartbeat,
    SourceDocument,
    UserDocumentVisibility,
    UserVisibilitySyncRun,
)
from integrations.tasks import monitor_freshness


@override_settings(
    GOOGLE_PERMISSION_AUTHORITY=DriveConnection.PermissionAuthority.PER_USER_OAUTH,
    GOOGLE_USER_OAUTH_ALLOWED_DOMAIN="example.com",
    GOOGLE_USER_VISIBILITY_MAX_AGE_SECONDS=600,
    FRESHNESS_WARN_REMAINING_FRACTION=0.4,
    FRESHNESS_HEARTBEAT_MAX_AGE_SECONDS=180,
)
class PerUserFreshnessTests(TestCase):
    def setUp(self):
        self.now = timezone.now().replace(microsecond=0)
        self.connection = DriveConnection.objects.create(
            workspace_domain="example.com",
            root_folder_id="root",
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
            last_successful_visibility_sync_at=self.now - datetime.timedelta(seconds=100),
        )
        SchedulerHeartbeat.objects.create(
            name=FRESHNESS_HEARTBEAT_NAME,
            last_tick_at=self.now - datetime.timedelta(seconds=10),
        )

    def test_healthy_authorization_reports_identity_free_worst_case_ages(self):
        report = build_freshness_report(now=self.now)

        self.assertEqual(report.status, STATUS_OK)
        self.assertEqual(report.active_connections, 1)
        self.assertEqual(report.active_authorizations, 1)
        self.assertEqual(report.sync_targets, 1)
        self.assertEqual(report.heartbeat_age_seconds, 10)
        self.assertEqual(report.worst_last_success_age_seconds, 100)
        self.assertEqual(report.worst_remaining_evidence_seconds, 500)
        payload = str(report.as_payload()).lower()
        self.assertNotIn("pilot@example.com", payload)
        self.assertNotIn("subject-1", payload)
        self.assertNotIn("root", payload)

    def test_pre_expiry_and_expired_targets_degrade_before_and_at_deadline(self):
        self.authorization.last_successful_visibility_sync_at = self.now - datetime.timedelta(
            seconds=500
        )
        self.authorization.save(update_fields=["last_successful_visibility_sync_at"])

        warning = build_freshness_report(now=self.now)
        self.assertEqual(warning.status, STATUS_WARN)
        self.assertEqual(warning.targets_expiring_soon, 1)
        self.assertEqual(warning.worst_remaining_evidence_seconds, 100)

        self.authorization.last_successful_visibility_sync_at = self.now - datetime.timedelta(
            seconds=601
        )
        self.authorization.save(update_fields=["last_successful_visibility_sync_at"])
        expired = build_freshness_report(now=self.now)
        self.assertEqual(expired.status, STATUS_ERROR)
        self.assertEqual(expired.targets_expired, 1)
        self.assertEqual(expired.worst_remaining_evidence_seconds, 0)

    def test_never_successful_target_and_stale_heartbeat_are_errors(self):
        self.authorization.last_successful_visibility_sync_at = None
        self.authorization.save(update_fields=["last_successful_visibility_sync_at"])
        report = build_freshness_report(now=self.now)
        self.assertEqual(report.status, STATUS_ERROR)
        self.assertEqual(report.targets_never_succeeded, 1)

        self.authorization.last_successful_visibility_sync_at = self.now
        self.authorization.save(update_fields=["last_successful_visibility_sync_at"])
        SchedulerHeartbeat.objects.update(last_tick_at=self.now - datetime.timedelta(seconds=181))
        report = build_freshness_report(now=self.now)
        self.assertEqual(report.status, STATUS_ERROR)
        self.assertEqual(report.heartbeat_age_seconds, 181)

    def test_delayed_scheduler_alerts_while_expired_evidence_still_denies(self):
        SchedulerHeartbeat.objects.update(last_tick_at=self.now - datetime.timedelta(seconds=181))
        self.authorization.last_successful_visibility_sync_at = self.now - datetime.timedelta(
            seconds=601
        )
        self.authorization.save(update_fields=["last_successful_visibility_sync_at"])
        document = SourceDocument.objects.create(
            connection=self.connection,
            drive_file_id="file-expired",
            title="Private title",
            mime_type="text/plain",
            source_permissions_version="generation",
            active_in_scope=True,
            retrieval_eligible=True,
        )
        UserDocumentVisibility.objects.create(
            authorization=self.authorization,
            source_document=document,
            connection_generation=self.connection.authorization_generation,
            authorization_generation=self.authorization.authorization_generation,
            state=UserDocumentVisibility.State.VERIFIED_VISIBLE,
            checked_at=self.now - datetime.timedelta(seconds=601),
            spicedb_revision="stale-revision",
            spicedb_verified_at=self.now - datetime.timedelta(seconds=601),
        )

        report = build_freshness_report(now=self.now)

        self.assertEqual(report.status, STATUS_ERROR)
        self.assertEqual(report.expired_evidence_documents, 1)
        self.assertEqual(
            fresh_authorized_documents("pilot@example.com", {document.pk}),
            {},
        )

    def test_run_backlog_duration_results_and_evidence_age_are_aggregated(self):
        queued = UserVisibilitySyncRun.create_for_authorization(self.authorization)
        UserVisibilitySyncRun.objects.filter(pk=queued.pk).update(
            created_at=self.now - datetime.timedelta(seconds=120)
        )
        running = UserVisibilitySyncRun.create_for_authorization(self.authorization)
        running.status = UserVisibilitySyncRun.Status.RUNNING
        running.started_at = self.now - datetime.timedelta(seconds=90)
        running.save(update_fields=["status", "started_at"])
        latest = UserVisibilitySyncRun.create_for_authorization(self.authorization)
        latest.status = UserVisibilitySyncRun.Status.SUCCEEDED
        latest.started_at = self.now - datetime.timedelta(seconds=40)
        latest.finished_at = self.now - datetime.timedelta(seconds=10)
        latest.documents_denied = 2
        latest.save(
            update_fields=[
                "status",
                "started_at",
                "finished_at",
                "documents_denied",
            ]
        )
        document = SourceDocument.objects.create(
            connection=self.connection,
            drive_file_id="file-1",
            title="Private title",
            mime_type="text/plain",
        )
        UserDocumentVisibility.objects.create(
            authorization=self.authorization,
            source_document=document,
            connection_generation=self.connection.authorization_generation,
            authorization_generation=self.authorization.authorization_generation,
            state=UserDocumentVisibility.State.VERIFIED_VISIBLE,
            checked_at=self.now - datetime.timedelta(seconds=200),
            spicedb_revision="revision",
            spicedb_verified_at=self.now - datetime.timedelta(seconds=200),
        )
        unknown_document = SourceDocument.objects.create(
            connection=self.connection,
            drive_file_id="file-2",
            title="Unknown title",
            mime_type="text/plain",
        )
        UserDocumentVisibility.objects.create(
            authorization=self.authorization,
            source_document=unknown_document,
            connection_generation=self.connection.authorization_generation,
            authorization_generation=self.authorization.authorization_generation,
            state=UserDocumentVisibility.State.UNKNOWN,
        )

        report = build_freshness_report(now=self.now)

        self.assertEqual(report.status, STATUS_WARN)
        self.assertEqual(report.queued_runs, 1)
        self.assertEqual(report.oldest_queued_run_age_seconds, 120)
        self.assertEqual(report.running_runs, 1)
        self.assertEqual(report.longest_running_run_age_seconds, 90)
        self.assertEqual(report.worst_recent_run_duration_seconds, 30)
        self.assertEqual(report.latest_denied_documents, 2)
        self.assertEqual(report.unknown_documents, 1)
        self.assertEqual(report.worst_remaining_evidence_seconds, 400)

    def test_latest_failure_streak_warns_and_old_generations_are_ignored(self):
        succeeded = UserVisibilitySyncRun.create_for_authorization(self.authorization)
        succeeded.status = UserVisibilitySyncRun.Status.SUCCEEDED
        succeeded.save(update_fields=["status"])
        for _ in range(2):
            failed = UserVisibilitySyncRun.create_for_authorization(self.authorization)
            failed.status = UserVisibilitySyncRun.Status.FAILED
            failed.save(update_fields=["status"])
        stale = UserVisibilitySyncRun.create_for_authorization(self.authorization)
        stale.connection_generation = uuid.uuid4()
        stale.status = UserVisibilitySyncRun.Status.RUNNING
        stale.started_at = self.now - datetime.timedelta(days=1)
        stale.save(update_fields=["connection_generation", "status", "started_at"])

        report = build_freshness_report(now=self.now)

        self.assertEqual(report.status, STATUS_WARN)
        self.assertEqual(report.max_consecutive_failures, 2)
        self.assertEqual(report.latest_error_runs, 1)
        self.assertEqual(report.running_runs, 0)

    def test_monitor_task_updates_heartbeat_and_returns_safe_payload(self):
        SchedulerHeartbeat.objects.all().delete()

        result = monitor_freshness.run()

        self.assertTrue(SchedulerHeartbeat.objects.filter(name=FRESHNESS_HEARTBEAT_NAME).exists())
        self.assertEqual(result["status"], STATUS_OK)
        self.assertNotIn("pilot@example.com", str(result).lower())

    def test_monitor_task_is_registered_with_celery_beat(self):
        from django.conf import settings

        tasks = {entry["task"] for entry in settings.CELERY_BEAT_SCHEDULE.values()}
        self.assertIn("integrations.monitor_freshness", tasks)


@override_settings(
    GOOGLE_PERMISSION_AUTHORITY=DriveConnection.PermissionAuthority.DELEGATED_ACL,
    PERMISSION_VERIFICATION_MAX_AGE_SECONDS=600,
    FRESHNESS_WARN_REMAINING_FRACTION=0.4,
    FRESHNESS_HEARTBEAT_MAX_AGE_SECONDS=180,
)
class DelegatedFreshnessTests(TestCase):
    def setUp(self):
        self.now = timezone.now().replace(microsecond=0)
        self.connection = DriveConnection.objects.create(
            workspace_domain="example.com",
            root_folder_id="root",
            permission_authority=DriveConnection.PermissionAuthority.DELEGATED_ACL,
        )
        SchedulerHeartbeat.objects.create(
            name=FRESHNESS_HEARTBEAT_NAME,
            last_tick_at=self.now,
        )
        self.run = PermissionSyncRun.objects.create(
            connection=self.connection,
            status=PermissionSyncRun.Status.SUCCEEDED,
            started_at=self.now - datetime.timedelta(seconds=130),
            finished_at=self.now - datetime.timedelta(seconds=100),
        )
        self.document = SourceDocument.objects.create(
            connection=self.connection,
            drive_file_id="file-1",
            title="Private title",
            mime_type="text/plain",
            source_permissions_version="version",
            spicedb_permissions_version="version",
            spicedb_verified_at=self.now - datetime.timedelta(seconds=100),
            active_in_scope=True,
            retrieval_eligible=True,
        )

    def test_delegated_connection_uses_run_and_document_evidence(self):
        report = build_freshness_report(now=self.now)

        self.assertEqual(report.status, STATUS_OK)
        self.assertEqual(report.active_connections, 1)
        self.assertEqual(report.active_authorizations, 0)
        self.assertEqual(report.sync_targets, 1)
        self.assertEqual(report.worst_last_success_age_seconds, 100)
        self.assertEqual(report.worst_recent_run_duration_seconds, 30)
        self.assertEqual(report.worst_remaining_evidence_seconds, 500)

    def test_expired_delegated_document_evidence_is_an_error(self):
        SourceDocument.objects.filter(pk=self.document.pk).update(
            spicedb_verified_at=self.now - datetime.timedelta(seconds=601)
        )

        report = build_freshness_report(now=self.now)

        self.assertEqual(report.status, STATUS_ERROR)
        self.assertEqual(report.expired_evidence_documents, 1)

    def test_latest_delegated_failure_warns_before_evidence_expires(self):
        PermissionSyncRun.objects.create(
            connection=self.connection,
            status=PermissionSyncRun.Status.FAILED,
            error_code="safe_code",
            finished_at=self.now,
        )

        report = build_freshness_report(now=self.now)

        self.assertEqual(report.status, STATUS_WARN)
        self.assertEqual(report.latest_error_runs, 1)
        self.assertEqual(report.max_consecutive_failures, 1)


class FreshnessTaskFailureTests(TestCase):
    @patch("integrations.tasks.build_freshness_report", side_effect=RuntimeError("private"))
    def test_aggregation_failure_still_records_the_task_tick(self, build_report):
        with self.assertRaisesRegex(RuntimeError, "private"):
            monitor_freshness.run()

        self.assertTrue(SchedulerHeartbeat.objects.filter(name=FRESHNESS_HEARTBEAT_NAME).exists())
        build_report.assert_called_once_with()
