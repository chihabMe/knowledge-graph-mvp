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
    content_hash: str = ""
    folder_path: str = ""
    parent_folder_ids: list[str] = field(default_factory=list)
    shared_drive_id: str = ""
    owner_email: str = ""
    creator_email: str = ""
    permissions: list[dict] = field(default_factory=list)


class DriveMetadataClient(Protocol):
    def list_files(self, connection: DriveConnection) -> list[DriveFileMetadata]:
        """Return Drive file metadata for a configured connection."""
