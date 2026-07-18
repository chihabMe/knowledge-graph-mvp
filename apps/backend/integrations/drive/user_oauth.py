"""Admin-approved per-user Google Drive OAuth authorization flow.

This boundary stores only encrypted refresh credentials. It never lists Drive,
accepts document identifiers, exports content, or creates authorization grants.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
import uuid
from dataclasses import dataclass
from urllib import parse, request

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import id_token
from google_auth_oauthlib.flow import Flow

from authorization.oauth_viewer import delete_oauth_viewer_relationships
from integrations.drive.token_encryption import (
    CredentialDecryptionError,
    CredentialEncryptionError,
    decrypt_refresh_credential,
    encrypt_refresh_credential,
)
from integrations.models import (
    DriveConnection,
    GoogleDriveAuthorization,
    UserDocumentVisibility,
)
from retrieval.identity import normalize_trusted_email

DRIVE_METADATA_SCOPE = "https://www.googleapis.com/auth/drive.metadata.readonly"
EMAIL_SCOPE = "https://www.googleapis.com/auth/userinfo.email"
OPENID_SCOPE = "openid"
REQUIRED_SCOPES = frozenset({DRIVE_METADATA_SCOPE, EMAIL_SCOPE, OPENID_SCOPE})
GOOGLE_ISSUERS = frozenset({"accounts.google.com", "https://accounts.google.com"})
GOOGLE_REVOCATION_ENDPOINT = "https://oauth2.googleapis.com/revoke"

_SESSION_KEY = "google_user_drive_oauth"
_MAX_STATE_LENGTH = 256
_MAX_CODE_LENGTH = 4096

logger = logging.getLogger(__name__)


class UserDriveOAuthError(RuntimeError):
    """Controlled failure that never includes a provider payload or credential."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class OAuthStatus:
    configured: bool
    connected: bool
    status: str

    def as_payload(self) -> dict[str, bool | str]:
        return {
            "configured": self.configured,
            "connected": self.connected,
            "status": self.status,
        }


def _normalize_user_email(user_email: str) -> str:
    try:
        normalized = normalize_trusted_email(user_email)
    except ValueError as exc:
        raise UserDriveOAuthError("identity_unavailable") from exc
    allowed_domain = settings.GOOGLE_USER_OAUTH_ALLOWED_DOMAIN.lower()
    if normalized.rpartition("@")[2] != allowed_domain:
        raise UserDriveOAuthError("identity_not_allowed")
    return normalized


def _active_per_user_connection(*, lock: bool = False) -> DriveConnection:
    if settings.GOOGLE_PERMISSION_AUTHORITY != DriveConnection.PermissionAuthority.PER_USER_OAUTH:
        raise UserDriveOAuthError("oauth_not_configured")
    queryset = DriveConnection.objects.filter(
        enabled=True,
        permission_authority=DriveConnection.PermissionAuthority.PER_USER_OAUTH,
    ).order_by("pk")
    if lock:
        queryset = queryset.select_for_update()
    connections = list(queryset[:2])
    if len(connections) != 1:
        raise UserDriveOAuthError("connection_unavailable")
    connection = connections[0]
    if (
        not connection.effective_root_id
        or connection.workspace_domain.lower() != settings.GOOGLE_USER_OAUTH_ALLOWED_DOMAIN.lower()
    ):
        raise UserDriveOAuthError("connection_unavailable")
    return connection


def _new_flow(
    *,
    state: str | None = None,
    code_verifier: str | None = None,
    autogenerate_code_verifier: bool = False,
) -> Flow:
    try:
        flow = Flow.from_client_secrets_file(
            settings.GOOGLE_USER_OAUTH_CLIENT_SECRET_FILE,
            scopes=sorted(REQUIRED_SCOPES),
            state=state,
            code_verifier=code_verifier,
            autogenerate_code_verifier=autogenerate_code_verifier,
        )
    except (OSError, ValueError, KeyError) as exc:
        raise UserDriveOAuthError("oauth_configuration_unavailable") from exc
    if getattr(flow, "client_config", {}).get("client_id") != settings.GOOGLE_USER_OAUTH_CLIENT_ID:
        raise UserDriveOAuthError("oauth_configuration_unavailable")
    flow.redirect_uri = settings.GOOGLE_USER_OAUTH_REDIRECT_URI
    return flow


def _state_digest(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def begin_authorization(*, session, user_email: str) -> str:
    """Create session-bound state and return the Google authorization URL."""
    normalized_email = _normalize_user_email(user_email)
    connection = _active_per_user_connection()
    state = secrets.token_urlsafe(32)
    flow = _new_flow(state=state, autogenerate_code_verifier=True)
    try:
        authorization_url, returned_state = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="false",
            prompt="consent",
            login_hint=normalized_email,
            hd=settings.GOOGLE_USER_OAUTH_ALLOWED_DOMAIN,
        )
    except (ValueError, KeyError) as exc:
        session.pop(_SESSION_KEY, None)
        session.save()
        raise UserDriveOAuthError("authorization_start_failed") from exc
    code_verifier = getattr(flow, "code_verifier", None)
    if (
        not hmac.compare_digest(str(returned_state), state)
        or not isinstance(code_verifier, str)
        or not 43 <= len(code_verifier) <= 128
    ):
        raise UserDriveOAuthError("authorization_start_failed")

    session.cycle_key()
    session[_SESSION_KEY] = {
        "state_digest": _state_digest(state),
        "code_verifier": code_verifier,
        "created_at": int(time.time()),
        "normalized_email": normalized_email,
        "connection_id": connection.pk,
        "connection_generation": str(connection.authorization_generation),
    }
    session.save()
    return authorization_url


def _consume_state(*, session, state: str | None, user_email: str) -> dict[str, object]:
    """Consume state before any remote exchange, even when validation fails."""
    stored = session.pop(_SESSION_KEY, None)
    session.save()
    if (
        not isinstance(stored, dict)
        or not isinstance(state, str)
        or not state
        or len(state) > _MAX_STATE_LENGTH
    ):
        raise UserDriveOAuthError("invalid_oauth_state")
    expected_digest = stored.get("state_digest")
    if not isinstance(expected_digest, str) or not hmac.compare_digest(
        expected_digest, _state_digest(state)
    ):
        raise UserDriveOAuthError("invalid_oauth_state")
    created_at = stored.get("created_at")
    if (
        not isinstance(created_at, int)
        or created_at > int(time.time())
        or int(time.time()) - created_at > settings.GOOGLE_USER_OAUTH_STATE_MAX_AGE_SECONDS
    ):
        raise UserDriveOAuthError("invalid_oauth_state")
    normalized_email = _normalize_user_email(user_email)
    if not hmac.compare_digest(str(stored.get("normalized_email", "")), normalized_email):
        raise UserDriveOAuthError("identity_mismatch")
    code_verifier = stored.get("code_verifier")
    if not isinstance(code_verifier, str) or not 43 <= len(code_verifier) <= 128:
        raise UserDriveOAuthError("invalid_oauth_state")
    return stored


def _verified_claims(raw_id_token: str) -> dict[str, object]:
    if not isinstance(raw_id_token, str) or not raw_id_token:
        raise UserDriveOAuthError("identity_token_invalid")
    try:
        claims = id_token.verify_oauth2_token(
            raw_id_token,
            GoogleAuthRequest(),
            settings.GOOGLE_USER_OAUTH_CLIENT_ID,
        )
    except Exception as exc:
        raise UserDriveOAuthError("identity_token_invalid") from exc
    if not isinstance(claims, dict):
        raise UserDriveOAuthError("identity_token_invalid")
    return claims


def _validate_claims(claims: dict[str, object], *, expected_email: str) -> tuple[str, str]:
    issuer = claims.get("iss")
    audience = claims.get("aud")
    subject = claims.get("sub")
    email = claims.get("email")
    hosted_domain = claims.get("hd")
    if (
        issuer not in GOOGLE_ISSUERS
        or audience != settings.GOOGLE_USER_OAUTH_CLIENT_ID
        or not isinstance(subject, str)
        or not subject
        or len(subject) > 255
        or claims.get("email_verified") is not True
        or not isinstance(email, str)
        or not isinstance(hosted_domain, str)
    ):
        raise UserDriveOAuthError("identity_token_invalid")
    normalized_email = _normalize_user_email(email)
    if not hmac.compare_digest(normalized_email, expected_email) or not hmac.compare_digest(
        hosted_domain.lower(), settings.GOOGLE_USER_OAUTH_ALLOWED_DOMAIN.lower()
    ):
        raise UserDriveOAuthError("identity_mismatch")
    return str(issuer), subject


def _granted_scopes(flow: Flow, credentials) -> list[str]:
    token = getattr(getattr(flow, "oauth2session", None), "token", {})
    raw_scopes = token.get("scope") if isinstance(token, dict) else None
    if isinstance(raw_scopes, str):
        scopes = raw_scopes.split()
    elif isinstance(raw_scopes, (list, tuple, set)):
        scopes = list(raw_scopes)
    else:
        scopes = list(getattr(credentials, "granted_scopes", None) or [])
    normalized = {EMAIL_SCOPE if scope == "email" else str(scope) for scope in scopes}
    if not REQUIRED_SCOPES.issubset(normalized):
        raise UserDriveOAuthError("required_scope_missing")
    if any(
        scope.startswith("https://www.googleapis.com/auth/drive") and scope != DRIVE_METADATA_SCOPE
        for scope in normalized
    ):
        raise UserDriveOAuthError("overbroad_drive_scope")
    return sorted(normalized)


def _disconnect_authorization_row(authorization: GoogleDriveAuthorization, *, now) -> None:
    UserDocumentVisibility.objects.filter(authorization=authorization).delete()
    authorization.authorization_generation = uuid.uuid4()
    authorization.status = GoogleDriveAuthorization.Status.DISCONNECTED
    authorization.encrypted_refresh_credential = b""
    authorization.encryption_key_version = ""
    authorization.granted_scopes = []
    authorization.disconnected_at = now
    authorization.last_successful_visibility_sync_at = None
    authorization.save(
        update_fields=[
            "authorization_generation",
            "status",
            "encrypted_refresh_credential",
            "encryption_key_version",
            "granted_scopes",
            "disconnected_at",
            "last_successful_visibility_sync_at",
            "updated_at",
        ]
    )


def complete_authorization(
    *,
    session,
    user_email: str,
    state: str | None,
    authorization_code: str | None,
    provider_error: bool = False,
) -> GoogleDriveAuthorization:
    """Exchange a one-time code and persist only an encrypted refresh credential."""
    stored = _consume_state(session=session, state=state, user_email=user_email)
    if (
        provider_error
        or not isinstance(authorization_code, str)
        or not authorization_code
        or len(authorization_code) > _MAX_CODE_LENGTH
    ):
        raise UserDriveOAuthError("authorization_response_invalid")

    flow = _new_flow(
        state=state,
        code_verifier=str(stored["code_verifier"]),
    )
    try:
        # State was already consumed and compared above. Passing only the code
        # keeps localhost HTTP callbacks out of oauthlib's transport parsing;
        # the actual token exchange still goes to Google's HTTPS endpoint.
        flow.fetch_token(code=authorization_code)
        credentials = flow.credentials
    except Exception as exc:
        logger.warning(
            "Google user OAuth exchange failed (%s).",
            exc.__class__.__name__,
        )
        raise UserDriveOAuthError("authorization_exchange_failed") from exc

    claims = _verified_claims(getattr(credentials, "id_token", ""))
    expected_email = str(stored["normalized_email"])
    issuer, subject = _validate_claims(claims, expected_email=expected_email)
    granted_scopes = _granted_scopes(flow, credentials)
    refresh_credential = getattr(credentials, "refresh_token", None)

    now = timezone.now()
    with transaction.atomic():
        connection = _active_per_user_connection(lock=True)
        if connection.pk != stored.get("connection_id") or str(
            connection.authorization_generation
        ) != stored.get("connection_generation"):
            raise UserDriveOAuthError("connection_changed")

        same_subject = (
            GoogleDriveAuthorization.objects.select_for_update()
            .filter(connection=connection, google_issuer=issuer, google_subject=subject)
            .first()
        )
        if not refresh_credential and (
            same_subject is None or not bytes(same_subject.encrypted_refresh_credential)
        ):
            raise UserDriveOAuthError("refresh_credential_missing")
        if not refresh_credential:
            try:
                decrypt_refresh_credential(
                    ciphertext=bytes(same_subject.encrypted_refresh_credential),
                    key_version=same_subject.encryption_key_version,
                )
            except CredentialDecryptionError as exc:
                raise UserDriveOAuthError("credential_storage_unavailable") from exc

        encrypted = None
        if refresh_credential:
            try:
                encrypted = encrypt_refresh_credential(refresh_credential)
            except CredentialEncryptionError as exc:
                raise UserDriveOAuthError("credential_storage_unavailable") from exc

        conflicting = list(
            GoogleDriveAuthorization.objects.select_for_update().filter(
                connection=connection,
                normalized_email=expected_email,
            )
        )
        for authorization in conflicting:
            if same_subject is None or authorization.pk != same_subject.pk:
                _disconnect_authorization_row(authorization, now=now)

        authorization = same_subject or GoogleDriveAuthorization(
            connection=connection,
            google_issuer=issuer,
            google_subject=subject,
        )
        if authorization.pk:
            UserDocumentVisibility.objects.filter(authorization=authorization).delete()
        authorization.normalized_email = expected_email
        authorization.workspace_domain = settings.GOOGLE_USER_OAUTH_ALLOWED_DOMAIN.lower()
        authorization.connection_generation = connection.authorization_generation
        authorization.authorization_generation = uuid.uuid4()
        authorization.granted_scopes = granted_scopes
        authorization.status = GoogleDriveAuthorization.Status.ACTIVE
        authorization.connected_at = now
        authorization.last_refreshed_at = now
        authorization.last_successful_visibility_sync_at = None
        authorization.disconnected_at = None
        if encrypted is not None:
            authorization.encrypted_refresh_credential = encrypted.ciphertext
            authorization.encryption_key_version = encrypted.key_version
        authorization.save()
    return authorization


def authorization_status(*, user_email: str) -> OAuthStatus:
    try:
        normalized_email = _normalize_user_email(user_email)
        connection = _active_per_user_connection()
    except UserDriveOAuthError:
        return OAuthStatus(configured=False, connected=False, status="unavailable")
    authorizations = list(
        GoogleDriveAuthorization.objects.filter(
            connection=connection,
            normalized_email=normalized_email,
            connection_generation=connection.authorization_generation,
        ).order_by("pk")[:2]
    )
    if len(authorizations) != 1:
        return OAuthStatus(configured=True, connected=False, status="disconnected")
    authorization = authorizations[0]
    connected = bool(
        authorization.status == GoogleDriveAuthorization.Status.ACTIVE
        and bytes(authorization.encrypted_refresh_credential)
        and authorization.encryption_key_version
        and REQUIRED_SCOPES.issubset(set(authorization.granted_scopes))
    )
    return OAuthStatus(
        configured=True,
        connected=connected,
        status=authorization.status if connected else "disconnected",
    )


def _revoke_refresh_credential(refresh_credential: str) -> None:
    payload = parse.urlencode({"token": refresh_credential}).encode("ascii")
    revoke_request = request.Request(
        GOOGLE_REVOCATION_ENDPOINT,
        data=payload,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with request.urlopen(revoke_request, timeout=10) as response:  # noqa: S310
        if response.status >= 400:
            raise OSError("Google revocation was not accepted.")


def disconnect_authorization(*, user_email: str) -> None:
    """Deny locally and wipe ciphertext before best-effort remote revocation."""
    normalized_email = _normalize_user_email(user_email)
    refresh_credentials: list[str] = []
    with transaction.atomic():
        connection = _active_per_user_connection(lock=True)
        authorizations = list(
            GoogleDriveAuthorization.objects.select_for_update().filter(
                connection=connection,
                normalized_email=normalized_email,
            )
        )
        now = timezone.now()
        for authorization in authorizations:
            ciphertext = bytes(authorization.encrypted_refresh_credential)
            if ciphertext and authorization.encryption_key_version:
                try:
                    refresh_credentials.append(
                        decrypt_refresh_credential(
                            ciphertext=ciphertext,
                            key_version=authorization.encryption_key_version,
                        )
                    )
                except CredentialDecryptionError:
                    pass
            _disconnect_authorization_row(authorization, now=now)

    try:
        delete_oauth_viewer_relationships(
            connection=connection,
            user_email=normalized_email,
        )
    except Exception as exc:
        # PostgreSQL denial is immediate and authoritative. A stale direct
        # tuple cannot grant without the now-deleted fresh evidence; the
        # authority cutover's connection-wide cleanup also removes leftovers.
        logger.warning(
            "SpiceDB oauth_viewer cleanup failed during disconnect (%s).",
            exc.__class__.__name__,
        )

    for refresh_credential in refresh_credentials:
        try:
            _revoke_refresh_credential(refresh_credential)
        except Exception:
            # Local denial and credential deletion are authoritative. Provider
            # availability must not undo them or expose a remote response.
            continue
