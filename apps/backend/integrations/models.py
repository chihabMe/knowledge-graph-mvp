import datetime
import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


def permission_verification_cutoff():
    """Oldest SpiceDB verification evidence retrieval may trust."""
    return timezone.now() - datetime.timedelta(
        seconds=settings.PERMISSION_VERIFICATION_MAX_AGE_SECONDS
    )


class DriveConnection(models.Model):
    class ScopeType(models.TextChoices):
        FOLDER = "folder", "Folder"
        SHARED_DRIVE = "shared_drive", "Shared drive"

    class PermissionAuthority(models.TextChoices):
        DELEGATED_ACL = "delegated_acl", "Delegated ACL (legacy)"
        PER_USER_OAUTH = "per_user_oauth", "Per-user OAuth"

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
    permission_authority = models.CharField(
        max_length=32,
        choices=PermissionAuthority.choices,
        default=PermissionAuthority.PER_USER_OAUTH,
    )
    # Rotated by the cutover service before a root, account, or authority
    # change may grant access. It is intentionally not derived from mutable
    # identifiers and is never exposed through status APIs.
    authorization_generation = models.UUIDField(default=uuid.uuid4, editable=False)
    permission_authority_changed_at = models.DateTimeField(null=True, blank=True)
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    @property
    def effective_root_id(self) -> str:
        """The Drive root matching scope_type; empty when misconfigured."""
        if self.scope_type == self.ScopeType.SHARED_DRIVE:
            return self.shared_drive_id
        return self.root_folder_id


class SourceDocumentQuerySet(models.QuerySet):
    def permission_verified(self):
        """The one definition of 'retrieval eligibility is backed by SpiceDB'.

        Retrieval filtering (lookup), the drive-sync preserve gate, and any
        future consumer must share this conjunction; see also
        SourceDocument.is_permission_verified for the instance twin. Expired
        evidence is denied even if a stale SpiceDB tuple still exists.
        """
        return self.filter(
            active_in_scope=True,
            retrieval_eligible=True,
            spicedb_verified_at__gte=permission_verification_cutoff(),
            spicedb_permissions_version=models.F("source_permissions_version"),
        )


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
        UNSUPPORTED_PERMISSION = "unsupported_permission", "Unsupported permission"
        GROUP_MEMBERSHIP_UNRESOLVED = (
            "group_membership_unresolved",
            "Group membership unresolved",
        )
        INACTIVE_IN_SCOPE = "inactive_in_scope", "Inactive in selected scope"
        NO_EFFECTIVE_GRANTS = "no_effective_grants", "No effective grants"

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
    active_in_scope = models.BooleanField(default=True)
    last_seen_sync_marker = models.CharField(max_length=36, blank=True)
    spicedb_permissions_version = models.CharField(max_length=64, blank=True)
    spicedb_revision = models.CharField(max_length=1024, blank=True)
    spicedb_verified_at = models.DateTimeField(null=True, blank=True)
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

    objects = SourceDocumentQuerySet.as_manager()

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
            models.Index(fields=["connection", "active_in_scope"]),
        ]

    def __str__(self) -> str:
        return self.title

    def is_permission_verified(self, version: str) -> bool:
        """Instance twin of SourceDocumentQuerySet.permission_verified.

        `version` pins both sides of the CAS: the row's ACL version and its
        verified SpiceDB version must equal the version just scanned.
        """
        return bool(
            self.active_in_scope
            and self.retrieval_eligible
            and self.spicedb_verified_at
            and self.spicedb_verified_at >= permission_verification_cutoff()
            and self.source_permissions_version == version
            and self.spicedb_permissions_version == version
        )


class GoogleDriveAuthorization(models.Model):
    """One encrypted per-user Drive authorization for an indexed connection."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        SCOPE_MISSING = "scope_missing", "Required scope missing"
        REFRESH_FAILED = "refresh_failed", "Refresh failed"
        REVOKED = "revoked", "Revoked"
        DISCONNECTED = "disconnected", "Disconnected"

    connection = models.ForeignKey(
        DriveConnection,
        on_delete=models.PROTECT,
        related_name="google_drive_authorizations",
    )
    google_issuer = models.CharField(max_length=255)
    google_subject = models.CharField(max_length=255)
    normalized_email = models.EmailField()
    workspace_domain = models.CharField(max_length=255)
    # Ciphertext only. Plain refresh credentials never have a model field and
    # this value is non-editable so generic model forms omit it by default.
    encrypted_refresh_credential = models.BinaryField(default=bytes, editable=False)
    encryption_key_version = models.CharField(max_length=32, blank=True, editable=False)
    granted_scopes = models.JSONField(default=list, blank=True)
    connection_generation = models.UUIDField(editable=False)
    authorization_generation = models.UUIDField(default=uuid.uuid4, editable=False)
    status = models.CharField(max_length=32, choices=Status.choices, default=Status.DISCONNECTED)
    connected_at = models.DateTimeField(null=True, blank=True)
    last_refreshed_at = models.DateTimeField(null=True, blank=True)
    last_successful_visibility_sync_at = models.DateTimeField(null=True, blank=True)
    disconnected_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "google_issuer", "google_subject"],
                name="unique_google_drive_authorization_per_connection_subject",
            )
        ]
        indexes = [
            models.Index(fields=["connection", "normalized_email", "status"]),
            models.Index(fields=["connection", "connection_generation"]),
        ]

    def __str__(self) -> str:
        return f"Drive authorization {self.pk or 'unsaved'} ({self.status})"


class UserDocumentVisibility(models.Model):
    """Fresh PostgreSQL evidence that narrows, but never creates, a SpiceDB grant."""

    class State(models.TextChoices):
        VERIFIED_VISIBLE = "verified_visible", "Verified visible"
        DENIED = "denied", "Denied"
        UNKNOWN = "unknown", "Unknown"

    authorization = models.ForeignKey(
        GoogleDriveAuthorization,
        on_delete=models.CASCADE,
        related_name="document_visibility",
    )
    source_document = models.ForeignKey(
        SourceDocument,
        on_delete=models.CASCADE,
        related_name="user_visibility_evidence",
    )
    connection_generation = models.UUIDField(editable=False)
    authorization_generation = models.UUIDField(editable=False)
    state = models.CharField(max_length=32, choices=State.choices, default=State.UNKNOWN)
    checked_at = models.DateTimeField(default=timezone.now)
    visibility_sync_marker = models.UUIDField(default=uuid.uuid4, editable=False)
    spicedb_revision = models.CharField(max_length=1024, blank=True)
    spicedb_verified_at = models.DateTimeField(null=True, blank=True)
    # Controlled labels only; never store a remote response or exception text.
    reason_code = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["authorization", "source_document"],
                name="unique_user_visibility_per_authorization_document",
            )
        ]
        indexes = [
            models.Index(fields=["authorization", "state", "checked_at"]),
            models.Index(
                fields=["source_document", "connection_generation", "authorization_generation"]
            ),
        ]

    def __str__(self) -> str:
        return f"User document visibility {self.pk or 'unsaved'} ({self.state})"


class UserVisibilitySyncRun(models.Model):
    """Durable, controlled audit state for one user's indexed-ID visibility run."""

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        PARTIAL = "partial", "Partial"
        FAILED = "failed", "Failed"

    connection = models.ForeignKey(
        DriveConnection,
        on_delete=models.PROTECT,
        related_name="user_visibility_sync_runs",
    )
    authorization = models.ForeignKey(
        GoogleDriveAuthorization,
        on_delete=models.PROTECT,
        related_name="visibility_sync_runs",
    )
    connection_generation = models.UUIDField(editable=False)
    authorization_generation = models.UUIDField(editable=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    documents_considered = models.PositiveIntegerField(default=0)
    documents_verified_visible = models.PositiveIntegerField(default=0)
    documents_denied = models.PositiveIntegerField(default=0)
    documents_unknown = models.PositiveIntegerField(default=0)
    relationships_touched = models.PositiveIntegerField(default=0)
    relationships_deleted = models.PositiveIntegerField(default=0)
    error_code = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["authorization", "status", "created_at"]),
            models.Index(fields=["connection", "status", "created_at"]),
        ]

    def __str__(self) -> str:
        return f"User visibility sync {self.pk or 'unsaved'} ({self.status})"

    @classmethod
    def create_for_authorization(cls, authorization):
        return cls.objects.create(
            connection=authorization.connection,
            authorization=authorization,
            connection_generation=authorization.connection_generation,
            authorization_generation=authorization.authorization_generation,
        )


class PermissionSnapshotBase(models.Model):
    """Raw ACL capture for one Drive resource; the parent row keeps the
    authoritative source_permissions_version so it is not duplicated here."""

    # SECURITY: contains the raw Drive permission entries, including client
    # email addresses. Never expose via an API serializer and never log it.
    raw_permissions = models.JSONField(default=list, blank=True)
    permissions_complete = models.BooleanField(default=True)
    captured_at = models.DateTimeField(default=timezone.now)

    class Meta:
        abstract = True


class DrivePermissionSnapshot(PermissionSnapshotBase):
    source_document = models.OneToOneField(
        SourceDocument,
        on_delete=models.CASCADE,
        related_name="permission_snapshot",
    )
    has_public_link = models.BooleanField(default=False)
    has_domain_visibility = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=["has_public_link", "has_domain_visibility"]),
        ]

    def __str__(self) -> str:
        return f"Permissions for {self.source_document_id}"


class DriveFolder(models.Model):
    connection = models.ForeignKey(
        DriveConnection,
        on_delete=models.CASCADE,
        related_name="drive_folders",
    )
    drive_folder_id = models.CharField(max_length=255)
    parent_folder_ids = models.JSONField(default=list, blank=True)
    source_permissions_version = models.CharField(max_length=64, blank=True)
    active_in_scope = models.BooleanField(default=True)
    last_seen_sync_marker = models.CharField(max_length=36, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["connection", "drive_folder_id"],
                name="unique_drive_folder_per_connection",
            )
        ]
        indexes = [models.Index(fields=["connection", "active_in_scope"])]

    def __str__(self) -> str:
        return f"Drive folder {self.pk or 'unsaved'}"


class DriveFolderPermissionSnapshot(PermissionSnapshotBase):
    drive_folder = models.OneToOneField(
        DriveFolder,
        on_delete=models.CASCADE,
        related_name="permission_snapshot",
    )

    def __str__(self) -> str:
        return f"Folder permissions {self.drive_folder_id}"


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


class PermissionSyncRun(models.Model):
    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        PARTIAL = "partial", "Partial"
        FAILED = "failed", "Failed"

    connection = models.ForeignKey(
        DriveConnection,
        on_delete=models.PROTECT,
        related_name="permission_sync_runs",
    )
    triggered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="permission_sync_runs",
    )
    actor_email = models.EmailField(blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.QUEUED)
    documents_seen = models.PositiveIntegerField(default=0)
    folders_seen = models.PositiveIntegerField(default=0)
    groups_resolved = models.PositiveIntegerField(default=0)
    relationships_touched = models.PositiveIntegerField(default=0)
    relationships_deleted = models.PositiveIntegerField(default=0)
    documents_verified = models.PositiveIntegerField(default=0)
    documents_excluded = models.PositiveIntegerField(default=0)
    error_code = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "created_at"])]

    def __str__(self) -> str:
        return f"Permission sync {self.pk or 'unsaved'} ({self.status})"

    @classmethod
    def create_for_connection(cls, connection, *, triggered_by=None):
        actor_email = getattr(triggered_by, "email", "") if triggered_by else ""
        return cls.objects.create(
            connection=connection,
            triggered_by=triggered_by,
            actor_email=actor_email,
        )


class SchedulerHeartbeat(models.Model):
    """Liveness marker a periodic task updates on every tick.

    The freshness health endpoint reads the tick age, so a dead Celery beat
    or worker is itself detectable instead of silently freezing alerting.
    """

    name = models.CharField(max_length=64, unique=True)
    last_tick_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Scheduler heartbeat {self.name}"
