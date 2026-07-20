import math
from dataclasses import dataclass

import jwt
from django.conf import settings
from rest_framework.exceptions import AuthenticationFailed

from retrieval.identity import TrustedIdentityUnavailable, normalize_trusted_email

INVALID_IDENTITY_ASSERTION = "Invalid user identity assertion."
MAX_IDENTITY_TOKEN_CHARS = 4096


@dataclass(frozen=True)
class OpenWebUIUserPrincipal:
    """Immutable authenticated user carried by the verified Open WebUI JWT."""

    subject: str
    email: str

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_anonymous(self) -> bool:
        return False

    @property
    def pk(self) -> str:
        return self.subject


def _is_numeric_date(value) -> bool:
    return type(value) in {int, float} and math.isfinite(value)


def _invalid_identity() -> AuthenticationFailed:
    return AuthenticationFailed(INVALID_IDENTITY_ASSERTION)


def verify_identity_jwt(request) -> OpenWebUIUserPrincipal:
    """Verify the signed Open WebUI identity before it can select a subject."""
    token = request.headers.get(settings.OPEN_WEBUI_IDENTITY_JWT_HEADER)
    if (
        not isinstance(token, str)
        or not token
        or token != token.strip()
        or len(token) > MAX_IDENTITY_TOKEN_CHARS
    ):
        raise _invalid_identity()

    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") != "HS256":
            raise _invalid_identity()
        claims = jwt.decode(
            token,
            settings.OPEN_WEBUI_IDENTITY_JWT_SECRET,
            algorithms=["HS256"],
            issuer=settings.OPEN_WEBUI_IDENTITY_JWT_ISSUER,
            leeway=settings.OPEN_WEBUI_IDENTITY_JWT_CLOCK_SKEW_SECONDS,
            options={
                "require": ["iss", "sub", "iat", "exp", "email"],
                "verify_signature": True,
                "verify_iss": True,
                "verify_iat": True,
                "verify_exp": True,
            },
        )
    except AuthenticationFailed:
        raise
    except (jwt.InvalidTokenError, OverflowError, TypeError, ValueError) as exc:
        raise _invalid_identity() from exc

    subject = claims["sub"]
    issued_at = claims["iat"]
    expires_at = claims["exp"]
    if (
        not isinstance(subject, str)
        or not subject
        or subject != subject.strip()
        or len(subject) > 255
        or not _is_numeric_date(issued_at)
        or not _is_numeric_date(expires_at)
        or expires_at <= issued_at
        or expires_at - issued_at > settings.OPEN_WEBUI_IDENTITY_JWT_MAX_LIFETIME_SECONDS
        or not isinstance(claims["email"], str)
    ):
        raise _invalid_identity()

    try:
        email = normalize_trusted_email(claims["email"])
    except TrustedIdentityUnavailable as exc:
        raise _invalid_identity() from exc
    return OpenWebUIUserPrincipal(subject=subject, email=email)
