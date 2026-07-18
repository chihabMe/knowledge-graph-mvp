from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from google.auth.exceptions import DefaultCredentialsError

from integrations.drive.credentials import (
    ApplicationDefaultCredentialError,
    load_application_default_credentials,
)


class ApplicationDefaultCredentialTests(SimpleTestCase):
    @patch("google.auth.default")
    def test_adc_requests_only_the_supplied_scopes(self, mock_default):
        credentials = Mock()
        mock_default.return_value = (credentials, "quota-project")

        loaded = load_application_default_credentials(("scope-b", "scope-a"))

        self.assertIs(loaded, credentials)
        mock_default.assert_called_once_with(scopes=["scope-b", "scope-a"])

    @patch(
        "google.auth.default",
        side_effect=DefaultCredentialsError("provider detail must not escape"),
    )
    def test_adc_discovery_failure_is_normalized(self, _mock_default):
        with self.assertRaises(ApplicationDefaultCredentialError) as context:
            load_application_default_credentials(("scope-a",))

        self.assertEqual(
            str(context.exception),
            "Google Application Default Credentials are unavailable.",
        )
        self.assertNotIn("provider detail", str(context.exception))
