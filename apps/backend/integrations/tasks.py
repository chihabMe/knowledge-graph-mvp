import datetime

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from integrations.drive.export import export_file_content
from integrations.drive.google_client import (
    GoogleDriveMetadataClient,
    build_drive_service,
)
from integrations.drive.sync import sync_drive_metadata
from integrations.models import DriveSyncRun


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


@shared_task(name="integrations.queue_document_extraction")
def queue_document_extraction(source_document_id: int) -> dict[str, int | str]:
    """Phase 3 stub: text extraction and chunking hook per stored document.

    Return value is persisted by the Celery result backend, so it must only
    ever carry ids and status — never document content.
    """
    return {"source_document_id": source_document_id, "status": "pending_extraction"}
