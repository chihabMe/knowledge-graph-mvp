from django.core.exceptions import ValidationError
from django.core.validators import validate_email


class TrustedIdentityUnavailable(ValueError):
    """Raised when server-authenticated state has no usable Drive identity."""


def trusted_user_email(user) -> str:
    """Return the normalized server-side user email; never inspect request JSON."""
    if user is None or not user.is_authenticated:
        raise TrustedIdentityUnavailable
    email = str(getattr(user, "email", "")).strip().lower()
    try:
        validate_email(email)
    except ValidationError as exc:
        raise TrustedIdentityUnavailable from exc
    return email
