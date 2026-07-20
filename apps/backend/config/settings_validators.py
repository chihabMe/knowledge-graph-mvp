import base64
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from django.core.exceptions import ImproperlyConfigured

HTTP_TOKEN_PATTERN = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")
BEARER_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9._~+/=-]+$")
MODEL_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
INSECURE_SECRET_PREFIXES = ("change-this", "django-insecure-", "unsafe-")
GOOGLE_PERMISSION_AUTHORITIES = frozenset({"delegated_acl", "per_user_oauth"})
GOOGLE_USER_TOKEN_KEY_VERSION_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
GOOGLE_USER_OAUTH_CLIENT_ID_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._-]{5,250}\.apps\.googleusercontent\.com$"
)
GOOGLE_WORKSPACE_DOMAIN_PATTERN = re.compile(
    r"^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"
)
GOOGLE_USER_OAUTH_CALLBACK_PATH = "/api/drive/oauth/callback"
GOOGLE_SESSION_OAUTH_CALLBACK_PATH = "/api/session/google/callback"
GOOGLE_USER_TOKEN_KEYRING_MAX_BYTES = 16_384


def validate_drive_onboarding_urls(
    *,
    enabled: bool,
    session_oauth_enabled: bool,
    webui_url: str,
    development_context: bool,
) -> None:
    """Require a browser-safe Open WebUI return origin for guided onboarding."""
    if not enabled:
        return
    if not session_oauth_enabled:
        raise ImproperlyConfigured(
            "GOOGLE_SESSION_OAUTH_ENABLED must be true when guided Drive onboarding is enabled."
        )
    parsed = urlsplit(webui_url)
    local_http_allowed = (
        development_context
        and parsed.scheme == "http"
        and parsed.hostname in {"localhost", "127.0.0.1"}
    )
    if (
        (parsed.scheme != "https" and not local_http_allowed)
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ImproperlyConfigured(
            "WEBUI_URL must be an exact HTTPS origin when guided Drive onboarding is enabled."
        )


def validate_open_webui_compatible_settings(
    *,
    enabled: bool,
    backend_api_key: str,
    identity_jwt_secret: str,
    webui_secret_key: str,
    identity_jwt_header: str,
    identity_jwt_issuer: str,
    identity_jwt_max_lifetime_seconds: int,
    identity_jwt_clock_skew_seconds: int,
    model_id: str,
) -> None:
    """Fail at startup when the compatible API trust boundary is unsafe."""
    if not HTTP_TOKEN_PATTERN.fullmatch(identity_jwt_header):
        raise ImproperlyConfigured(
            "OPEN_WEBUI_IDENTITY_JWT_HEADER must be a valid HTTP header name."
        )
    if not identity_jwt_issuer or identity_jwt_issuer != identity_jwt_issuer.strip():
        raise ImproperlyConfigured(
            "OPEN_WEBUI_IDENTITY_JWT_ISSUER must be non-empty without surrounding whitespace."
        )
    if len(identity_jwt_issuer) > 255:
        raise ImproperlyConfigured("OPEN_WEBUI_IDENTITY_JWT_ISSUER must be at most 255 characters.")
    if not 1 <= identity_jwt_max_lifetime_seconds <= 300:
        raise ImproperlyConfigured(
            "OPEN_WEBUI_IDENTITY_JWT_MAX_LIFETIME_SECONDS must be between 1 and 300."
        )
    if not 0 <= identity_jwt_clock_skew_seconds <= 30:
        raise ImproperlyConfigured(
            "OPEN_WEBUI_IDENTITY_JWT_CLOCK_SKEW_SECONDS must be between 0 and 30."
        )
    if not MODEL_ID_PATTERN.fullmatch(model_id):
        raise ImproperlyConfigured(
            "OPEN_WEBUI_MODEL_ID must start with a letter or number and contain only "
            "letters, numbers, dots, underscores, or hyphens (maximum 128 characters)."
        )

    if not enabled:
        return

    secrets = {
        "OPEN_WEBUI_BACKEND_API_KEY": backend_api_key,
        "OPEN_WEBUI_IDENTITY_JWT_SECRET": identity_jwt_secret,
    }
    for setting_name, value in secrets.items():
        if len(value) < 32 or value.startswith(INSECURE_SECRET_PREFIXES):
            raise ImproperlyConfigured(
                f"{setting_name} must contain at least 32 non-default characters when "
                "OPEN_WEBUI_COMPATIBLE_API_ENABLED is true."
            )
        if value != value.strip():
            raise ImproperlyConfigured(f"{setting_name} must not contain surrounding whitespace.")

    if not BEARER_TOKEN_PATTERN.fullmatch(backend_api_key):
        raise ImproperlyConfigured("OPEN_WEBUI_BACKEND_API_KEY must be a valid bearer token value.")

    if backend_api_key == identity_jwt_secret:
        raise ImproperlyConfigured(
            "OPEN_WEBUI_BACKEND_API_KEY and OPEN_WEBUI_IDENTITY_JWT_SECRET must be distinct."
        )
    if webui_secret_key and webui_secret_key in {backend_api_key, identity_jwt_secret}:
        raise ImproperlyConfigured(
            "Open WebUI service and identity secrets must not reuse WEBUI_SECRET_KEY."
        )


def validate_google_session_oauth_settings(
    *,
    enabled: bool,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    allowed_domain: str,
    state_max_age_seconds: int,
    development_context: bool,
) -> None:
    """Fail closed when the identity-only Django session bridge is unsafe."""
    if not 60 <= state_max_age_seconds <= 900:
        raise ImproperlyConfigured(
            "GOOGLE_SESSION_OAUTH_STATE_MAX_AGE_SECONDS must be between 60 and 900."
        )
    if not enabled:
        return
    if not GOOGLE_USER_OAUTH_CLIENT_ID_PATTERN.fullmatch(client_id):
        raise ImproperlyConfigured("GOOGLE_CLIENT_ID is invalid or missing.")
    if (
        len(client_secret) < 16
        or client_secret != client_secret.strip()
        or client_secret.startswith(INSECURE_SECRET_PREFIXES)
    ):
        raise ImproperlyConfigured(
            "GOOGLE_CLIENT_SECRET must be a non-default OAuth client secret."
        )
    if not GOOGLE_WORKSPACE_DOMAIN_PATTERN.fullmatch(allowed_domain):
        raise ImproperlyConfigured("GOOGLE_USER_OAUTH_ALLOWED_DOMAIN is invalid or missing.")

    parsed_redirect = urlsplit(redirect_uri)
    local_http_allowed = (
        development_context
        and parsed_redirect.scheme == "http"
        and parsed_redirect.hostname in {"localhost", "127.0.0.1"}
    )
    if (
        (parsed_redirect.scheme != "https" and not local_http_allowed)
        or not parsed_redirect.netloc
        or parsed_redirect.username
        or parsed_redirect.password
        or parsed_redirect.query
        or parsed_redirect.fragment
        or parsed_redirect.path != GOOGLE_SESSION_OAUTH_CALLBACK_PATH
    ):
        raise ImproperlyConfigured(
            "GOOGLE_SESSION_OAUTH_REDIRECT_URI must be the exact HTTPS session callback."
        )


def _validated_secret_file(
    path_value: str,
    *,
    setting_name: str,
    maximum_bytes: int,
) -> Path:
    if not path_value or path_value != path_value.strip():
        raise ImproperlyConfigured(f"{setting_name} must name a readable secret file.")
    path = Path(path_value)
    if not path.is_absolute():
        raise ImproperlyConfigured(f"{setting_name} must be an absolute path.")
    try:
        resolved = path.resolve(strict=True)
        size = resolved.stat().st_size
    except OSError as exc:
        raise ImproperlyConfigured(f"{setting_name} must name a readable secret file.") from exc
    if not resolved.is_file() or not 1 <= size <= maximum_bytes:
        raise ImproperlyConfigured(
            f"{setting_name} must name a non-empty secret file no larger than "
            f"{maximum_bytes} bytes."
        )
    return resolved


def load_google_user_token_keyring(path_value: str) -> tuple[str, dict[str, str]]:
    """Load a strict versioned Fernet keyring without exposing key material."""
    path = _validated_secret_file(
        path_value,
        setting_name="GOOGLE_USER_TOKEN_ENCRYPTION_KEY_FILE",
        maximum_bytes=GOOGLE_USER_TOKEN_KEYRING_MAX_BYTES,
    )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ImproperlyConfigured(
            "GOOGLE_USER_TOKEN_ENCRYPTION_KEY_FILE must contain a valid keyring."
        ) from exc

    if not isinstance(payload, dict) or set(payload) != {"active_version", "keys"}:
        raise ImproperlyConfigured(
            "GOOGLE_USER_TOKEN_ENCRYPTION_KEY_FILE must contain only active_version and keys."
        )
    active_version = payload.get("active_version")
    keys = payload.get("keys")
    if not isinstance(active_version, str) or not GOOGLE_USER_TOKEN_KEY_VERSION_PATTERN.fullmatch(
        active_version
    ):
        raise ImproperlyConfigured("The token keyring active_version is invalid.")
    if not isinstance(keys, dict) or not 1 <= len(keys) <= 8 or active_version not in keys:
        raise ImproperlyConfigured(
            "The token keyring must contain the active key and at most eight versions."
        )

    validated_keys: dict[str, str] = {}
    for version, encoded_key in keys.items():
        if not isinstance(version, str) or not GOOGLE_USER_TOKEN_KEY_VERSION_PATTERN.fullmatch(
            version
        ):
            raise ImproperlyConfigured("The token keyring contains an invalid key version.")
        if not isinstance(encoded_key, str) or encoded_key != encoded_key.strip():
            raise ImproperlyConfigured("The token keyring contains an invalid encryption key.")
        try:
            decoded_key = base64.b64decode(
                encoded_key.encode("ascii"), altchars=b"-_", validate=True
            )
        except (UnicodeEncodeError, ValueError) as exc:
            raise ImproperlyConfigured(
                "The token keyring contains an invalid encryption key."
            ) from exc
        if len(decoded_key) != 32:
            raise ImproperlyConfigured("The token keyring contains an invalid encryption key.")
        validated_keys[version] = encoded_key
    return active_version, validated_keys


def validate_google_user_oauth_settings(
    *,
    permission_authority: str,
    client_id: str,
    client_secret_file: str,
    redirect_uri: str,
    allowed_domain: str,
    token_encryption_key_file: str,
    sync_interval_seconds: int,
    visibility_max_age_seconds: int,
    maximum_users: int,
    maximum_documents: int,
    batch_size: int,
    state_max_age_seconds: int,
    development_context: bool,
    independent_secret_values: tuple[str, ...] = (),
    other_secret_files: tuple[str, ...] = (),
) -> None:
    """Validate the ADR-015 trust boundary before Django starts."""
    if permission_authority not in GOOGLE_PERMISSION_AUTHORITIES:
        raise ImproperlyConfigured(
            "GOOGLE_PERMISSION_AUTHORITY must be 'delegated_acl' or 'per_user_oauth'."
        )
    if not 60 <= sync_interval_seconds <= 86_400:
        raise ImproperlyConfigured(
            "GOOGLE_USER_VISIBILITY_SYNC_INTERVAL_SECONDS must be between 60 and 86400."
        )
    if not sync_interval_seconds < visibility_max_age_seconds <= 86_400:
        raise ImproperlyConfigured(
            "GOOGLE_USER_VISIBILITY_MAX_AGE_SECONDS must exceed the sync interval and "
            "be at most 86400."
        )
    if not 1 <= maximum_users <= 100:
        raise ImproperlyConfigured("GOOGLE_USER_VISIBILITY_MAX_USERS must be between 1 and 100.")
    if not 1 <= maximum_documents <= 10_000:
        raise ImproperlyConfigured(
            "GOOGLE_USER_VISIBILITY_MAX_DOCUMENTS must be between 1 and 10000."
        )
    if maximum_users * maximum_documents > 100_000:
        raise ImproperlyConfigured(
            "The configured per-user visibility size exceeds the 100000-check pilot cap."
        )
    if not 1 <= batch_size <= min(100, maximum_documents):
        raise ImproperlyConfigured(
            "GOOGLE_USER_VISIBILITY_BATCH_SIZE must be between 1 and the smaller of 100 "
            "or GOOGLE_USER_VISIBILITY_MAX_DOCUMENTS."
        )
    if not 60 <= state_max_age_seconds <= 900:
        raise ImproperlyConfigured(
            "GOOGLE_USER_OAUTH_STATE_MAX_AGE_SECONDS must be between 60 and 900."
        )

    if permission_authority != "per_user_oauth":
        return

    if not GOOGLE_USER_OAUTH_CLIENT_ID_PATTERN.fullmatch(client_id):
        raise ImproperlyConfigured("GOOGLE_USER_OAUTH_CLIENT_ID is invalid or missing.")
    if not GOOGLE_WORKSPACE_DOMAIN_PATTERN.fullmatch(allowed_domain):
        raise ImproperlyConfigured("GOOGLE_USER_OAUTH_ALLOWED_DOMAIN is invalid or missing.")

    parsed_redirect = urlsplit(redirect_uri)
    local_http_allowed = (
        development_context
        and parsed_redirect.scheme == "http"
        and (parsed_redirect.hostname in {"localhost", "127.0.0.1"})
    )
    if (
        (parsed_redirect.scheme != "https" and not local_http_allowed)
        or not parsed_redirect.netloc
        or parsed_redirect.username
        or parsed_redirect.password
        or parsed_redirect.query
        or parsed_redirect.fragment
        or parsed_redirect.path != GOOGLE_USER_OAUTH_CALLBACK_PATH
    ):
        raise ImproperlyConfigured(
            "GOOGLE_USER_OAUTH_REDIRECT_URI must be the exact HTTPS Drive OAuth callback."
        )

    client_secret_path = _validated_secret_file(
        client_secret_file,
        setting_name="GOOGLE_USER_OAUTH_CLIENT_SECRET_FILE",
        maximum_bytes=1_048_576,
    )
    encryption_key_path = _validated_secret_file(
        token_encryption_key_file,
        setting_name="GOOGLE_USER_TOKEN_ENCRYPTION_KEY_FILE",
        maximum_bytes=GOOGLE_USER_TOKEN_KEYRING_MAX_BYTES,
    )
    if client_secret_path == encryption_key_path:
        raise ImproperlyConfigured(
            "The user OAuth client secret and token encryption key must use distinct files."
        )
    for other_path_value in other_secret_files:
        if not other_path_value:
            continue
        if Path(other_path_value).expanduser().resolve(strict=False) == encryption_key_path:
            raise ImproperlyConfigured(
                "GOOGLE_USER_TOKEN_ENCRYPTION_KEY_FILE must be dedicated to token encryption."
            )

    _, keyring = load_google_user_token_keyring(token_encryption_key_file)
    application_secrets = {value for value in independent_secret_values if value}
    if application_secrets.intersection(keyring.values()):
        raise ImproperlyConfigured(
            "Google user token encryption keys must not reuse application secrets."
        )


def validate_freshness_monitor_settings(
    *,
    interval_seconds: int,
    warn_remaining_fraction: float,
    heartbeat_max_age_seconds: int,
    evidence_max_age_seconds: int,
    monitor_bearer_key: str,
    development_context: bool,
) -> None:
    """Reject freshness-monitor thresholds that could silence alerting."""
    if not 1 <= interval_seconds <= 3600:
        raise ImproperlyConfigured("FRESHNESS_MONITOR_INTERVAL_SECONDS must be between 1 and 3600.")
    if not 0.0 < warn_remaining_fraction < 1.0:
        raise ImproperlyConfigured(
            "FRESHNESS_WARN_REMAINING_FRACTION must be strictly between 0 and 1."
        )
    if warn_remaining_fraction * evidence_max_age_seconds <= interval_seconds:
        raise ImproperlyConfigured(
            "FRESHNESS_WARN_REMAINING_FRACTION must provide more than one monitor "
            "interval of warning before evidence expires."
        )
    if heartbeat_max_age_seconds <= interval_seconds:
        raise ImproperlyConfigured(
            "FRESHNESS_HEARTBEAT_MAX_AGE_SECONDS must be greater than "
            "FRESHNESS_MONITOR_INTERVAL_SECONDS so one late tick is not an outage."
        )
    if heartbeat_max_age_seconds + interval_seconds >= evidence_max_age_seconds:
        raise ImproperlyConfigured(
            "FRESHNESS_HEARTBEAT_MAX_AGE_SECONDS must leave at least one monitor "
            "interval before permission evidence expires."
        )
    if not development_context and (
        len(monitor_bearer_key) < 32
        or monitor_bearer_key != monitor_bearer_key.strip()
        or monitor_bearer_key.startswith(INSECURE_SECRET_PREFIXES)
        or not BEARER_TOKEN_PATTERN.fullmatch(monitor_bearer_key)
    ):
        raise ImproperlyConfigured(
            "FRESHNESS_MONITOR_BEARER_KEY must be a non-default bearer token with at "
            "least 32 characters outside development."
        )
