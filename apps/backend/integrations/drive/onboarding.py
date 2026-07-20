"""Guided Open WebUI-to-Drive onboarding state and safe browser URLs."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlsplit, urlunsplit

from django.conf import settings
from django.urls import reverse
from django.utils import timezone

from integrations.drive.user_oauth import REQUIRED_SCOPES, UserDriveOAuthError
from integrations.models import (
    DriveConnection,
    GoogleDriveAuthorization,
    UserVisibilitySyncRun,
)
from retrieval.identity import normalize_trusted_email

NOT_CONNECTED = "not_connected"
SYNCING = "syncing"
READY = "ready"
REAUTHORIZATION_REQUIRED = "reauthorization_required"
TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"

TERMINAL_AUTHORIZATION_STATUSES = frozenset(
    {
        GoogleDriveAuthorization.Status.REFRESH_FAILED,
        GoogleDriveAuthorization.Status.REVOKED,
        GoogleDriveAuthorization.Status.SCOPE_MISSING,
    }
)


@dataclass(frozen=True)
class DriveConnectionState:
    configured: bool
    connected: bool
    status: str
    state: str

    def as_payload(self) -> dict[str, bool | str]:
        return {
            "configured": self.configured,
            "connected": self.connected,
            "status": self.status,
            "state": self.state,
        }


def _normalized_email(user_email: str) -> str:
    try:
        normalized = normalize_trusted_email(user_email)
    except ValueError as exc:
        raise UserDriveOAuthError("identity_unavailable") from exc
    if normalized.rpartition("@")[2] != settings.GOOGLE_USER_OAUTH_ALLOWED_DOMAIN.lower():
        raise UserDriveOAuthError("identity_not_allowed")
    return normalized


def _active_connection() -> DriveConnection:
    if settings.GOOGLE_PERMISSION_AUTHORITY != DriveConnection.PermissionAuthority.PER_USER_OAUTH:
        raise UserDriveOAuthError("oauth_not_configured")
    connections = list(
        DriveConnection.objects.filter(
            enabled=True,
            permission_authority=DriveConnection.PermissionAuthority.PER_USER_OAUTH,
        ).order_by("pk")[:2]
    )
    if len(connections) != 1:
        raise UserDriveOAuthError("connection_unavailable")
    connection = connections[0]
    if (
        not connection.effective_root_id
        or connection.workspace_domain.lower() != settings.GOOGLE_USER_OAUTH_ALLOWED_DOMAIN.lower()
    ):
        raise UserDriveOAuthError("connection_unavailable")
    return connection


def connection_state(*, user_email: str) -> DriveConnectionState:
    """Return a controlled readiness state without exposing authorization data."""
    try:
        normalized_email = _normalized_email(user_email)
        connection = _active_connection()
    except UserDriveOAuthError:
        return DriveConnectionState(False, False, "unavailable", TEMPORARILY_UNAVAILABLE)

    authorizations = list(
        GoogleDriveAuthorization.objects.filter(
            connection=connection,
            normalized_email=normalized_email,
            connection_generation=connection.authorization_generation,
        ).order_by("pk")[:2]
    )
    if len(authorizations) != 1:
        return DriveConnectionState(True, False, "disconnected", NOT_CONNECTED)

    authorization = authorizations[0]
    if authorization.status in TERMINAL_AUTHORIZATION_STATUSES:
        return DriveConnectionState(
            True,
            False,
            authorization.status,
            REAUTHORIZATION_REQUIRED,
        )
    if authorization.status == GoogleDriveAuthorization.Status.DISCONNECTED:
        return DriveConnectionState(True, False, authorization.status, NOT_CONNECTED)

    connected = bool(
        authorization.status == GoogleDriveAuthorization.Status.ACTIVE
        and bytes(authorization.encrypted_refresh_credential)
        and authorization.encryption_key_version
        and REQUIRED_SCOPES.issubset(set(authorization.granted_scopes))
    )
    if not connected:
        return DriveConnectionState(True, False, "disconnected", NOT_CONNECTED)

    latest_run = (
        UserVisibilitySyncRun.objects.filter(
            authorization=authorization,
            connection_generation=connection.authorization_generation,
            authorization_generation=authorization.authorization_generation,
        )
        .order_by("-created_at", "-pk")
        .first()
    )
    if latest_run and latest_run.status in {
        UserVisibilitySyncRun.Status.QUEUED,
        UserVisibilitySyncRun.Status.RUNNING,
    }:
        return DriveConnectionState(True, True, authorization.status, SYNCING)

    successful_at = authorization.last_successful_visibility_sync_at
    latest_run_invalidated_success = bool(
        latest_run
        and latest_run.status
        in {UserVisibilitySyncRun.Status.FAILED, UserVisibilitySyncRun.Status.PARTIAL}
        and (successful_at is None or latest_run.created_at >= successful_at)
    )
    evidence_fresh = bool(
        successful_at
        and successful_at
        >= timezone.now() - timedelta(seconds=settings.GOOGLE_USER_VISIBILITY_MAX_AGE_SECONDS)
    )
    state = (
        READY if evidence_fresh and not latest_run_invalidated_success else TEMPORARILY_UNAVAILABLE
    )
    return DriveConnectionState(True, True, authorization.status, state)


def session_onboarding_url() -> str:
    """Build the public bootstrap URL only from the validated session callback origin."""
    callback = urlsplit(settings.GOOGLE_SESSION_OAUTH_REDIRECT_URI)
    return urlunsplit(
        (callback.scheme, callback.netloc, reverse("google-session-oauth-start"), "", "")
    )


def webui_return_url() -> str:
    """Return the validated Open WebUI origin with a normalized trailing slash."""
    return settings.WEBUI_URL.rstrip("/") + "/"
