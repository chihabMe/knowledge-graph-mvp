from django.db import transaction
from django.utils import timezone

from integrations.drive.client import DriveMetadataClient
from integrations.drive.permissions import (
    has_domain_visibility,
    has_public_link,
    source_permissions_version,
)
from integrations.models import (
    DriveConnection,
    DrivePermissionSnapshot,
    DriveSyncRun,
    SourceDocument,
)

SUPPORTED_MIME_TYPES = {
    "application/pdf",
    "application/vnd.google-apps.document",
    "application/vnd.google-apps.spreadsheet",
    "text/csv",
    "text/markdown",
    "text/plain",
}


# TODO: the future Celery task wrapper must accept connection_id (a primitive
# int) and load the model itself — never pass model instances to tasks.
def sync_drive_metadata(
    *,
    connection: DriveConnection,
    client: DriveMetadataClient,
    triggered_by=None,
) -> DriveSyncRun:
    run = DriveSyncRun.create_for_connection(connection, triggered_by=triggered_by)
    run.status = DriveSyncRun.Status.RUNNING
    run.started_at = timezone.now()
    run.save(update_fields=["status", "started_at"])

    try:
        files = client.list_files(connection)
        stored_files = 0
        skipped_files = 0

        with transaction.atomic():
            for file_metadata in files:
                permissions = file_metadata.permissions or []
                permissions_version = source_permissions_version(permissions)
                public_link = has_public_link(permissions)
                domain_visibility = has_domain_visibility(permissions)
                exclusion_reason = _exclusion_reason(
                    mime_type=file_metadata.mime_type,
                    public_link=public_link,
                    domain_visibility=domain_visibility,
                )
                if exclusion_reason:
                    skipped_files += 1
                else:
                    stored_files += 1

                document, _created = SourceDocument.objects.update_or_create(
                    connection=connection,
                    drive_file_id=file_metadata.drive_file_id,
                    defaults={
                        "title": file_metadata.title,
                        "mime_type": file_metadata.mime_type,
                        "drive_url": file_metadata.drive_url,
                        "created_time": file_metadata.created_time,
                        "modified_time": file_metadata.modified_time,
                        "last_metadata_sync_time": timezone.now(),
                        "content_hash": file_metadata.content_hash,
                        "folder_path": file_metadata.folder_path,
                        "parent_folder_ids": file_metadata.parent_folder_ids,
                        "shared_drive_id": file_metadata.shared_drive_id,
                        "owner_email": file_metadata.owner_email,
                        "creator_email": file_metadata.creator_email,
                        "source_permissions_version": permissions_version,
                        "last_permission_sync_time": timezone.now(),
                        "retrieval_eligible": False,
                        "exclusion_reason": exclusion_reason,
                    },
                )
                DrivePermissionSnapshot.objects.update_or_create(
                    source_document=document,
                    defaults={
                        "source_permissions_version": permissions_version,
                        "raw_permissions": permissions,
                        "has_public_link": public_link,
                        "has_domain_visibility": domain_visibility,
                        "captured_at": timezone.now(),
                    },
                )

        run.status = DriveSyncRun.Status.SUCCEEDED
        run.total_files = len(files)
        run.stored_files = stored_files
        run.skipped_files = skipped_files
    except Exception as exc:
        run.status = DriveSyncRun.Status.FAILED
        # Class name only. str(exc) is content-controlled (an HttpError can
        # embed the request URI and response body, i.e. Drive metadata) and
        # this row is a persistence sink, so the message must not be stored.
        # The exception is re-raised — full detail stays with the caller.
        run.error_summary = f"{type(exc).__module__}.{type(exc).__name__}"[:512]
        raise
    finally:
        run.finished_at = timezone.now()
        run.save(
            update_fields=[
                "status",
                "total_files",
                "stored_files",
                "skipped_files",
                "error_summary",
                "finished_at",
            ]
        )

    return run


def _exclusion_reason(*, mime_type: str, public_link: bool, domain_visibility: bool) -> str:
    if public_link:
        return SourceDocument.ExclusionReason.PUBLIC_LINK_NOT_SUPPORTED
    if domain_visibility:
        return SourceDocument.ExclusionReason.DOMAIN_WIDE_VISIBILITY_NOT_SUPPORTED
    if mime_type not in SUPPORTED_MIME_TYPES:
        return SourceDocument.ExclusionReason.UNSUPPORTED_MIME_TYPE
    return ""
