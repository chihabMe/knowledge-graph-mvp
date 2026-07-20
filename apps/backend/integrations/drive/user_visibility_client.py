"""Per-user Drive visibility checks over server-selected indexed documents only."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from integrations.drive.token_encryption import (
    CredentialDecryptionError,
    decrypt_refresh_credential,
)
from integrations.drive.user_oauth import REQUIRED_SCOPES
from integrations.models import (
    DriveConnection,
    GoogleDriveAuthorization,
    SourceDocument,
    UserDocumentVisibility,
)

TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
MAX_ATTEMPTS = 3
INITIAL_RETRY_DELAY_SECONDS = 0.25
TRANSIENT_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})
TRANSIENT_GOOGLE_REASONS = frozenset(
    {"rateLimitExceeded", "userRateLimitExceeded", "dailyLimitExceeded", "backendError"}
)


class UserVisibilityCheckError(RuntimeError):
    """Controlled batch failure with no token, email, file ID, or remote payload."""


@dataclass(frozen=True)
class IndexedVisibilityResult:
    source_document_id: int
    state: str
    reason_code: str


@dataclass(frozen=True)
class IndexedVisibilityBatch:
    authorization_id: int
    connection_generation: str
    authorization_generation: str
    results: tuple[IndexedVisibilityResult, ...]


def _load_client_secret() -> str:
    try:
        payload = json.loads(
            Path(settings.GOOGLE_USER_OAUTH_CLIENT_SECRET_FILE).read_text(encoding="utf-8")
        )
        web = payload["web"]
        if web["client_id"] != settings.GOOGLE_USER_OAUTH_CLIENT_ID:
            raise ValueError
        client_secret = web["client_secret"]
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        raise UserVisibilityCheckError("oauth_client_unavailable") from exc
    if not isinstance(client_secret, str) or not client_secret:
        raise UserVisibilityCheckError("oauth_client_unavailable")
    return client_secret


def _build_user_drive_service(authorization: GoogleDriveAuthorization):
    try:
        refresh_credential = decrypt_refresh_credential(
            ciphertext=bytes(authorization.encrypted_refresh_credential),
            key_version=authorization.encryption_key_version,
        )
    except CredentialDecryptionError as exc:
        raise UserVisibilityCheckError("credential_unavailable") from exc
    credentials = Credentials(
        token=None,
        refresh_token=refresh_credential,
        token_uri=TOKEN_ENDPOINT,
        client_id=settings.GOOGLE_USER_OAUTH_CLIENT_ID,
        client_secret=_load_client_secret(),
        scopes=sorted(REQUIRED_SCOPES),
    )
    try:
        credentials.refresh(GoogleAuthRequest())
    except Exception as exc:
        raise UserVisibilityCheckError("credential_refresh_failed") from exc
    return build("drive", "v3", credentials=credentials, cache_discovery=False)


def _load_authorization(authorization_id: int) -> GoogleDriveAuthorization:
    try:
        authorization = GoogleDriveAuthorization.objects.select_related("connection").get(
            pk=authorization_id
        )
    except GoogleDriveAuthorization.DoesNotExist as exc:
        raise UserVisibilityCheckError("authorization_unavailable") from exc
    connection = authorization.connection
    if (
        settings.GOOGLE_PERMISSION_AUTHORITY != DriveConnection.PermissionAuthority.PER_USER_OAUTH
        or not connection.enabled
        or connection.permission_authority != DriveConnection.PermissionAuthority.PER_USER_OAUTH
        or not connection.effective_root_id
        or authorization.status != GoogleDriveAuthorization.Status.ACTIVE
        or authorization.connection_generation != connection.authorization_generation
        or not REQUIRED_SCOPES.issubset(set(authorization.granted_scopes))
        or not bytes(authorization.encrypted_refresh_credential)
        or not authorization.encryption_key_version
    ):
        raise UserVisibilityCheckError("authorization_unavailable")
    return authorization


def _indexed_documents(connection: DriveConnection) -> list[SourceDocument]:
    documents = list(
        SourceDocument.objects.filter(connection=connection, active_in_scope=True).order_by("pk")
    )
    if len(documents) > settings.GOOGLE_USER_VISIBILITY_MAX_DOCUMENTS:
        raise UserVisibilityCheckError("document_cap_exceeded")
    return documents


def _http_status(exc: HttpError) -> int:
    return int(getattr(exc.resp, "status", 0) or 0)


def _google_error_reasons(exc: HttpError) -> set[str]:
    content = exc.content
    if not isinstance(content, bytes) or len(content) > 65_536:
        return set()
    try:
        payload = json.loads(content.decode("utf-8"))
        errors = payload.get("error", {}).get("errors", [])
    except (UnicodeError, json.JSONDecodeError, AttributeError):
        return set()
    return {
        item.get("reason")
        for item in errors
        if isinstance(item, dict) and isinstance(item.get("reason"), str)
    }


def _check_one(service, document: SourceDocument, *, sleep) -> IndexedVisibilityResult:
    for attempt in range(MAX_ATTEMPTS):
        try:
            payload = (
                service.files()
                .get(
                    fileId=document.drive_file_id,
                    supportsAllDrives=True,
                    fields="id,trashed",
                )
                .execute(num_retries=0)
            )
        except HttpError as exc:
            status = _http_status(exc)
            transient = status in TRANSIENT_HTTP_STATUSES or bool(
                _google_error_reasons(exc) & TRANSIENT_GOOGLE_REASONS
            )
            if transient and attempt + 1 < MAX_ATTEMPTS:
                sleep(INITIAL_RETRY_DELAY_SECONDS * (2**attempt))
                continue
            if status in {403, 404, 410} and not transient:
                return IndexedVisibilityResult(
                    document.pk, UserDocumentVisibility.State.DENIED, "inaccessible"
                )
            return IndexedVisibilityResult(
                document.pk,
                UserDocumentVisibility.State.UNKNOWN,
                "transient_failure" if transient else "drive_api_error",
            )
        except (OSError, TimeoutError):
            if attempt + 1 < MAX_ATTEMPTS:
                sleep(INITIAL_RETRY_DELAY_SECONDS * (2**attempt))
                continue
            return IndexedVisibilityResult(
                document.pk, UserDocumentVisibility.State.UNKNOWN, "transient_failure"
            )
        if not isinstance(payload, dict) or payload.get("id") != document.drive_file_id:
            return IndexedVisibilityResult(
                document.pk, UserDocumentVisibility.State.UNKNOWN, "malformed_response"
            )
        if payload.get("trashed") is not False:
            return IndexedVisibilityResult(
                document.pk, UserDocumentVisibility.State.DENIED, "trashed"
            )
        return IndexedVisibilityResult(
            document.pk, UserDocumentVisibility.State.VERIFIED_VISIBLE, ""
        )
    raise AssertionError("bounded retry loop exhausted unexpectedly")


class IndexedDriveVisibilityClient:
    """Checks every active indexed row; callers cannot supply or widen file IDs."""

    def __init__(self, *, service=None, sleep=time.sleep):
        self._service = service
        self._sleep = sleep

    def check_authorization(self, authorization_id: int) -> IndexedVisibilityBatch:
        if isinstance(authorization_id, bool) or not isinstance(authorization_id, int):
            raise UserVisibilityCheckError("authorization_unavailable")
        authorization = _load_authorization(authorization_id)
        documents = _indexed_documents(authorization.connection)
        service = self._service or _build_user_drive_service(authorization)
        results = tuple(_check_one(service, document, sleep=self._sleep) for document in documents)
        return IndexedVisibilityBatch(
            authorization_id=authorization.pk,
            connection_generation=str(authorization.connection_generation),
            authorization_generation=str(authorization.authorization_generation),
            results=results,
        )
