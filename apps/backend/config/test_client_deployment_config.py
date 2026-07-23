import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from django.test import SimpleTestCase

SCRIPT_PATH = Path(__file__).resolve().parents[3] / "scripts/deploy/generate_client.py"
COOLIFY_COMPOSE_PATH = Path(__file__).resolve().parents[3] / "infra/compose.coolify.yml"
SPEC = importlib.util.spec_from_file_location("generate_client", SCRIPT_PATH)
if SPEC is None or SPEC.loader is None:  # pragma: no cover - import setup guard
    raise RuntimeError("could not load generate_client.py")
generate_client = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generate_client)


class ClientDeploymentConfigTests(SimpleTestCase):
    service_account_email = (
        "knowledge-graph-ingestion@knowledge-graph-pilot.iam.gserviceaccount.com"
    )

    def setUp(self):
        self.temporary_directory = TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        secrets_dir = Path(self.temporary_directory.name)
        self.keyring_path = secrets_dir / "google-user-token-keyring.json"
        self.keyring_path.write_text(
            json.dumps({"active_version": "v1", "keys": {"v1": generate_client.fernet_key()}}),
            encoding="utf-8",
        )

    def build_ready_values(self):
        values, _ = generate_client.build_values(
            "acme",
            "chihab.online",
            self.keyring_path,
            workspace_domain="acme.example",
            service_account_email=self.service_account_email,
        )
        drive_client_id = "123456-drive.apps.googleusercontent.com"
        values.update(
            {
                "GOOGLE_CLIENT_ID": "123456-login.apps.googleusercontent.com",
                "GOOGLE_CLIENT_SECRET": "shared-login-client-secret",
                "GOOGLE_USER_OAUTH_CLIENT_ID": drive_client_id,
                "KG_GOOGLE_USER_OAUTH_CLIENT_JSON": json.dumps(
                    {
                        "web": {
                            "client_id": drive_client_id,
                            "client_secret": "shared-drive-client-secret",
                        }
                    }
                ),
                "OPENROUTER_API_KEY": "openrouter-test-key",
            }
        )
        return values

    def test_generation_sets_exact_callbacks_and_keyless_client_identity(self):
        values = self.build_ready_values()

        self.assertEqual(values["WEBUI_URL"], "https://acme.chihab.online")
        self.assertEqual(
            values["OPENAI_API_BASE_URL"],
            "https://api.acme.chihab.online/v1",
        )
        self.assertEqual(
            values["DJANGO_ALLOWED_HOSTS"],
            "api.acme.chihab.online,django,kg-django",
        )
        self.assertEqual(
            values["GOOGLE_REDIRECT_URI"],
            "https://acme.chihab.online/oauth/google/callback",
        )
        self.assertEqual(
            values["GOOGLE_USER_OAUTH_REDIRECT_URI"],
            "https://api.acme.chihab.online/api/drive/oauth/callback",
        )
        self.assertEqual(values["GOOGLE_DRIVE_AUTH_MODE"], "application_default")
        self.assertEqual(values["GOOGLE_SERVICE_ACCOUNT_FILE"], "")
        self.assertEqual(values["GOOGLE_DRIVE_ROOT_ID"], "")
        self.assertEqual(values["GOOGLE_CLOUD_PROJECT"], "knowledge-graph-pilot")
        self.assertEqual(
            generate_client.validate_client_values(values, target="coolify"),
            [],
        )

    def test_coolify_routes_cache_and_webui_api_to_unambiguous_endpoints(self):
        compose = COOLIFY_COMPOSE_PATH.read_text(encoding="utf-8")

        self.assertIn("DJANGO_CACHE_URL: redis://kg-redis:6379/2", compose)
        self.assertIn("OPENAI_API_BASE_URL: ${OPENAI_API_BASE_URL}", compose)

    def test_preflight_rejects_wrong_identity_callback_and_oauth_json(self):
        values = self.build_ready_values()
        values["GOOGLE_INGESTION_SERVICE_ACCOUNT_EMAIL"] = (
            "144917704622-compute@developer.gserviceaccount.com"
        )
        values["GOOGLE_SESSION_OAUTH_REDIRECT_URI"] = "http://localhost/callback"
        values["KG_GOOGLE_USER_OAUTH_CLIENT_JSON"] = "{}"

        problems = generate_client.validate_client_values(values, target="coolify")

        self.assertTrue(any("dedicated" in problem for problem in problems))
        self.assertTrue(any("GOOGLE_SESSION_OAUTH_REDIRECT_URI" in problem for problem in problems))
        self.assertTrue(any("KG_GOOGLE_USER_OAUTH_CLIENT_JSON" in problem for problem in problems))

    def test_local_preflight_requires_both_secret_files(self):
        values = self.build_ready_values()

        problems = generate_client.validate_client_values(values, target="local")

        self.assertTrue(
            any("GOOGLE_USER_OAUTH_CLIENT_SECRET_FILE" in problem for problem in problems)
        )
        self.assertFalse(
            any("GOOGLE_USER_TOKEN_ENCRYPTION_KEY_FILE" in problem for problem in problems)
        )
