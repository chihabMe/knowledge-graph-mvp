"""Shared Google credential loading for Drive API clients.

Drive and Directory construct credentials from the same mounted key file;
this module is the single place that validates credential paths and parses
tokens, so a credential fix cannot land in one client and miss the other.
"""

import json
import os
from pathlib import Path

from django.conf import settings


class ServiceAccountKeyError(RuntimeError):
    """Reason-coded key failure; callers map reasons to their domain errors.

    Reasons: "unreadable_path" (the path could not be inspected),
    "missing_or_empty" (unset path, absent file, or the /dev/null bootstrap
    mount), "invalid_key" (contents do not parse as key JSON).
    """

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class OAuthCredentialError(RuntimeError):
    """Controlled failure while loading development OAuth credentials."""


def _path_missing_or_empty(path: str) -> bool:
    try:
        return not path or not os.path.exists(path) or os.path.getsize(path) == 0
    except OSError as exc:
        raise OAuthCredentialError("Google OAuth credential path is unreadable.") from exc


def load_service_account_credentials(scopes, *, subject=""):
    key_path = settings.GOOGLE_SERVICE_ACCOUNT_FILE
    try:
        missing_or_empty = (
            not key_path or not os.path.exists(key_path) or os.path.getsize(key_path) == 0
        )
    except OSError as exc:
        raise ServiceAccountKeyError("unreadable_path") from exc
    if missing_or_empty:
        raise ServiceAccountKeyError("missing_or_empty")

    # Imported lazily so tests that inject a fake service never need
    # Google credentials or the discovery cache.
    from google.oauth2 import service_account

    try:
        credentials = service_account.Credentials.from_service_account_file(
            key_path, scopes=list(scopes)
        )
    except (OSError, ValueError) as exc:
        raise ServiceAccountKeyError("invalid_key") from exc
    if subject:
        credentials = credentials.with_subject(subject)
    return credentials


def load_oauth_credentials(scopes):
    """Load and refresh a development-only authorized-user token.

    The interactive consent flow is intentionally separate from this loader;
    ``drive_oauth_login`` creates the token once, while workers only read and
    refresh it. No token contents are logged.
    """
    token_path = settings.GOOGLE_OAUTH_TOKEN_FILE
    if _path_missing_or_empty(token_path):
        raise OAuthCredentialError(
            "Google OAuth authorization is not configured. Run drive_oauth_login first."
        )

    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    try:
        credentials = Credentials.from_authorized_user_file(token_path, scopes=list(scopes))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise OAuthCredentialError("Google OAuth token file is invalid.") from exc

    if credentials.valid:
        return credentials
    if not credentials.expired or not credentials.refresh_token:
        raise OAuthCredentialError(
            "Google OAuth authorization is missing or expired. Run drive_oauth_login again."
        )

    try:
        credentials.refresh(Request())
        _persist_oauth_credentials(credentials, token_path)
    except Exception as exc:  # Google auth errors are intentionally normalized.
        raise OAuthCredentialError(
            "Google OAuth token refresh failed. Run drive_oauth_login again."
        ) from exc
    return credentials


def _persist_oauth_credentials(credentials, token_path: str) -> None:
    """Atomically persist a refreshed token with owner-only permissions."""
    destination = Path(token_path)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(credentials.to_json(), encoding="utf-8")
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise OAuthCredentialError("Google OAuth token could not be persisted.") from exc
