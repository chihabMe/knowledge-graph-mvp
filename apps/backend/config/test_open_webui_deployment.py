from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILE = REPOSITORY_ROOT / "infra" / "compose.infrastructure.yml"
APP_COMPOSE_FILE = REPOSITORY_ROOT / "infra" / "compose.app.yml"
ENV_EXAMPLE = REPOSITORY_ROOT / ".env.example"


def test_open_webui_connection_is_server_side_and_single_model():
    compose = COMPOSE_FILE.read_text()
    app_compose = APP_COMPOSE_FILE.read_text()

    assert "OPENAI_API_BASE_URL: http://django:8000/v1" in compose
    assert "OPENAI_API_KEY: ${OPEN_WEBUI_BACKEND_API_KEY" in compose
    assert '"model_ids":["${OPEN_WEBUI_MODEL_ID' in compose
    assert 'ENABLE_OLLAMA_API: "false"' in compose
    assert 'ENABLE_DIRECT_CONNECTIONS: "false"' in compose
    assert 'ENABLE_OPENAI_API_PASSTHROUGH: "false"' in compose
    assert 'SAFE_MODE: "true"' in compose
    assert 'BYPASS_EMBEDDING_AND_RETRIEVAL: "true"' in compose
    assert 'USER_PERMISSIONS_CHAT_FILE_UPLOAD: "false"' in compose
    assert 'USER_PERMISSIONS_CHAT_WEB_UPLOAD: "false"' in compose
    assert 'USER_PERMISSIONS_FEATURES_DIRECT_TOOL_SERVERS: "false"' in compose
    assert 'USER_PERMISSIONS_FEATURES_WEB_SEARCH: "false"' in compose
    assert "DJANGO_ALLOWED_HOSTS: ${DJANGO_ALLOWED_HOSTS" in app_compose
    assert ",django" in app_compose


def test_open_webui_signed_identity_forwarding_is_fail_closed():
    compose = COMPOSE_FILE.read_text()
    app_compose = APP_COMPOSE_FILE.read_text()

    assert 'ENABLE_FORWARD_USER_INFO_HEADERS: "true"' in compose
    assert "FORWARD_USER_INFO_HEADER_JWT_SECRET: ${OPEN_WEBUI_IDENTITY_JWT_SECRET" in compose
    assert "FORWARD_USER_INFO_HEADER_JWT: ${OPEN_WEBUI_IDENTITY_JWT_HEADER" in compose
    assert "FORWARD_USER_INFO_HEADER_JWT_EXPIRES_SECONDS:" in compose
    assert "FORWARD_USER_INFO_HEADER_USER_EMAIL:" not in compose
    assert "OPEN_WEBUI_COMPATIBLE_API_ENABLED: ${OPEN_WEBUI_COMPATIBLE_API_ENABLED" in app_compose
    assert "OPEN_WEBUI_BACKEND_API_KEY: ${OPEN_WEBUI_BACKEND_API_KEY" in app_compose
    assert "OPEN_WEBUI_IDENTITY_JWT_SECRET: ${OPEN_WEBUI_IDENTITY_JWT_SECRET" in app_compose
    assert "OPEN_WEBUI_IDENTITY_JWT_HEADER: ${OPEN_WEBUI_IDENTITY_JWT_HEADER" in app_compose
    assert "OPEN_WEBUI_IDENTITY_JWT_ISSUER: ${OPEN_WEBUI_IDENTITY_JWT_ISSUER" in app_compose
    assert "OPEN_WEBUI_MODEL_ID: ${OPEN_WEBUI_MODEL_ID" in app_compose
    assert "WEBUI_SECRET_KEY: ${WEBUI_SECRET_KEY" in app_compose


def test_open_webui_login_defaults_to_google_only():
    compose = COMPOSE_FILE.read_text()

    assert 'ENABLE_PERSISTENT_CONFIG: "false"' in compose
    assert 'ENABLE_OAUTH_PERSISTENT_CONFIG: "false"' in compose
    assert 'ENABLE_OAUTH_SIGNUP: "true"' in compose
    assert 'OAUTH_MERGE_ACCOUNTS_BY_EMAIL: "false"' in compose
    assert 'ENABLE_OAUTH_ID_TOKEN_COOKIE: "false"' in compose
    assert 'ENABLE_PROFILE_IMAGE_URL_FORWARDING: "false"' in compose
    assert 'ENABLE_SIGNUP: "false"' in compose
    assert "ENABLE_LOGIN_FORM: ${OPEN_WEBUI_ENABLE_LOGIN_FORM:-false}" in compose
    assert "ENABLE_PASSWORD_AUTH: ${OPEN_WEBUI_ENABLE_PASSWORD_AUTH:-false}" in compose
    password_change_setting = (
        "ENABLE_PASSWORD_CHANGE_FORM: ${OPEN_WEBUI_ENABLE_PASSWORD_CHANGE_FORM:-false}"
    )
    assert password_change_setting in compose


def test_example_lists_every_operator_supplied_open_webui_value():
    env_example = ENV_EXAMPLE.read_text()

    for name in (
        "WEBUI_URL",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "GOOGLE_REDIRECT_URI",
        "WEBUI_SECRET_KEY",
        "OPEN_WEBUI_COMPATIBLE_API_ENABLED",
        "OPEN_WEBUI_BACKEND_API_KEY",
        "OPEN_WEBUI_IDENTITY_JWT_SECRET",
        "OPEN_WEBUI_IDENTITY_JWT_HEADER",
        "OPEN_WEBUI_IDENTITY_JWT_MAX_LIFETIME_SECONDS",
        "OPEN_WEBUI_MODEL_ID",
    ):
        assert f"{name}=" in env_example
