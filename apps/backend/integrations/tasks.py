import datetime

from celery import shared_task
from django.conf import settings
from django.core.cache import cache
from django.db.models import F
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
from integrations.models import (
    DriveSyncRun,
    PermissionSyncRun,
    SourceDocument,
    SourceDocumentContent,
)

_UNSET = object()


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
    if not cache.add(lock_key, lock_token, timeout=900):
        raise self.retry(countdown=min(2 ** (self.request.retries + 1), 60))
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
                raise self.retry(
                    exc=exc, countdown=min(2 ** (self.request.retries + 1), 60)
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
                    countdown=min(2 ** (self.request.retries + 1), 60),
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
    return result
