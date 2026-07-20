from django.core.exceptions import ValidationError
from django.core.validators import validate_email


class TrustedIdentityUnavailable(ValueError):
    """Raised when server-authenticated state has no usable Drive identity."""


def normalize_trusted_email(value) -> str:
    """Normalize an already-authenticated identity into the SpiceDB subject form."""
    email = str(value).strip().lower()
    try:
        validate_email(email)
    except ValidationError as exc:
        raise TrustedIdentityUnavailable from exc
    return email


def trusted_user_email(user) -> str:
    """Return the normalized server-side user email; never inspect request JSON."""
    if user is None or not user.is_authenticated:
        raise TrustedIdentityUnavailable
    return normalize_trusted_email(getattr(user, "email", ""))
