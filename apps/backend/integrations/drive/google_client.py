"""Google Drive metadata client backed by the real Drive v3 API.

Read-only access via a service account (domain-wide delegation when a
delegated subject is configured). This module fetches metadata and sharing
information only — content export lives in integrations.drive.export.

The Drive service object is injectable so tests never touch the network.
"""

import os
from datetime import datetime
from typing import Any

from django.conf import settings

from integrations.drive.client import (
    DriveFileMetadata,
    DrivePermissionAccessReport,
    DriveRootCandidate,
)
from integrations.models import DriveConnection

DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"
PAGE_SIZE = 100
PERMISSION_CHECK_SAMPLE_SIZE = 10
FILE_LIST_FIELDS = (
    "nextPageToken, files(id, name, mimeType, webViewLink, createdTime, "
    "modifiedTime, md5Checksum, parents, driveId, owners(emailAddress))"
)
ROOT_FOLDER_LIST_FIELDS = "nextPageToken, files(id, name, webViewLink, driveId)"
SHARED_DRIVE_LIST_FIELDS = "nextPageToken, drives(id, name)"
PERMISSION_FIELDS = (
    "nextPageToken, permissions(id, type, role, emailAddress, domain, "
    "allowFileDiscovery, deleted, pendingOwner)"
)
ROOT_FOLDER_QUERY = (
    "sharedWithMe and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
)

try:
    from google.auth.exceptions import GoogleAuthError
except ImportError:  # pragma: no cover - dependency is installed in real/test envs.
    GoogleAuthError = None

try:
    from googleapiclient.errors import HttpError
except ImportError:  # pragma: no cover - dependency is installed in real/test envs.
    HttpError = None

_DRIVE_API_ERROR_TYPES = [OSError]
if HttpError is not None:
    _DRIVE_API_ERROR_TYPES.append(HttpError)
if GoogleAuthError is not None:
    _DRIVE_API_ERROR_TYPES.append(GoogleAuthError)
DRIVE_API_ERRORS = tuple(_DRIVE_API_ERROR_TYPES)


class MissingServiceAccountKeyError(RuntimeError):
    """The service-account key file is missing, empty, unreadable, or malformed.

    Raised whenever GOOGLE_SERVICE_ACCOUNT_FILE cannot yield credentials:
    unset path, absent file, empty file, an unreadable file, or contents that
    do not parse as key JSON — despite the name, callers must not assume
    "file absent". The compose stack mounts /dev/null when no host key path is
    configured, so "empty file" means the key was never mounted. This class
    name is what lands in DriveSyncRun.error_summary, so it must say the
    problem on its own — the alternative is an opaque credential parse error
    mid-sync.
    """


class GoogleDriveApiError(RuntimeError):
    """Controlled API-boundary error for Drive request failures."""


def build_drive_service(connection: DriveConnection):
    """Build an authenticated Drive v3 service for a connection."""
    key_path = settings.GOOGLE_SERVICE_ACCOUNT_FILE
    try:
        missing_or_empty_key = (
            not key_path or not os.path.exists(key_path) or os.path.getsize(key_path) == 0
        )
    except OSError as exc:
        raise MissingServiceAccountKeyError(
            "GOOGLE_SERVICE_ACCOUNT_FILE could not be inspected. Check the "
            "mounted service-account key path and file permissions."
        ) from exc
    if missing_or_empty_key:
        raise MissingServiceAccountKeyError(
            "GOOGLE_SERVICE_ACCOUNT_FILE is not configured or points at an "
            "empty file (the /dev/null bootstrap mount). Set the host path "
            "in .env and restart the stack."
        )

    # Imported lazily so tests that inject a fake service never need
    # Google credentials or the discovery cache.
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    try:
        credentials = service_account.Credentials.from_service_account_file(
            key_path,
            scopes=[DRIVE_READONLY_SCOPE],
        )
    except (OSError, ValueError) as exc:
        raise MissingServiceAccountKeyError(
            "GOOGLE_SERVICE_ACCOUNT_FILE could not be read as a service-account "
            "key. Check that the mounted file contains valid key JSON."
        ) from exc
    subject = connection.delegated_subject_email or settings.GOOGLE_DRIVE_DELEGATED_SUBJECT
    if subject:
        credentials = credentials.with_subject(subject)
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


class GoogleDriveMetadataClient:
    """Implements the DriveMetadataClient protocol against the Drive API."""

    def __init__(self, service=None):
        self._service = service

    def list_files(self, connection: DriveConnection) -> list[DriveFileMetadata]:
        service = self._service or build_drive_service(connection)
        root_id = self._root_id(connection)
        files: list[DriveFileMetadata] = []

        # Breadth-first folder walk. Works for both My Drive folders and
        # shared drives (the shared drive id acts as the root folder id).
        root_name = self._folder_name(service, root_id, connection)
        pending_folders: list[tuple[str, str]] = [(root_id, f"/{root_name}")]
        seen_folders = {root_id}
        # A multi-parented file is listed under each of its parents inside the
        # scanned scope; emit it once (first path wins) so it is never stored,
        # exported, or queued for extraction twice in one run.
        seen_files: set[str] = set()

        while pending_folders:
            folder_id, folder_path = pending_folders.pop(0)
            for entry in self._list_children(service, connection, folder_id):
                if entry.get("mimeType") == FOLDER_MIME_TYPE:
                    if entry["id"] not in seen_folders:
                        seen_folders.add(entry["id"])
                        pending_folders.append(
                            (entry["id"], f"{folder_path}/{entry.get('name', '')}")
                        )
                    continue
                if entry["id"] in seen_files:
                    continue
                seen_files.add(entry["id"])
                # A single file's permission ACL can be unreadable even when the
                # file itself is listable (e.g. folder-level sharing without
                # "manage permissions" rights) — isolate that to this one file
                # instead of aborting the whole sync; sync.py fails it closed.
                try:
                    permissions = self._list_permissions(service, entry["id"])
                    permissions_fetch_failed = False
                except DRIVE_API_ERRORS:
                    permissions = []
                    permissions_fetch_failed = True
                files.append(
                    self._to_metadata(entry, folder_path, permissions, permissions_fetch_failed)
                )

        return files

    def list_root_candidates(self, connection: DriveConnection) -> list[DriveRootCandidate]:
        try:
            service = self._service or build_drive_service(connection)
            candidates = [
                *self._list_shared_folders(service),
                *self._list_shared_drives(service),
            ]
        except MissingServiceAccountKeyError:
            # Key-file problems keep their own error (409 at the API boundary);
            # only Drive/auth request failures collapse into the 502 below.
            raise
        except DRIVE_API_ERRORS as exc:
            raise GoogleDriveApiError(
                "Google Drive API request failed while listing Drive roots."
            ) from exc

        by_scope_and_id: dict[tuple[str, str], DriveRootCandidate] = {}
        for candidate in candidates:
            by_scope_and_id.setdefault((candidate.scope_type, candidate.root_id), candidate)

        return sorted(
            by_scope_and_id.values(),
            key=lambda candidate: (
                candidate.scope_type,
                candidate.name.lower(),
                candidate.root_id,
            ),
        )

    def check_permission_access(
        self,
        connection: DriveConnection,
        *,
        max_files: int = PERMISSION_CHECK_SAMPLE_SIZE,
    ) -> DrivePermissionAccessReport:
        try:
            service = self._service or build_drive_service(connection)
            return self._check_permission_access(service, connection, max_files=max_files)
        except MissingServiceAccountKeyError:
            raise
        except DRIVE_API_ERRORS as exc:
            raise GoogleDriveApiError(
                "Google Drive API request failed while checking Drive permission metadata access."
            ) from exc

    def _root_id(self, connection: DriveConnection) -> str:
        if connection.scope_type == DriveConnection.ScopeType.SHARED_DRIVE:
            return connection.shared_drive_id
        return connection.root_folder_id

    def _folder_name(self, service, folder_id: str, connection: DriveConnection) -> str:
        if connection.scope_type == DriveConnection.ScopeType.SHARED_DRIVE:
            drive = service.drives().get(driveId=folder_id, fields="id, name").execute()
            return drive.get("name", "")
        folder = (
            service.files()
            .get(fileId=folder_id, fields="id, name", supportsAllDrives=True)
            .execute()
        )
        return folder.get("name", "")

    def _list_children(self, service, connection: DriveConnection, folder_id: str):
        page_token = None
        while True:
            request = service.files().list(
                q=f"'{folder_id}' in parents and trashed = false",
                fields=FILE_LIST_FIELDS,
                pageSize=PAGE_SIZE,
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            response = request.execute()
            yield from response.get("files", [])
            page_token = response.get("nextPageToken")
            if not page_token:
                return

    def _list_shared_folders(self, service):
        page_token = None
        while True:
            response = (
                service.files()
                .list(
                    q=ROOT_FOLDER_QUERY,
                    fields=ROOT_FOLDER_LIST_FIELDS,
                    pageSize=PAGE_SIZE,
                    pageToken=page_token,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
            for entry in response.get("files", []):
                root_id = entry.get("id", "")
                if not root_id:
                    continue
                yield DriveRootCandidate(
                    scope_type=DriveConnection.ScopeType.FOLDER,
                    root_id=root_id,
                    name=entry.get("name", ""),
                    drive_url=entry.get("webViewLink", ""),
                    shared_drive_id=entry.get("driveId", ""),
                )
            page_token = response.get("nextPageToken")
            if not page_token:
                return

    def _list_shared_drives(self, service):
        page_token = None
        while True:
            response = (
                service.drives()
                .list(
                    fields=SHARED_DRIVE_LIST_FIELDS,
                    pageSize=PAGE_SIZE,
                    pageToken=page_token,
                )
                .execute()
            )
            for entry in response.get("drives", []):
                root_id = entry.get("id", "")
                if not root_id:
                    continue
                yield DriveRootCandidate(
                    scope_type=DriveConnection.ScopeType.SHARED_DRIVE,
                    root_id=root_id,
                    name=entry.get("name", ""),
                    shared_drive_id=root_id,
                )
            page_token = response.get("nextPageToken")
            if not page_token:
                return

    def _list_permissions(self, service, file_id: str) -> list[dict[str, Any]]:
        # Shared-drive items do not return permissions on files.list, so a
        # dedicated permissions.list keeps behavior uniform for both scopes.
        permissions: list[dict[str, Any]] = []
        page_token = None
        while True:
            response = (
                service.permissions()
                .list(
                    fileId=file_id,
                    fields=PERMISSION_FIELDS,
                    pageToken=page_token,
                    supportsAllDrives=True,
                )
                .execute()
            )
            permissions.extend(response.get("permissions", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                return permissions

    def _check_permission_access(
        self,
        service,
        connection: DriveConnection,
        *,
        max_files: int,
    ) -> DrivePermissionAccessReport:
        max_files = max(1, max_files)
        root_id = self._root_id(connection)
        pending_folders: list[str] = [root_id]
        seen_folders = {root_id}
        seen_files: set[str] = set()
        sampled_files = 0
        readable_files = 0
        unreadable_files = 0
        folder_listing_errors = 0

        while pending_folders:
            folder_id = pending_folders.pop(0)
            try:
                for entry in self._list_children(service, connection, folder_id):
                    if entry.get("mimeType") == FOLDER_MIME_TYPE:
                        if entry["id"] not in seen_folders:
                            seen_folders.add(entry["id"])
                            pending_folders.append(entry["id"])
                        continue
                    if entry["id"] in seen_files:
                        continue
                    seen_files.add(entry["id"])
                    sampled_files += 1
                    try:
                        self._list_permissions(service, entry["id"])
                        readable_files += 1
                    except DRIVE_API_ERRORS:
                        unreadable_files += 1
                    if sampled_files >= max_files:
                        return DrivePermissionAccessReport(
                            sampled_files=sampled_files,
                            readable_files=readable_files,
                            unreadable_files=unreadable_files,
                            checked_all_available_files=False,
                            folder_listing_errors=folder_listing_errors,
                        )
            except DRIVE_API_ERRORS:
                folder_listing_errors += 1

        return DrivePermissionAccessReport(
            sampled_files=sampled_files,
            readable_files=readable_files,
            unreadable_files=unreadable_files,
            checked_all_available_files=folder_listing_errors == 0,
            folder_listing_errors=folder_listing_errors,
        )

    def _to_metadata(
        self,
        entry: dict[str, Any],
        folder_path: str,
        permissions: list[dict[str, Any]],
        permissions_fetch_failed: bool = False,
    ) -> DriveFileMetadata:
        owners = entry.get("owners") or []
        return DriveFileMetadata(
            drive_file_id=entry["id"],
            title=entry.get("name", ""),
            mime_type=entry.get("mimeType", ""),
            drive_url=entry.get("webViewLink", ""),
            created_time=_parse_rfc3339(entry.get("createdTime")),
            modified_time=_parse_rfc3339(entry.get("modifiedTime")),
            md5_checksum=entry.get("md5Checksum", ""),
            folder_path=folder_path,
            parent_folder_ids=entry.get("parents") or [],
            shared_drive_id=entry.get("driveId", ""),
            owner_email=owners[0].get("emailAddress", "") if owners else "",
            # Drive v3 exposes no creator field (shared-drive files have no
            # owners either); recovering the creator needs the Revisions API,
            # which is a follow-up — never fabricate it from other fields.
            creator_email="",
            permissions=permissions,
            permissions_fetch_failed=permissions_fetch_failed,
        )


def _parse_rfc3339(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
