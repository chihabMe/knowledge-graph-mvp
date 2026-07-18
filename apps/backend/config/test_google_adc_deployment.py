from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
APP_COMPOSE_FILE = REPOSITORY_ROOT / "infra" / "compose.app.yml"
DEV_COMPOSE_FILE = REPOSITORY_ROOT / "infra" / "compose.dev.yml"
ENV_EXAMPLE = REPOSITORY_ROOT / ".env.example"


def test_compose_supports_local_adc_and_metadata_server_fallback():
    compose = APP_COMPOSE_FILE.read_text(encoding="utf-8")

    assert (
        "${GOOGLE_ADC_FILE:-/dev/null}:/run/secrets/google-application-default.json:ro" in compose
    )
    assert "GOOGLE_APPLICATION_CREDENTIALS: ${GOOGLE_ADC_CONTAINER_FILE:-}" in compose
    assert "GOOGLE_CLOUD_PROJECT: ${GOOGLE_CLOUD_PROJECT:-}" in compose
    assert "GOOGLE_DRIVE_AUTH_MODE: ${GOOGLE_DRIVE_AUTH_MODE:-application_default}" in compose


def test_example_prefers_keyless_adc_without_a_reusable_key_path():
    example = ENV_EXAMPLE.read_text(encoding="utf-8")

    assert "GOOGLE_DRIVE_AUTH_MODE=application_default" in example
    assert "GOOGLE_ADC_FILE=" in example
    assert "GOOGLE_ADC_CONTAINER_FILE=" in example
    assert "GOOGLE_CLOUD_PROJECT=" in example
    assert "Do not weaken organization policy" in example


def test_dev_django_processes_share_the_host_identity_for_private_secret_mounts():
    compose = DEV_COMPOSE_FILE.read_text(encoding="utf-8")

    assert compose.count('user: "${LOCAL_UID:-1000}:${LOCAL_GID:-1000}"') == 4
