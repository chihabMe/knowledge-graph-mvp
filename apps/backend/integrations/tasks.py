import datetime

from celery import shared_task
from django.conf import settings
from django.utils import timezone
from neo4j.exceptions import ServiceUnavailable, SessionExpired, TransientError
from openai import APIConnectionError, APITimeoutError, InternalServerError, RateLimitError

from graph.pipeline import extract_document_to_graph
from integrations.drive.export import export_file_content
from integrations.drive.google_client import (
    GoogleDriveMetadataClient,
    build_drive_service,
)
from integrations.drive.sync import sync_drive_metadata
from integrations.models import DriveSyncRun, SourceDocument

RETRYABLE_EXTRACTION_EXCEPTIONS = (
    APIConnectionError,
    APITimeoutError,
    InternalServerError,
    RateLimitError,
    OSError,
    ServiceUnavailable,
    SessionExpired,
    TransientError,
)
_UNSET = object()


def _set_graph_extraction_state(
    source_document_id: int,
    status: str,
    *,
    error_summary: str = "",
    started_at=_UNSET,
    finished_at=_UNSET,
) -> None:
    """Persist safe extraction-job state without ever retaining error text."""
    updates = {
        "graph_extraction_status": status,
        "graph_extraction_error_summary": error_summary,
        "updated_at": timezone.now(),
    }
    if started_at is not _UNSET:
        updates["graph_extraction_started_at"] = started_at
    if finished_at is not _UNSET:
        updates["graph_extraction_finished_at"] = finished_at
    SourceDocument.objects.filter(pk=source_document_id).update(**updates)


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


@shared_task(
    bind=True,
    name="integrations.queue_document_extraction",
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=3,
)
def queue_document_extraction(self, source_document_id: int) -> dict[str, int | str]:
    """Extract one stored document into the Neo4j graph (Phase 3 pipeline).

    Return value is persisted by the Celery result backend, so it must only
    ever carry ids, status, and counts — never document content.
    """
    _set_graph_extraction_state(
        source_document_id,
        SourceDocument.GraphExtractionStatus.RUNNING,
        started_at=timezone.now(),
    )
    try:
        result = extract_document_to_graph(source_document_id)
    except RETRYABLE_EXTRACTION_EXCEPTIONS as exc:
        if self.request.retries < self.max_retries:
            # The row stays RUNNING while Celery owns the scheduled retry, so
            # the next metadata sync does not enqueue a competing recovery job.
            raise self.retry(
                exc=exc,
                countdown=min(2 ** (self.request.retries + 1), 60),
            ) from exc
        _set_graph_extraction_state(
            source_document_id,
            SourceDocument.GraphExtractionStatus.FAILED,
            error_summary=f"{type(exc).__module__}.{type(exc).__name__}"[:512],
            finished_at=timezone.now(),
        )
        raise
    except Exception as exc:
        _set_graph_extraction_state(
            source_document_id,
            SourceDocument.GraphExtractionStatus.FAILED,
            error_summary=f"{type(exc).__module__}.{type(exc).__name__}"[:512],
            finished_at=timezone.now(),
        )
        raise

    final_status = (
        SourceDocument.GraphExtractionStatus.SUCCEEDED
        if result["status"] == "extracted"
        else SourceDocument.GraphExtractionStatus.SKIPPED
    )
    _set_graph_extraction_state(
        source_document_id,
        final_status,
        error_summary=(
            ""
            if final_status == SourceDocument.GraphExtractionStatus.SUCCEEDED
            else result["status"]
        ),
        finished_at=timezone.now(),
    )
    return result
