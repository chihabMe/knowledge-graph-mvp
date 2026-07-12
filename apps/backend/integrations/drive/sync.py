from uuid import uuid4

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from integrations.drive.client import DriveMetadataClient
from integrations.drive.export import content_sha256
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
    SourceDocumentContent,
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
    run: DriveSyncRun | None = None,
    content_exporter=None,
    queue_extraction=None,
) -> DriveSyncRun:
    """Sync Drive metadata (and optionally content) into PostgreSQL.

    `run` lets a caller (the API view) pre-create the audit record before the
    work is queued. `content_exporter(file_metadata) -> (bytes, mime)` enables
    the content stage; `queue_extraction(document_id, content_hash)` is called for every
    document whose content was (re)stored and every failed/pending extraction
    that can be retried without re-exporting unchanged content.
    """
    if run is None:
        run = DriveSyncRun.create_for_connection(connection, triggered_by=triggered_by)
    # The Celery task claims QUEUED→RUNNING atomically before calling in;
    # only transition here for direct callers, so the claim's started_at
    # (the real claim moment) is never overwritten with a later one.
    if run.status != DriveSyncRun.Status.RUNNING:
        run.status = DriveSyncRun.Status.RUNNING
        run.started_at = timezone.now()
        run.save(update_fields=["status", "started_at"])

    extraction_candidates: list[tuple[int, str]] = []
    sync_marker = str(uuid4())

    try:
        files = client.list_files(connection)
        stored_files = 0
        skipped_files = 0

        # The pilot scope is small enough to keep one transaction around the
        # whole batch; content export inside it is an accepted POC tradeoff.
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
                    permissions_fetch_failed=file_metadata.permissions_fetch_failed,
                )
                if exclusion_reason:
                    skipped_files += 1
                else:
                    stored_files += 1

                existing_document = SourceDocument.objects.filter(
                    connection=connection,
                    drive_file_id=file_metadata.drive_file_id,
                ).first()
                previous_modified_time = (
                    existing_document.modified_time if existing_document else None
                )
                preserve_permission_verification = bool(
                    existing_document
                    and not exclusion_reason
                    and existing_document.is_permission_verified(permissions_version)
                )

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
                        # content_hash is deliberately absent: the content
                        # stage owns it (sha256 of exported bytes). Writing
                        # Drive's md5 here would wipe it on every metadata
                        # sync — Google-native files report no md5 at all.
                        "drive_md5_checksum": file_metadata.md5_checksum,
                        "folder_path": file_metadata.folder_path,
                        "parent_folder_ids": file_metadata.parent_folder_ids,
                        "shared_drive_id": file_metadata.shared_drive_id,
                        "owner_email": file_metadata.owner_email,
                        "creator_email": file_metadata.creator_email,
                        "source_permissions_version": permissions_version,
                        "last_permission_sync_time": timezone.now(),
                        "active_in_scope": True,
                        "last_seen_sync_marker": sync_marker,
                        "retrieval_eligible": preserve_permission_verification,
                        "spicedb_permissions_version": (
                            permissions_version if preserve_permission_verification else ""
                        ),
                        "spicedb_revision": (
                            existing_document.spicedb_revision
                            if preserve_permission_verification
                            else ""
                        ),
                        "spicedb_verified_at": (
                            existing_document.spicedb_verified_at
                            if preserve_permission_verification
                            else None
                        ),
                        "exclusion_reason": exclusion_reason,
                    },
                )
                DrivePermissionSnapshot.objects.update_or_create(
                    source_document=document,
                    defaults={
                        "raw_permissions": permissions,
                        "permissions_complete": not file_metadata.permissions_fetch_failed,
                        "has_public_link": public_link,
                        "has_domain_visibility": domain_visibility,
                        "captured_at": timezone.now(),
                    },
                )

                if content_exporter is not None and not exclusion_reason:
                    if _needs_content_refresh(document, previous_modified_time):
                        content_hash = _store_content(document, file_metadata, content_exporter)
                        extraction_candidates.append((document.pk, content_hash))
                    elif _extraction_needs_requeue(document):
                        # A transient graph/LLM outage must not require a
                        # content change before this source can recover.
                        extraction_candidates.append((document.pk, document.content_hash))

            SourceDocument.objects.filter(connection=connection, active_in_scope=True).exclude(
                last_seen_sync_marker=sync_marker
            ).update(
                active_in_scope=False,
                retrieval_eligible=False,
                exclusion_reason=SourceDocument.ExclusionReason.INACTIVE_IN_SCOPE,
                spicedb_permissions_version="",
                spicedb_revision="",
                spicedb_verified_at=None,
                updated_at=timezone.now(),
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
        # [:512] fits the CharField — a DataError here would mask the original
        # exception and leave the run row without its failure state.
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

    # Queue extraction only after the transaction committed, so a rolled-back
    # sync can never leave queued jobs pointing at missing rows. A mid-batch
    # failure cannot reach this line: the except block above re-raises, which
    # exits the function before any queueing happens (covered by test).
    if queue_extraction is not None and extraction_candidates:
        for document_id, content_hash in extraction_candidates:
            queue_extraction(document_id, content_hash)
        # Stamp the enqueue so the PENDING requeue gate can tell "queued just
        # now" from "queued long ago and the message evidently went missing".
        SourceDocument.objects.filter(
            pk__in=[document_id for document_id, _ in extraction_candidates]
        ).update(graph_extraction_queued_at=timezone.now())

    return run


def _extraction_needs_requeue(document) -> bool:
    """Recover unfinished extraction without duplicating or looping work.

    FAILED is retried only within the per-content-version attempts budget —
    a deterministic failure must not re-run on every sync forever. PENDING is
    retried only once its last enqueue is old enough that the task message is
    plainly gone; a fresh PENDING usually just means a worker hasn't started
    yet, and requeueing it would run the same extraction twice. RUNNING is
    the worker's (or the stale-extraction sweeper's) to resolve.
    """
    status = document.graph_extraction_status
    if status == SourceDocument.GraphExtractionStatus.FAILED:
        return document.graph_extraction_attempts < settings.GRAPH_EXTRACTION_MAX_SYNC_ATTEMPTS
    if status == SourceDocument.GraphExtractionStatus.PENDING:
        if document.graph_extraction_queued_at is None:
            return True
        cutoff = timezone.now() - timezone.timedelta(
            minutes=settings.GRAPH_EXTRACTION_PENDING_REQUEUE_AFTER_MINUTES
        )
        return document.graph_extraction_queued_at < cutoff
    return False


def _needs_content_refresh(document, previous_modified_time) -> bool:
    if previous_modified_time is None or previous_modified_time != document.modified_time:
        return True
    return not SourceDocumentContent.objects.filter(source_document=document).exists()


def _store_content(document, file_metadata, content_exporter) -> str:
    data, effective_mime = content_exporter(file_metadata)
    digest = content_sha256(data)
    SourceDocumentContent.objects.update_or_create(
        source_document=document,
        defaults={
            "content": data,
            "exported_mime_type": effective_mime,
            "content_hash": digest,
            "exported_at": timezone.now(),
        },
    )
    # The exported-bytes hash is authoritative: Google-native files have no
    # md5Checksum in their metadata, so this keeps the field uniform.
    document.content_hash = digest
    document.graph_extraction_status = SourceDocument.GraphExtractionStatus.PENDING
    document.graph_extraction_error_summary = ""
    document.graph_extraction_attempts = 0
    document.graph_extraction_started_at = None
    document.graph_extraction_finished_at = None
    # updated_at is listed so auto_now refreshes it: the PENDING requeue gate
    # reads it as "when did this row last transition".
    document.save(
        update_fields=[
            "content_hash",
            "graph_extraction_status",
            "graph_extraction_error_summary",
            "graph_extraction_attempts",
            "graph_extraction_started_at",
            "graph_extraction_finished_at",
            "updated_at",
        ]
    )
    return digest


def _exclusion_reason(
    *,
    mime_type: str,
    public_link: bool,
    domain_visibility: bool,
    permissions_fetch_failed: bool,
) -> str:
    # Checked first: an empty permissions list from a failed fetch reads
    # identically to "no special sharing" to the checks below. Fail closed
    # instead of silently treating an unknown ACL as safe.
    if permissions_fetch_failed:
        return SourceDocument.ExclusionReason.PERMISSION_METADATA_INCOMPLETE
    if public_link:
        return SourceDocument.ExclusionReason.PUBLIC_LINK_NOT_SUPPORTED
    if domain_visibility:
        return SourceDocument.ExclusionReason.DOMAIN_WIDE_VISIBILITY_NOT_SUPPORTED
    if mime_type not in SUPPORTED_MIME_TYPES:
        return SourceDocument.ExclusionReason.UNSUPPORTED_MIME_TYPE
    return ""
