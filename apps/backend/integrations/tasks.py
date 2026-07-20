import datetime
import logging

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.db.models import F, Max, Q
from django.utils import timezone

from authorization.client import SPICEDB_TRANSIENT_ERRORS
from authorization.sync import synchronize_permissions
from graph.pipeline import extract_document_to_graph, get_retryable_extraction_exceptions
from integrations.drive.export import export_file_content
from integrations.drive.google_client import (
    DRIVE_API_ERRORS,
    GoogleDriveMetadataClient,
    build_drive_service,
)
from integrations.drive.sync import sync_drive_metadata
from integrations.drive.user_oauth import REQUIRED_SCOPES
from integrations.drive.user_visibility_sync import (
    UserVisibilitySyncError,
    invalidate_authorization_evidence,
    synchronize_user_visibility,
)
from integrations.freshness import (
    FRESHNESS_HEARTBEAT_NAME,
    STATUS_ERROR,
    STATUS_WARN,
    build_freshness_report,
)
from integrations.models import (
    DriveConnection,
    DriveSyncRun,
    GoogleDriveAuthorization,
    PermissionSyncRun,
    SchedulerHeartbeat,
    SourceDocument,
    SourceDocumentContent,
    UserVisibilitySyncRun,
)

logger = logging.getLogger(__name__)

_UNSET = object()


def _retry_countdown(retries: int) -> int:
    """Exponential backoff shared by every retrying task, capped at 60s."""
    return min(2 ** (retries + 1), 60)


@shared_task(
    bind=True,
    name="integrations.run_permission_sync",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=3,
)
def run_permission_sync(self, run_id: int) -> dict[str, int | str]:
    """Reconcile one permission run using only its durable primary key."""
    run = PermissionSyncRun.objects.select_related("connection").get(pk=run_id)
    if run.status not in {PermissionSyncRun.Status.QUEUED, PermissionSyncRun.Status.RUNNING}:
        return {"run_id": run.pk, "status": run.status}
    lock_key = f"permission-sync:connection:{run.connection_id}"
    lock_token = f"run:{run.pk}"
    # The lock must outlive any legitimate run, so it shares the stale-run
    # timeout: a scan longer than a fixed 900s TTL would lose the lock
    # mid-flight and collide with the next beat-scheduled run.
    lock_ttl_seconds = settings.PERMISSION_SYNC_STALE_RUN_TIMEOUT_MINUTES * 60
    if not cache.add(lock_key, lock_token, timeout=lock_ttl_seconds):
        raise self.retry(countdown=_retry_countdown(self.request.retries))
    try:
        claimed = PermissionSyncRun.objects.filter(
            pk=run_id, status=PermissionSyncRun.Status.QUEUED
        ).update(status=PermissionSyncRun.Status.RUNNING, started_at=timezone.now())
        run.refresh_from_db()
        if not claimed and run.status != PermissionSyncRun.Status.RUNNING:
            return {"run_id": run.pk, "status": run.status}
        try:
            run = synchronize_permissions(run, drive_client=GoogleDriveMetadataClient())
        except (*DRIVE_API_ERRORS, *SPICEDB_TRANSIENT_ERRORS) as exc:
            if self.request.retries < self.max_retries:
                PermissionSyncRun.objects.filter(pk=run.pk).update(
                    status=PermissionSyncRun.Status.QUEUED,
                    error_code="",
                    finished_at=None,
                )
                raise self.retry(exc=exc, countdown=_retry_countdown(self.request.retries)) from exc
            raise
        return {"run_id": run.pk, "status": run.status}
    finally:
        if cache.get(lock_key) == lock_token:
            cache.delete(lock_key)


_RETRYABLE_USER_VISIBILITY_ERRORS = frozenset(
    {
        "credential_refresh_failed",
        "relationship_verification_mismatch",
        "spicedb_operation_failed",
        "visibility_sync_failed",
    }
)


@shared_task(
    bind=True,
    name="integrations.run_user_visibility_sync",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=3,
)
def run_user_visibility_sync(self, run_id: int) -> dict[str, int | str]:
    """Refresh one durable authorization using no task-supplied identity or file IDs."""
    run = UserVisibilitySyncRun.objects.select_related("connection", "authorization").get(pk=run_id)
    if run.status not in {
        UserVisibilitySyncRun.Status.QUEUED,
        UserVisibilitySyncRun.Status.RUNNING,
    }:
        return {"run_id": run.pk, "status": run.status}
    lock_key = f"user-visibility-sync:authorization:{run.authorization_id}"
    lock_token = f"run:{run.pk}"
    lock_ttl_seconds = settings.GOOGLE_USER_VISIBILITY_STALE_RUN_TIMEOUT_MINUTES * 60
    if not cache.add(lock_key, lock_token, timeout=lock_ttl_seconds):
        raise self.retry(countdown=_retry_countdown(self.request.retries))
    try:
        claimed = UserVisibilitySyncRun.objects.filter(
            pk=run_id,
            status=UserVisibilitySyncRun.Status.QUEUED,
        ).update(
            status=UserVisibilitySyncRun.Status.RUNNING,
            started_at=timezone.now(),
            finished_at=None,
        )
        run.refresh_from_db()
        if not claimed and run.status != UserVisibilitySyncRun.Status.RUNNING:
            return {"run_id": run.pk, "status": run.status}
        try:
            run = synchronize_user_visibility(run)
        except UserVisibilitySyncError as exc:
            if (
                str(exc) in _RETRYABLE_USER_VISIBILITY_ERRORS
                and self.request.retries < self.max_retries
            ):
                UserVisibilitySyncRun.objects.filter(pk=run.pk).update(
                    status=UserVisibilitySyncRun.Status.QUEUED,
                    error_code="",
                    finished_at=None,
                )
                raise self.retry(
                    exc=exc,
                    countdown=_retry_countdown(self.request.retries),
                ) from exc
            raise
        return {"run_id": run.pk, "status": run.status}
    finally:
        if cache.get(lock_key) == lock_token:
            cache.delete(lock_key)


def _set_graph_extraction_state(
    source_document_id: int,
    status: str,
    *,
    expected_content_hash: str | None = None,
    error_summary: str = "",
    started_at=_UNSET,
    finished_at=_UNSET,
) -> bool:
    """Persist safe extraction-job state without ever retaining error text."""
    updates = {
        "graph_extraction_status": status,
        "graph_extraction_error_summary": error_summary,
        "updated_at": timezone.now(),
    }
    if status == SourceDocument.GraphExtractionStatus.FAILED:
        # Every FAILED transition spends one unit of the sync-requeue budget
        # (GRAPH_EXTRACTION_MAX_SYNC_ATTEMPTS); new content resets it.
        updates["graph_extraction_attempts"] = F("graph_extraction_attempts") + 1
    if started_at is not _UNSET:
        updates["graph_extraction_started_at"] = started_at
    if finished_at is not _UNSET:
        updates["graph_extraction_finished_at"] = finished_at
    documents = SourceDocument.objects.filter(pk=source_document_id)
    if expected_content_hash is not None:
        documents = documents.filter(content_hash=expected_content_hash)
    return bool(documents.update(**updates))


def _mark_per_user_content_ready(
    source_document_id: int,
    expected_content_hash: str,
) -> None:
    """Open only the coarse content gate; user evidence still grants access."""
    if settings.GOOGLE_PERMISSION_AUTHORITY != DriveConnection.PermissionAuthority.PER_USER_OAUTH:
        return
    SourceDocument.objects.filter(
        pk=source_document_id,
        content_hash=expected_content_hash,
        content__content_hash=expected_content_hash,
        active_in_scope=True,
        exclusion_reason="",
        graph_extraction_status=SourceDocument.GraphExtractionStatus.SUCCEEDED,
        connection__enabled=True,
        connection__permission_authority=DriveConnection.PermissionAuthority.PER_USER_OAUTH,
    ).exclude(source_permissions_version="").update(
        retrieval_eligible=True,
        updated_at=timezone.now(),
    )


def _current_content_hash(source_document_id: int) -> str:
    return (
        SourceDocumentContent.objects.filter(source_document_id=source_document_id)
        .values_list("content_hash", flat=True)
        .first()
        or ""
    )


def _stale_content_result(source_document_id: int) -> dict[str, int | str]:
    return {"source_document_id": source_document_id, "status": "skipped_stale_content_version"}


@shared_task(name="integrations.run_drive_sync")
def run_drive_sync(run_id: int) -> dict[str, int | str]:
    """Execute a pre-created Drive sync run: metadata, permissions, content.

    Takes a primitive id, never a model instance. The run row is the audit
    record the API view created before dispatch; sync_drive_metadata updates
    its status/counters and stores only an exception class name on failure.
    """
    # Atomic claim: a read-then-check guard would let two workers holding
    # duplicate deliveries both see QUEUED and both execute. Only the worker
    # whose UPDATE actually transitions the row proceeds.
    claimed = DriveSyncRun.objects.filter(pk=run_id, status=DriveSyncRun.Status.QUEUED).update(
        status=DriveSyncRun.Status.RUNNING, started_at=timezone.now()
    )
    run = DriveSyncRun.objects.select_related("connection").get(pk=run_id)
    if not claimed:
        return {"run_id": run.pk, "status": run.status}
    connection = run.connection

    try:
        # One authenticated service shared by the metadata walk and the exporter.
        service = build_drive_service(connection)
    except Exception as exc:
        # Without this, a bad credential file would leave the audit row
        # stuck in QUEUED forever. Class name only, as everywhere else.
        run.status = DriveSyncRun.Status.FAILED
        run.error_summary = f"{type(exc).__module__}.{type(exc).__name__}"[:512]
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "error_summary", "finished_at"])
        raise
    client = GoogleDriveMetadataClient(service=service)

    def content_exporter(file_metadata):
        return export_file_content(
            service,
            drive_file_id=file_metadata.drive_file_id,
            mime_type=file_metadata.mime_type,
        )

    run = sync_drive_metadata(
        connection=connection,
        client=client,
        run=run,
        content_exporter=content_exporter,
        queue_extraction=queue_document_extraction.delay,
    )
    return {"run_id": run.pk, "status": run.status}


@shared_task(name="integrations.schedule_permission_syncs")
def schedule_permission_syncs() -> dict[str, int]:
    """Enqueue a periodic permission run per configured connection.

    Group membership changes never alter a document's own ACL hash, so the
    drive-sync preserve gate cannot see them; only a periodic reconciliation
    deletes stale SpiceDB member tuples. This beat task is the healthy refresh
    cadence; query-time verification expiry is the hard bound when runs fail.
    """
    scheduled = 0
    redispatched = 0
    for connection in DriveConnection.objects.filter(
        enabled=True,
        permission_authority=DriveConnection.PermissionAuthority.DELEGATED_ACL,
    ).order_by("pk"):
        if not connection.effective_root_id:
            continue
        active = PermissionSyncRun.objects.filter(
            connection=connection,
            status__in=[PermissionSyncRun.Status.QUEUED, PermissionSyncRun.Status.RUNNING],
        )
        if active.filter(status=PermissionSyncRun.Status.RUNNING).exists():
            continue
        queued = list(active.order_by("pk"))
        if queued:
            # A QUEUED run whose task message is gone (lock-contention
            # retries exhausted against a long scan, broker loss) would
            # otherwise block this connection's scheduled syncs forever:
            # nothing re-dispatches it and the sweeper only covers RUNNING.
            # Re-sending is idempotent -- the task re-claims through the
            # status CAS and the per-connection lock.
            for run in queued:
                run_permission_sync.delay(run.pk)
                redispatched += 1
            continue
        run = PermissionSyncRun.create_for_connection(connection)
        run_permission_sync.delay(run.pk)
        scheduled += 1
    return {"scheduled": scheduled, "redispatched": redispatched}


def _user_authorization_ready(authorization: GoogleDriveAuthorization) -> bool:
    return bool(
        authorization.status == GoogleDriveAuthorization.Status.ACTIVE
        and authorization.connection_generation == authorization.connection.authorization_generation
        and REQUIRED_SCOPES.issubset(set(authorization.granted_scopes))
        and bytes(authorization.encrypted_refresh_credential)
        and authorization.encryption_key_version
    )


@shared_task(name="integrations.schedule_user_visibility_syncs")
def schedule_user_visibility_syncs() -> dict[str, int]:
    """Refresh each ready pilot authorization before positive evidence expires."""
    scheduled = 0
    redispatched = 0
    skipped_user_cap = 0
    connections = DriveConnection.objects.filter(
        enabled=True,
        permission_authority=DriveConnection.PermissionAuthority.PER_USER_OAUTH,
    ).order_by("pk")
    for connection in connections:
        if (
            settings.GOOGLE_PERMISSION_AUTHORITY
            != DriveConnection.PermissionAuthority.PER_USER_OAUTH
            or not connection.effective_root_id
        ):
            continue
        authorizations = list(
            GoogleDriveAuthorization.objects.filter(connection=connection)
            .select_related("connection")
            .order_by("pk")
        )
        ready = [item for item in authorizations if _user_authorization_ready(item)]
        if len(ready) > settings.GOOGLE_USER_VISIBILITY_MAX_USERS:
            for authorization in authorizations:
                invalidate_authorization_evidence(
                    authorization.pk,
                    reason_code="user_cap_exceeded",
                )
            skipped_user_cap += 1
            continue
        for authorization in authorizations:
            if authorization not in ready:
                invalidate_authorization_evidence(
                    authorization.pk,
                    reason_code="authorization_unavailable",
                )
                continue
            active = UserVisibilitySyncRun.objects.filter(
                authorization=authorization,
                status__in=[
                    UserVisibilitySyncRun.Status.QUEUED,
                    UserVisibilitySyncRun.Status.RUNNING,
                ],
            )
            if active.filter(status=UserVisibilitySyncRun.Status.RUNNING).exists():
                continue
            queued = list(active.order_by("pk"))
            if queued:
                for run in queued:
                    run_user_visibility_sync.delay(run.pk)
                    redispatched += 1
                continue
            run = UserVisibilitySyncRun.create_for_authorization(authorization)
            run_user_visibility_sync.delay(run.pk)
            scheduled += 1
    return {
        "scheduled": scheduled,
        "redispatched": redispatched,
        "skipped_user_cap": skipped_user_cap,
    }


@shared_task(name="integrations.sweep_stale_permission_sync_runs")
def sweep_stale_permission_sync_runs() -> dict[str, int]:
    """Fail closed on permission runs a crashed worker left stuck in RUNNING.

    acks_late redelivery only covers a lost worker while the broker still
    holds the task message; once it is gone the run would stay RUNNING
    forever, blocking rerun visibility while no reconciliation happens.
    """
    cutoff = timezone.now() - datetime.timedelta(
        minutes=settings.PERMISSION_SYNC_STALE_RUN_TIMEOUT_MINUTES
    )
    swept = PermissionSyncRun.objects.filter(
        status=PermissionSyncRun.Status.RUNNING,
        started_at__lt=cutoff,
    ).update(
        status=PermissionSyncRun.Status.FAILED,
        error_code="stale_run_timeout",
        finished_at=timezone.now(),
    )
    return {"swept": swept}


@shared_task(name="integrations.sweep_stale_user_visibility_sync_runs")
def sweep_stale_user_visibility_sync_runs() -> dict[str, int]:
    """Expire evidence for authorizations whose worker died during refresh."""
    cutoff = timezone.now() - datetime.timedelta(
        minutes=settings.GOOGLE_USER_VISIBILITY_STALE_RUN_TIMEOUT_MINUTES
    )
    stale_runs = list(
        UserVisibilitySyncRun.objects.filter(
            status=UserVisibilitySyncRun.Status.RUNNING,
            started_at__lt=cutoff,
        ).order_by("pk")
    )
    swept = 0
    for run in stale_runs:
        claimed = UserVisibilitySyncRun.objects.filter(
            pk=run.pk,
            status=UserVisibilitySyncRun.Status.RUNNING,
            started_at__lt=cutoff,
        ).update(
            status=UserVisibilitySyncRun.Status.FAILED,
            error_code="stale_run_timeout",
            finished_at=timezone.now(),
        )
        if not claimed:
            continue
        invalidate_authorization_evidence(
            run.authorization_id,
            reason_code="stale_run_timeout",
        )
        swept += 1
    return {"swept": swept}


@shared_task(name="integrations.sweep_stale_drive_sync_runs")
def sweep_stale_drive_sync_runs() -> dict[str, int]:
    """Fail closed on runs a crashed worker left stuck in RUNNING.

    A run only ever leaves RUNNING via sync_drive_metadata's own status
    update, so one still RUNNING after the timeout has no worker left
    finishing it — mark it FAILED rather than let it block re-sync forever.
    """
    cutoff = timezone.now() - datetime.timedelta(
        minutes=settings.DRIVE_SYNC_STALE_RUN_TIMEOUT_MINUTES
    )
    swept = DriveSyncRun.objects.filter(
        status=DriveSyncRun.Status.RUNNING,
        started_at__lt=cutoff,
    ).update(
        status=DriveSyncRun.Status.FAILED,
        error_summary="stale_run_timeout",
        finished_at=timezone.now(),
    )
    return {"swept": swept}


@shared_task(name="integrations.sweep_stale_graph_extractions")
def sweep_stale_graph_extractions() -> dict[str, int]:
    """Fail closed on extractions a crashed worker left stuck in RUNNING.

    acks_late redelivery only covers a lost worker while the broker still
    holds the task message; once it is gone the row would stay RUNNING
    forever, invisible to the sync requeue (which recovers PENDING/FAILED
    only). Marking it FAILED hands it back to that recovery path.
    """
    cutoff = timezone.now() - datetime.timedelta(
        minutes=settings.GRAPH_EXTRACTION_STALE_RUNNING_TIMEOUT_MINUTES
    )
    swept = SourceDocument.objects.filter(
        graph_extraction_status=SourceDocument.GraphExtractionStatus.RUNNING,
        graph_extraction_started_at__lt=cutoff,
    ).update(
        graph_extraction_status=SourceDocument.GraphExtractionStatus.FAILED,
        graph_extraction_error_summary="stale_running_timeout",
        graph_extraction_attempts=F("graph_extraction_attempts") + 1,
        graph_extraction_finished_at=timezone.now(),
        updated_at=timezone.now(),
    )
    return {"swept": swept}


@shared_task(name="integrations.monitor_freshness")
def monitor_freshness() -> dict[str, int | str | None]:
    """Tick the scheduler heartbeat and log pre-expiry freshness alerts.

    Read-only apart from the single heartbeat row and idempotent, so it
    needs no per-run lock or stale-run sweep: overlapping ticks converge on
    the same state. The returned payload is counts, ages, and status labels
    only — safe for the Celery result backend under the evidence policy.
    """
    SchedulerHeartbeat.objects.update_or_create(
        name=FRESHNESS_HEARTBEAT_NAME,
        defaults={"last_tick_at": timezone.now()},
    )
    report = build_freshness_report()
    if report.status == STATUS_ERROR:
        logger.error(
            "freshness error: expired_targets=%d expired_evidence=%d heartbeat_age=%s",
            report.targets_expired,
            report.expired_evidence_documents,
            report.heartbeat_age_seconds,
        )
    elif report.status == STATUS_WARN:
        logger.warning(
            "freshness warn: expiring_soon=%d unknown_documents=%d "
            "consecutive_failures=%d extraction_failed=%d",
            report.targets_expiring_soon,
            report.unknown_documents,
            report.max_consecutive_failures,
            report.content_extraction_failed_documents,
        )
    return report.as_payload()


def _prune_completed_runs(model, *, group_field, success_statuses, cutoff) -> int:
    """Delete completed runs past the cutoff, keeping the latest success per target."""
    keep = (
        model.objects.filter(status__in=success_statuses)
        .values(group_field)
        .annotate(latest=Max("pk"))
        .values_list("latest", flat=True)
    )
    deleted, _ = (
        model.objects.exclude(
            status__in=[model.Status.QUEUED, model.Status.RUNNING],
        )
        .exclude(pk__in=keep)
        .filter(
            Q(finished_at__lt=cutoff) | Q(finished_at__isnull=True, created_at__lt=cutoff),
        )
        .delete()
    )
    return deleted


@shared_task(name="integrations.prune_completed_sync_runs")
def prune_completed_sync_runs() -> dict[str, int]:
    """Delete completed sync-run rows past the retention window.

    Freshness aggregation reads these tables on every monitor tick, so they
    must not grow without bound. The most recent successful run per target is
    always kept: delegated last-success is derived from run rows, and pruning
    it would make a healthy connection look like it never synced. Queued and
    running rows are never touched.
    """
    cutoff = timezone.now() - datetime.timedelta(days=settings.SYNC_RUN_RETENTION_DAYS)
    return {
        "user_visibility_runs": _prune_completed_runs(
            UserVisibilitySyncRun,
            group_field="authorization",
            success_statuses=[
                UserVisibilitySyncRun.Status.SUCCEEDED,
                UserVisibilitySyncRun.Status.PARTIAL,
            ],
            cutoff=cutoff,
        ),
        "permission_runs": _prune_completed_runs(
            PermissionSyncRun,
            group_field="connection",
            success_statuses=[
                PermissionSyncRun.Status.SUCCEEDED,
                PermissionSyncRun.Status.PARTIAL,
            ],
            cutoff=cutoff,
        ),
        "drive_runs": _prune_completed_runs(
            DriveSyncRun,
            group_field="connection",
            success_statuses=[DriveSyncRun.Status.SUCCEEDED],
            cutoff=cutoff,
        ),
    }


@shared_task(
    bind=True,
    name="integrations.queue_document_extraction",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=3,
)
def queue_document_extraction(
    self, source_document_id: int, expected_content_hash: str | None = None
) -> dict[str, int | str]:
    """Extract one stored document into the Neo4j graph (Phase 3 pipeline).

    Return value is persisted by the Celery result backend, so it must only
    ever carry ids, status, and counts — never document content.
    """
    expected_content_hash = expected_content_hash or _current_content_hash(source_document_id)
    if not expected_content_hash:
        _set_graph_extraction_state(
            source_document_id,
            SourceDocument.GraphExtractionStatus.SKIPPED,
            error_summary="skipped_no_content",
            finished_at=timezone.now(),
        )
        return {"source_document_id": source_document_id, "status": "skipped_no_content"}

    if not _set_graph_extraction_state(
        source_document_id,
        SourceDocument.GraphExtractionStatus.RUNNING,
        expected_content_hash=expected_content_hash,
        started_at=timezone.now(),
    ):
        return _stale_content_result(source_document_id)
    try:
        result = extract_document_to_graph(source_document_id, expected_content_hash)
    except Exception as exc:
        if isinstance(exc, get_retryable_extraction_exceptions()):
            if _current_content_hash(source_document_id) != expected_content_hash:
                return _stale_content_result(source_document_id)
            if self.request.retries < self.max_retries:
                # The row stays RUNNING while Celery owns the scheduled retry,
                # so the next metadata sync does not enqueue a competing
                # recovery job.
                raise self.retry(
                    exc=exc,
                    countdown=_retry_countdown(self.request.retries),
                ) from exc
        if not _set_graph_extraction_state(
            source_document_id,
            SourceDocument.GraphExtractionStatus.FAILED,
            expected_content_hash=expected_content_hash,
            error_summary=f"{type(exc).__module__}.{type(exc).__name__}"[:512],
            finished_at=timezone.now(),
        ):
            return _stale_content_result(source_document_id)
        raise

    if result["status"] == "skipped_stale_content_version":
        return result

    final_status = (
        SourceDocument.GraphExtractionStatus.SUCCEEDED
        if result["status"] == "extracted"
        else SourceDocument.GraphExtractionStatus.SKIPPED
    )
    if not _set_graph_extraction_state(
        source_document_id,
        final_status,
        expected_content_hash=expected_content_hash,
        error_summary=(
            ""
            if final_status == SourceDocument.GraphExtractionStatus.SUCCEEDED
            else result["status"]
        ),
        finished_at=timezone.now(),
    ):
        return _stale_content_result(source_document_id)
    if final_status == SourceDocument.GraphExtractionStatus.SUCCEEDED:
        _mark_per_user_content_ready(source_document_id, expected_content_hash)
    return result
