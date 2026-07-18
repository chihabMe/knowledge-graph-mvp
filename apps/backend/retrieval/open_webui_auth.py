from dataclasses import dataclass
from secrets import compare_digest

from django.conf import settings
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from retrieval.open_webui_identity import verify_identity_jwt

INVALID_SERVICE_CREDENTIALS = "Invalid service credentials."
INVALID_CHAT_CREDENTIALS = "Invalid credentials."


@dataclass(frozen=True)
class OpenWebUIServicePrincipal:
    """Authenticated Open WebUI service; it never represents an end user."""

    service_name: str = "open-webui"

    @property
    def is_authenticated(self) -> bool:
        return True

    @property
    def is_anonymous(self) -> bool:
        return False

    @property
    def pk(self) -> str:
        return self.service_name


def verify_service_bearer(request) -> OpenWebUIServicePrincipal:
    """Verify the dedicated Open WebUI service key without selecting a user."""
    if not settings.OPEN_WEBUI_COMPATIBLE_API_ENABLED:
        raise AuthenticationFailed(INVALID_SERVICE_CREDENTIALS)

    parts = get_authorization_header(request).split()
    if len(parts) != 2 or parts[0].lower() != b"bearer" or not parts[1]:
        raise AuthenticationFailed(INVALID_SERVICE_CREDENTIALS)

    expected = settings.OPEN_WEBUI_BACKEND_API_KEY.encode("utf-8")
    if not compare_digest(parts[1], expected):
        raise AuthenticationFailed(INVALID_SERVICE_CREDENTIALS)
    return OpenWebUIServicePrincipal()


class OpenWebUIServiceAuthentication(BaseAuthentication):
    """DRF authentication for service-only compatible API requests."""

    def authenticate(self, request):
        return verify_service_bearer(request), None

    def authenticate_header(self, request) -> str:
        return "Bearer"


class OpenWebUIUserAuthentication(BaseAuthentication):
    """Require the Open WebUI service first, then its signed user assertion."""

    def authenticate(self, request):
        try:
            verify_service_bearer(request)
            principal = verify_identity_jwt(request)
        except AuthenticationFailed as exc:
            # Do not expose whether a guessed service key was correct.
            raise AuthenticationFailed(INVALID_CHAT_CREDENTIALS) from exc
        return principal, None

    def authenticate_header(self, request) -> str:
        return "Bearer"
