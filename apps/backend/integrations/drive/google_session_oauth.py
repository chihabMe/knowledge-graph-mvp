"""Identity-only Google OIDC flow that creates a Django browser session.

This is deliberately separate from the Drive authorization flow. It persists
no Google token and may never be used for Drive API calls.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import transaction
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import id_token
from google_auth_oauthlib.flow import Flow

from retrieval.identity import normalize_trusted_email

OPENID_SCOPE = "openid"
EMAIL_SCOPE = "https://www.googleapis.com/auth/userinfo.email"
REQUIRED_SCOPES = frozenset({OPENID_SCOPE, EMAIL_SCOPE})
GOOGLE_ISSUERS = frozenset({"accounts.google.com", "https://accounts.google.com"})
GOOGLE_AUTH_URI = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"

_SESSION_KEY = "google_identity_session_oauth"
_MAX_STATE_LENGTH = 256
_MAX_CODE_LENGTH = 4096

logger = logging.getLogger(__name__)


class GoogleSessionOAuthError(RuntimeError):
    """Controlled failure that never includes a provider payload or token."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _client_config() -> dict[str, dict[str, str]]:
    if not settings.GOOGLE_SESSION_OAUTH_ENABLED:
        raise GoogleSessionOAuthError("session_oauth_not_configured")
    return {
        "web": {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "auth_uri": GOOGLE_AUTH_URI,
            "token_uri": GOOGLE_TOKEN_URI,
        }
    }


def _new_flow(
    *,
    state: str | None = None,
    code_verifier: str | None = None,
    autogenerate_code_verifier: bool = False,
) -> Flow:
    try:
        flow = Flow.from_client_config(
            _client_config(),
            scopes=sorted(REQUIRED_SCOPES),
            state=state,
            code_verifier=code_verifier,
            autogenerate_code_verifier=autogenerate_code_verifier,
        )
    except (ValueError, KeyError) as exc:
        raise GoogleSessionOAuthError("session_oauth_not_configured") from exc
    flow.redirect_uri = settings.GOOGLE_SESSION_OAUTH_REDIRECT_URI
    return flow


def begin_session_authorization(*, session) -> str:
    """Create one-time state, nonce, and PKCE data for the current browser."""
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    flow = _new_flow(state=state, autogenerate_code_verifier=True)
    try:
        authorization_url, returned_state = flow.authorization_url(
            access_type="online",
            include_granted_scopes="false",
            prompt="select_account",
            hd=settings.GOOGLE_USER_OAUTH_ALLOWED_DOMAIN,
            nonce=nonce,
        )
    except (ValueError, KeyError) as exc:
        raise GoogleSessionOAuthError("authorization_start_failed") from exc

    code_verifier = getattr(flow, "code_verifier", None)
    if (
        not isinstance(returned_state, str)
        or not hmac.compare_digest(returned_state, state)
        or not isinstance(code_verifier, str)
        or not 43 <= len(code_verifier) <= 128
    ):
        raise GoogleSessionOAuthError("authorization_start_failed")

    session.cycle_key()
    session[_SESSION_KEY] = {
        "state_digest": _digest(state),
        "nonce_digest": _digest(nonce),
        "code_verifier": code_verifier,
        "created_at": int(time.time()),
    }
    session.save()
    return authorization_url


def _consume_state(*, session, state: str | None) -> dict[str, object]:
    stored = session.pop(_SESSION_KEY, None)
    session.save()
    if (
        not isinstance(stored, dict)
        or not isinstance(state, str)
        or not state
        or len(state) > _MAX_STATE_LENGTH
    ):
        raise GoogleSessionOAuthError("invalid_oauth_state")
    expected_digest = stored.get("state_digest")
    if not isinstance(expected_digest, str) or not hmac.compare_digest(
        expected_digest, _digest(state)
    ):
        raise GoogleSessionOAuthError("invalid_oauth_state")
    created_at = stored.get("created_at")
    now = int(time.time())
    if (
        not isinstance(created_at, int)
        or created_at > now
        or now - created_at > settings.GOOGLE_SESSION_OAUTH_STATE_MAX_AGE_SECONDS
    ):
        raise GoogleSessionOAuthError("invalid_oauth_state")
    nonce_digest = stored.get("nonce_digest")
    code_verifier = stored.get("code_verifier")
    if (
        not isinstance(nonce_digest, str)
        or not isinstance(code_verifier, str)
        or not 43 <= len(code_verifier) <= 128
    ):
        raise GoogleSessionOAuthError("invalid_oauth_state")
    return stored


def _verified_claims(raw_id_token: str) -> dict[str, object]:
    if not isinstance(raw_id_token, str) or not raw_id_token:
        raise GoogleSessionOAuthError("identity_token_invalid")
    try:
        claims = id_token.verify_oauth2_token(
            raw_id_token,
            GoogleAuthRequest(),
            settings.GOOGLE_CLIENT_ID,
        )
    except Exception as exc:
        raise GoogleSessionOAuthError("identity_token_invalid") from exc
    if not isinstance(claims, dict):
        raise GoogleSessionOAuthError("identity_token_invalid")
    return claims


def _validated_identity(
    claims: dict[str, object], *, expected_nonce_digest: str
) -> tuple[str, str, str]:
    issuer = claims.get("iss")
    audience = claims.get("aud")
    subject = claims.get("sub")
    email = claims.get("email")
    hosted_domain = claims.get("hd")
    nonce = claims.get("nonce")
    if (
        issuer not in GOOGLE_ISSUERS
        or audience != settings.GOOGLE_CLIENT_ID
        or not isinstance(subject, str)
        or not subject
        or len(subject) > 255
        or claims.get("email_verified") is not True
        or not isinstance(email, str)
        or not isinstance(hosted_domain, str)
        or not isinstance(nonce, str)
        or not hmac.compare_digest(expected_nonce_digest, _digest(nonce))
    ):
        raise GoogleSessionOAuthError("identity_token_invalid")
    try:
        normalized_email = normalize_trusted_email(email)
    except ValueError as exc:
        raise GoogleSessionOAuthError("identity_token_invalid") from exc
    allowed_domain = settings.GOOGLE_USER_OAUTH_ALLOWED_DOMAIN.lower()
    if normalized_email.rpartition("@")[2] != allowed_domain or not hmac.compare_digest(
        hosted_domain.lower(), allowed_domain
    ):
        raise GoogleSessionOAuthError("identity_not_allowed")
    return str(issuer), subject, normalized_email


def _oauth_username(*, issuer: str, subject: str) -> str:
    digest = hashlib.sha256(f"{issuer}\0{subject}".encode()).hexdigest()
    return f"google-oidc-{digest}"


def _session_user(*, issuer: str, subject: str, normalized_email: str):
    user_model = get_user_model()
    username = _oauth_username(issuer=issuer, subject=subject)
    with transaction.atomic():
        if (
            user_model.objects.select_for_update()
            .filter(email__iexact=normalized_email)
            .exclude(username=username)
            .exists()
        ):
            raise GoogleSessionOAuthError("identity_conflict")
        user, created = user_model.objects.select_for_update().get_or_create(
            username=username,
            defaults={
                "email": normalized_email,
                "is_active": True,
                "is_staff": False,
                "is_superuser": False,
            },
        )
        if user.is_staff or user.is_superuser or not user.is_active:
            raise GoogleSessionOAuthError("identity_not_allowed")
        if user.email and not hmac.compare_digest(user.email.lower(), normalized_email):
            raise GoogleSessionOAuthError("identity_conflict")
        update_fields: list[str] = []
        if user.email != normalized_email:
            user.email = normalized_email
            update_fields.append("email")
        if created or user.has_usable_password():
            user.set_unusable_password()
            update_fields.append("password")
        if update_fields:
            user.save(update_fields=update_fields)
    return user


def complete_session_authorization(
    *,
    session,
    state: str | None,
    code: str | None,
    provider_error: bool = False,
):
    """Verify Google identity and return a local user without persisting tokens."""
    stored = _consume_state(session=session, state=state)
    if provider_error or not isinstance(code, str) or not code or len(code) > _MAX_CODE_LENGTH:
        raise GoogleSessionOAuthError("authorization_response_invalid")

    flow = _new_flow(
        state=state,
        code_verifier=str(stored["code_verifier"]),
    )
    try:
        flow.fetch_token(code=code)
        credentials = flow.credentials
    except Exception as exc:
        logger.warning(
            "Google session OAuth exchange failed (%s).",
            exc.__class__.__name__,
        )
        raise GoogleSessionOAuthError("authorization_exchange_failed") from exc

    claims = _verified_claims(getattr(credentials, "id_token", ""))
    issuer, subject, normalized_email = _validated_identity(
        claims,
        expected_nonce_digest=str(stored["nonce_digest"]),
    )
    return _session_user(
        issuer=issuer,
        subject=subject,
        normalized_email=normalized_email,
    )
