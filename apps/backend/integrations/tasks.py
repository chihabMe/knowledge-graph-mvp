from celery import shared_task
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
    run = DriveSyncRun.objects.select_related("connection").get(pk=run_id)
    if run.status != DriveSyncRun.Status.QUEUED:
        # Celery redelivery or a duplicate dispatch must never re-execute a
        # run and overwrite its audit counters/timestamps.
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


@shared_task(name="integrations.queue_document_extraction")
def queue_document_extraction(source_document_id: int) -> dict[str, int | str]:
    """Phase 3 stub: text extraction and chunking hook per stored document.

    Return value is persisted by the Celery result backend, so it must only
    ever carry ids and status — never document content.
    """
    return {"source_document_id": source_document_id, "status": "pending_extraction"}
