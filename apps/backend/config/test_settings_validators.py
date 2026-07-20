import json
from pathlib import Path
from tempfile import TemporaryDirectory

from cryptography.fernet import Fernet
from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from config.settings_validators import (
    load_google_user_token_keyring,
    validate_drive_content_sync_settings,
    validate_drive_onboarding_urls,
    validate_freshness_monitor_settings,
    validate_google_session_oauth_settings,
    validate_google_user_oauth_settings,
    validate_open_webui_compatible_settings,
)


class DriveOnboardingUrlValidationTests(SimpleTestCase):
    def test_accepts_https_origin_and_local_development_http(self):
        self.assertIsNone(
            validate_drive_onboarding_urls(
                enabled=True,
                session_oauth_enabled=True,
                webui_url="https://ai.example.com",
                development_context=False,
            )
        )
        self.assertIsNone(
            validate_drive_onboarding_urls(
                enabled=True,
                session_oauth_enabled=True,
                webui_url="http://localhost:3000/",
                development_context=True,
            )
        )

    def test_rejects_untrusted_return_targets(self):
        for value in (
            "",
            "http://ai.example.com",
            "https://user@ai.example.com",
            "https://ai.example.com/chat",
            "https://ai.example.com?next=https://attacker.example",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ImproperlyConfigured):
                    validate_drive_onboarding_urls(
                        enabled=True,
                        session_oauth_enabled=True,
                        webui_url=value,
                        development_context=False,
                    )

    def test_disabled_onboarding_does_not_require_url(self):
        self.assertIsNone(
            validate_drive_onboarding_urls(
                enabled=False,
                session_oauth_enabled=False,
                webui_url="",
                development_context=False,
            )
        )

    def test_enabled_onboarding_requires_session_bootstrap(self):
        with self.assertRaises(ImproperlyConfigured):
            validate_drive_onboarding_urls(
                enabled=True,
                session_oauth_enabled=False,
                webui_url="https://ai.example.com",
                development_context=False,
            )


class GoogleSessionOAuthSettingsValidationTests(SimpleTestCase):
    valid_values = {
        "enabled": True,
        "client_id": "123456-login.apps.googleusercontent.com",
        "client_secret": "google-client-secret-value",
        "redirect_uri": "https://api.example.com/api/session/google/callback",
        "allowed_domain": "example.com",
        "state_max_age_seconds": 600,
        "development_context": False,
    }

    def validate(self, **overrides):
        return validate_google_session_oauth_settings(**{**self.valid_values, **overrides})

    def test_valid_and_disabled_settings(self):
        self.assertIsNone(self.validate())
        self.assertIsNone(
            self.validate(
                enabled=False,
                client_id="",
                client_secret="",
                redirect_uri="",
                allowed_domain="",
            )
        )

    def test_enabled_settings_fail_closed(self):
        invalid_values = (
            {"client_id": "not-google"},
            {"client_secret": "short"},
            {"allowed_domain": "Example.COM"},
            {"redirect_uri": "http://api.example.com/api/session/google/callback"},
            {"redirect_uri": "https://api.example.com/api/session/google/callback?x=1"},
            {"redirect_uri": "https://api.example.com/other"},
            {"state_max_age_seconds": 59},
            {"state_max_age_seconds": 901},
        )
        for values in invalid_values:
            with self.subTest(values=values):
                with self.assertRaises(ImproperlyConfigured):
                    self.validate(**values)

    def test_local_http_callback_is_development_only(self):
        self.assertIsNone(
            self.validate(
                redirect_uri="http://localhost:8000/api/session/google/callback",
                development_context=True,
            )
        )


class OpenWebUISettingsValidationTests(SimpleTestCase):
    valid_values = {
        "enabled": True,
        "backend_api_key": "service-" + ("a" * 40),
        "identity_jwt_secret": "identity-" + ("b" * 40),
        "webui_secret_key": "webui-" + ("c" * 40),
        "identity_jwt_header": "X-OpenWebUI-User-Jwt",
        "identity_jwt_issuer": "open-webui",
        "identity_jwt_max_lifetime_seconds": 300,
        "identity_jwt_clock_skew_seconds": 10,
        "model_id": "client-knowledge-graph",
    }

    def validate(self, **overrides):
        values = {**self.valid_values, **overrides}
        return validate_open_webui_compatible_settings(**values)

    def test_valid_enabled_settings_are_accepted(self):
        self.assertIsNone(self.validate())

    def test_disabled_api_does_not_require_secrets(self):
        self.assertIsNone(
            self.validate(
                enabled=False,
                backend_api_key="",
                identity_jwt_secret="",
                webui_secret_key="",
            )
        )

    def test_enabled_api_requires_strong_non_default_secrets(self):
        for setting_name in ("backend_api_key", "identity_jwt_secret"):
            for value in ("", "too-short", "change-this-placeholder-secret-value"):
                with self.subTest(setting_name=setting_name, value=value):
                    with self.assertRaises(ImproperlyConfigured):
                        self.validate(**{setting_name: value})

    def test_service_key_must_be_a_single_bearer_token(self):
        for value in (
            "service-" + ("a" * 40) + " ",
            "service key " + ("a" * 40),
            "service-" + ("a" * 40) + "\n",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ImproperlyConfigured):
                    self.validate(backend_api_key=value)

    def test_secrets_must_be_independent(self):
        service_key = self.valid_values["backend_api_key"]
        with self.assertRaises(ImproperlyConfigured):
            self.validate(identity_jwt_secret=service_key)
        with self.assertRaises(ImproperlyConfigured):
            self.validate(webui_secret_key=service_key)
        with self.assertRaises(ImproperlyConfigured):
            self.validate(webui_secret_key=self.valid_values["identity_jwt_secret"])

    def test_identity_header_issuer_and_model_id_are_validated(self):
        invalid_values = (
            {"identity_jwt_header": "X Invalid"},
            {"identity_jwt_issuer": ""},
            {"identity_jwt_issuer": " open-webui"},
            {"model_id": "invalid model"},
            {"model_id": "-invalid-prefix"},
        )
        for values in invalid_values:
            with self.subTest(values=values):
                with self.assertRaises(ImproperlyConfigured):
                    self.validate(**values)

    def test_identity_lifetime_and_clock_skew_are_bounded(self):
        invalid_values = (
            {"identity_jwt_max_lifetime_seconds": 0},
            {"identity_jwt_max_lifetime_seconds": 301},
            {"identity_jwt_clock_skew_seconds": -1},
            {"identity_jwt_clock_skew_seconds": 31},
        )
        for values in invalid_values:
            with self.subTest(values=values):
                with self.assertRaises(ImproperlyConfigured):
                    self.validate(**values)


class GoogleUserOAuthSettingsValidationTests(SimpleTestCase):
    def setUp(self):
        self.temporary_directory = TemporaryDirectory()
        directory = Path(self.temporary_directory.name)
        self.client_secret_file = directory / "client.json"
        self.client_secret_file.write_text('{"web": {}}', encoding="utf-8")
        self.encryption_key = Fernet.generate_key().decode("ascii")
        self.keyring_file = directory / "token-keyring.json"
        self.keyring_file.write_text(
            json.dumps(
                {
                    "active_version": "v1",
                    "keys": {"v1": self.encryption_key},
                }
            ),
            encoding="utf-8",
        )
        self.valid_values = {
            "permission_authority": "per_user_oauth",
            "client_id": "123456-example.apps.googleusercontent.com",
            "client_secret_file": str(self.client_secret_file),
            "redirect_uri": "https://api.example.com/api/drive/oauth/callback",
            "allowed_domain": "example.com",
            "token_encryption_key_file": str(self.keyring_file),
            "sync_interval_seconds": 900,
            "visibility_max_age_seconds": 1800,
            "maximum_users": 10,
            "maximum_documents": 1000,
            "batch_size": 100,
            "state_max_age_seconds": 600,
            "development_context": False,
            "independent_secret_values": ("another-application-secret",),
            "other_secret_files": (),
        }

    def tearDown(self):
        self.temporary_directory.cleanup()

    def validate(self, **overrides):
        values = {**self.valid_values, **overrides}
        return validate_google_user_oauth_settings(**values)

    def test_valid_per_user_settings_are_accepted(self):
        self.assertIsNone(self.validate())

    def test_delegated_mode_does_not_require_oauth_secrets(self):
        self.assertIsNone(
            self.validate(
                permission_authority="delegated_acl",
                client_id="",
                client_secret_file="",
                redirect_uri="",
                allowed_domain="",
                token_encryption_key_file="",
            )
        )

    def test_authority_and_limits_fail_closed(self):
        invalid_values = (
            {"permission_authority": "combined"},
            {"sync_interval_seconds": 0},
            {"visibility_max_age_seconds": 900},
            {"maximum_users": 101},
            {"maximum_documents": 10_001},
            {"maximum_users": 100, "maximum_documents": 1001},
            {"batch_size": 101},
            {"state_max_age_seconds": 59},
            {"state_max_age_seconds": 901},
        )
        for values in invalid_values:
            with self.subTest(values=values):
                with self.assertRaises(ImproperlyConfigured):
                    self.validate(**values)

    def test_per_user_mode_requires_exact_identity_and_callback_configuration(self):
        invalid_values = (
            {"client_id": "not-a-google-client"},
            {"allowed_domain": "Example.COM"},
            {"redirect_uri": "http://api.example.com/api/drive/oauth/callback"},
            {"redirect_uri": "https://api.example.com/api/drive/oauth/callback?next=x"},
            {"redirect_uri": "https://api.example.com/other"},
        )
        for values in invalid_values:
            with self.subTest(values=values):
                with self.assertRaises(ImproperlyConfigured):
                    self.validate(**values)

    def test_local_http_callback_is_development_only(self):
        self.assertIsNone(
            self.validate(
                redirect_uri="http://localhost/api/drive/oauth/callback",
                development_context=True,
            )
        )

    def test_secret_files_must_exist_and_be_distinct(self):
        with self.assertRaises(ImproperlyConfigured):
            self.validate(client_secret_file=str(self.client_secret_file.with_name("missing")))
        with self.assertRaises(ImproperlyConfigured):
            self.validate(token_encryption_key_file=str(self.client_secret_file))
        with self.assertRaises(ImproperlyConfigured):
            self.validate(other_secret_files=(str(self.keyring_file),))

    def test_encryption_key_must_not_reuse_an_application_secret(self):
        with self.assertRaises(ImproperlyConfigured):
            self.validate(independent_secret_values=(self.encryption_key,))

    def test_keyring_requires_valid_versioned_fernet_keys(self):
        active_version, keys = load_google_user_token_keyring(str(self.keyring_file))
        self.assertEqual(active_version, "v1")
        self.assertEqual(set(keys), {"v1"})

        self.keyring_file.write_text(
            '{"active_version":"v1","keys":{"v1":"invalid"}}',
            encoding="utf-8",
        )
        with self.assertRaises(ImproperlyConfigured):
            load_google_user_token_keyring(str(self.keyring_file))


class DriveContentSyncSettingsValidationTests(SimpleTestCase):
    def test_valid_schedule_passes(self):
        self.assertIsNone(
            validate_drive_content_sync_settings(
                interval_seconds=900,
                max_age_seconds=1800,
            )
        )

    def test_interval_must_stay_within_bounds(self):
        for interval in (0, 59, 86_401):
            with self.subTest(interval=interval):
                with self.assertRaises(ImproperlyConfigured):
                    validate_drive_content_sync_settings(
                        interval_seconds=interval,
                        max_age_seconds=1800,
                    )

    def test_max_age_must_exceed_interval_and_stay_bounded(self):
        for max_age in (899, 900, 172_801):
            with self.subTest(max_age=max_age):
                with self.assertRaises(ImproperlyConfigured):
                    validate_drive_content_sync_settings(
                        interval_seconds=900,
                        max_age_seconds=max_age,
                    )


class FreshnessMonitorSettingsValidationTests(SimpleTestCase):
    valid_values = {
        "interval_seconds": 60,
        "warn_remaining_fraction": 0.4,
        "heartbeat_max_age_seconds": 180,
        "evidence_max_age_seconds": 600,
        "monitor_bearer_key": "k" * 32,
        "development_context": False,
        "run_sample_limit": 20,
        "never_synced_grace_seconds": 120,
        "retention_days": 14,
    }

    def validate(self, **overrides):
        return validate_freshness_monitor_settings(**{**self.valid_values, **overrides})

    def test_valid_settings_pass(self):
        self.assertIsNone(self.validate())
        self.assertIsNone(self.validate(monitor_bearer_key="", development_context=True))

    def test_interval_must_stay_within_bounds(self):
        for interval in (0, -1, 3601):
            with self.subTest(interval=interval):
                with self.assertRaises(ImproperlyConfigured):
                    self.validate(interval_seconds=interval)

    def test_warn_fraction_must_be_strictly_between_zero_and_one(self):
        for fraction in (0.0, 1.0, -0.1, 1.5):
            with self.subTest(fraction=fraction):
                with self.assertRaises(ImproperlyConfigured):
                    self.validate(warn_remaining_fraction=fraction)

    def test_heartbeat_max_age_must_exceed_interval(self):
        with self.assertRaises(ImproperlyConfigured):
            self.validate(heartbeat_max_age_seconds=60)

    def test_warning_window_must_exceed_monitor_interval(self):
        with self.assertRaises(ImproperlyConfigured):
            self.validate(warn_remaining_fraction=0.1)

    def test_heartbeat_alert_must_precede_evidence_expiry(self):
        with self.assertRaises(ImproperlyConfigured):
            self.validate(heartbeat_max_age_seconds=540)

    def test_production_bearer_key_is_required_and_strict(self):
        for key in ("", "short", "change-this-monitor-key" + "x" * 16, "x" * 31 + " "):
            with self.subTest(key=key):
                with self.assertRaises(ImproperlyConfigured):
                    self.validate(monitor_bearer_key=key)

    def test_run_sample_limit_must_stay_within_bounds(self):
        for limit in (0, -1, 1001):
            with self.subTest(limit=limit):
                with self.assertRaises(ImproperlyConfigured):
                    self.validate(run_sample_limit=limit)

    def test_never_synced_grace_must_be_shorter_than_evidence_expiry(self):
        for grace in (-1, 600, 601):
            with self.subTest(grace=grace):
                with self.assertRaises(ImproperlyConfigured):
                    self.validate(never_synced_grace_seconds=grace)

    def test_retention_must_be_at_least_one_day_and_exceed_evidence_expiry(self):
        with self.assertRaises(ImproperlyConfigured):
            self.validate(retention_days=0)
        with self.assertRaises(ImproperlyConfigured):
            self.validate(retention_days=1, evidence_max_age_seconds=172800)
