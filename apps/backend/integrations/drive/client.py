from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from integrations.models import DriveConnection


@dataclass(frozen=True)
class DriveFileMetadata:
    drive_file_id: str
    title: str
    mime_type: str
    drive_url: str = ""
    created_time: datetime | None = None
    modified_time: datetime | None = None
    md5_checksum: str = ""
    folder_path: str = ""
    parent_folder_ids: list[str] = field(default_factory=list)
    shared_drive_id: str = ""
    owner_email: str = ""
    creator_email: str = ""
    permissions: list[dict] = field(default_factory=list)
    # True when permissions.list() failed for this file (e.g. the service
    # account has read access to the file itself but not to its ACL). Must
    # never be treated as "no special sharing" — sync.py excludes on this.
    permissions_fetch_failed: bool = False


@dataclass(frozen=True)
class DriveRootCandidate:
    scope_type: str
    root_id: str
    name: str
    drive_url: str = ""
    shared_drive_id: str = ""


@dataclass(frozen=True)
class DrivePermissionAccessReport:
    sampled_files: int
    readable_files: int
    unreadable_files: int
    checked_all_available_files: bool
    folder_listing_errors: int = 0


class DriveMetadataClient(Protocol):
    def list_files(self, connection: DriveConnection) -> list[DriveFileMetadata]:
        """Return Drive file metadata for a configured connection."""

    def list_root_candidates(self, connection: DriveConnection) -> list[DriveRootCandidate]:
        """Return folder/shared-drive roots visible to the configured connection."""

    def check_permission_access(
        self,
        connection: DriveConnection,
        *,
        max_files: int = 10,
    ) -> DrivePermissionAccessReport:
        """Sample selected-root files and report whether ACL metadata is readable."""
