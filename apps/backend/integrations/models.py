from django.conf import settings
from django.db import models
from django.utils import timezone


class DriveConnection(models.Model):
    class ScopeType(models.TextChoices):
        FOLDER = "folder", "Folder"
        SHARED_DRIVE = "shared_drive", "Shared drive"

    name = models.CharField(max_length=120, default="Primary Google Drive")
    workspace_domain = models.CharField(max_length=255)
    delegated_subject_email = models.EmailField(blank=True)
    service_account_email = models.EmailField(blank=True)
    credential_reference = models.CharField(
        max_length=255,
        default="GOOGLE_SERVICE_ACCOUNT_FILE",
        help_text="Env var or secret reference; not credential JSON.",
    )
    scope_type = models.CharField(
        max_length=32,
        choices=ScopeType.choices,
        default=ScopeType.FOLDER,
    )
    root_folder_id = models.CharField(max_length=255, blank=True)
    shared_drive_id = models.CharField(max_length=255, blank=True)
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class SourceDocument(models.Model):
    class GraphExtractionStatus(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        SKIPPED = "skipped", "Skipped"

    class ExclusionReason(models.TextChoices):
        PUBLIC_LINK_NOT_SUPPORTED = "public_link_not_supported", "Public link not supported"
        DOMAIN_WIDE_VISIBILITY_NOT_SUPPORTED = (
            "domain_wide_visibility_not_supported",
            "Domain-wide visibility not supported",
        )
        UNSUPPORTED_MIME_TYPE = "unsupported_mime_type", "Unsupported MIME type"
        MISSING_REQUIRED_METADATA = "missing_required_metadata", "Missing required metadata"
        PERMISSION_METADATA_INCOMPLETE = (
            "permission_metadata_incomplete",
            "Permission metadata incomplete",
        )

    connection = models.ForeignKey(
        DriveConnection,
        on_delete=models.PROTECT,
        related_name="source_documents",
    )
    drive_file_id = models.CharField(max_length=255)
    title = models.CharField(max_length=512)
    mime_type = models.CharField(max_length=255)
    drive_url = models.URLField(max_length=2048, blank=True)
    created_time = models.DateTimeField(null=True, blank=True)
    modified_time = models.DateTimeField(null=True, blank=True)
    last_metadata_sync_time = models.DateTimeField(null=True, blank=True)
    # Owned by the content stage: sha256 of the exported/downloaded bytes.
    # Metadata syncs must never write it.
    content_hash = models.CharField(max_length=128, blank=True)
    # Drive-reported md5 from file metadata; absent for Google-native files.
    # Kept separate so the two hashes can never overwrite each other.
    drive_md5_checksum = models.CharField(max_length=64, blank=True)
    folder_path = models.TextField(blank=True)
    parent_folder_ids = models.JSONField(default=list, blank=True)
    shared_drive_id = models.CharField(max_length=255, blank=True)
    owner_email = models.EmailField(blank=True)
    creator_email = models.EmailField(blank=True)
    source_permissions_version = models.CharField(max_length=64, blank=True)
    last_permission_sync_time = models.DateTimeField(null=True, blank=True)
    graph_extraction_status = models.CharField(
        max_length=16,
        choices=GraphExtractionStatus.choices,
        default=GraphExtractionStatus.PENDING,
    )
    # Exception class names or controlled skip labels only: document text and
    # remote error payloads must never become persistent task metadata.
    graph_extraction_error_summary = models.CharField(max_length=512, blank=True)
    # FAILED transitions for the current content version. Caps the sync-driven
    # requeue so a deterministic failure cannot burn LLM calls on every sync.
    graph_extraction_attempts = models.PositiveIntegerField(default=0)
    # When an extraction task was last enqueued for this row. updated_at can't
    # serve here: every metadata sync rewrites the row, so it is always fresh.
    graph_extraction_queued_at = models.DateTimeField(null=True, blank=True)
    graph_extraction_started_at = models.DateTimeField(null=True, blank=True)
    graph_extraction_finished_at = models.DateTimeField(null=True, blank=True)
    retrieval_eligible = models.BooleanField(default=False)
    exclusion_reason = models.CharField(
        max_length=64,
        choices=ExclusionReason.choices,
        blank=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["title", "drive_file_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "drive_file_id"],
                name="unique_source_document_per_drive_connection",
            )
        ]
        indexes = [
            models.Index(fields=["drive_file_id"]),
            models.Index(fields=["retrieval_eligible"]),
            models.Index(fields=["source_permissions_version"]),
        ]

    def __str__(self) -> str:
        return self.title


class DrivePermissionSnapshot(models.Model):
    source_document = models.OneToOneField(
        SourceDocument,
        on_delete=models.CASCADE,
        related_name="permission_snapshot",
    )
    source_permissions_version = models.CharField(max_length=64)
    # SECURITY: contains the raw Drive permission entries, including client
    # email addresses. Never expose via an API serializer and never log it.
    raw_permissions = models.JSONField(default=list, blank=True)
    has_public_link = models.BooleanField(default=False)
    has_domain_visibility = models.BooleanField(default=False)
    captured_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=["source_permissions_version"]),
            models.Index(fields=["has_public_link", "has_domain_visibility"]),
        ]

    def __str__(self) -> str:
        return f"Permissions for {self.source_document_id}"


class SourceDocumentContent(models.Model):
    """Exported/downloaded document content, stored ahead of extraction.

    Postgres is the Phase 2 content store for the pilot scope; revisit with
    object storage if document volume outgrows it.
    """

    source_document = models.OneToOneField(
        SourceDocument,
        on_delete=models.CASCADE,
        related_name="content",
    )
    # SECURITY: raw client document content. Never log it and never expose it
    # through an API serializer.
    content = models.BinaryField()
    exported_mime_type = models.CharField(max_length=255)
    content_hash = models.CharField(max_length=128)
    exported_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [models.Index(fields=["content_hash"])]

    def __str__(self) -> str:
        return f"Content for {self.source_document_id}"


class DriveSyncRun(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"
        CANCELED = "canceled", "Canceled"

    connection = models.ForeignKey(
        DriveConnection,
        on_delete=models.PROTECT,
        related_name="sync_runs",
    )
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="drive_sync_runs",
    )
    actor_email = models.EmailField(blank=True)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.QUEUED)
    scope_type = models.CharField(max_length=32, choices=DriveConnection.ScopeType.choices)
    root_folder_id = models.CharField(max_length=255, blank=True)
    shared_drive_id = models.CharField(max_length=255, blank=True)
    total_files = models.PositiveIntegerField(default=0)
    stored_files = models.PositiveIntegerField(default=0)
    skipped_files = models.PositiveIntegerField(default=0)
    error_summary = models.CharField(max_length=512, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "created_at"]),
            models.Index(fields=["scope_type", "root_folder_id", "shared_drive_id"]),
        ]

    def __str__(self) -> str:
        return f"Drive sync {self.pk or 'unsaved'} ({self.status})"

    @classmethod
    def create_for_connection(cls, connection, *, triggered_by=None):
        actor_email = getattr(triggered_by, "email", "") if triggered_by else ""
        return cls.objects.create(
            connection=connection,
            triggered_by=triggered_by,
            actor_email=actor_email,
            scope_type=connection.scope_type,
            root_folder_id=connection.root_folder_id,
            shared_drive_id=connection.shared_drive_id,
        )
