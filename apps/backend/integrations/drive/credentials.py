"""Shared service-account key loading for every Google API client.

Drive and Directory construct credentials from the same mounted key file;
this module is the single place that validates the path and parses the key,
so a key-file fix cannot land in one client and miss the other.
"""

import os

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
