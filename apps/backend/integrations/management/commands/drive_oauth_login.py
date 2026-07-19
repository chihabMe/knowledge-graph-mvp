"""Interactive, development-only Google Drive OAuth bootstrap."""

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from integrations.drive.google_client import DRIVE_READONLY_SCOPE


class Command(BaseCommand):
    help = "Authorize the development Google Drive OAuth account locally."

    def handle(self, *args, **options):
        if settings.GOOGLE_DRIVE_AUTH_MODE != "oauth_dev":
            raise CommandError("Set GOOGLE_DRIVE_AUTH_MODE=oauth_dev before running this command.")
        client_secret_path = Path(settings.GOOGLE_OAUTH_CLIENT_SECRET_FILE)
        token_path = Path(settings.GOOGLE_OAUTH_TOKEN_FILE)
        if not client_secret_path.is_file() or client_secret_path.stat().st_size == 0:
            raise CommandError("GOOGLE_OAUTH_CLIENT_SECRET_FILE is missing or empty.")
        token_path.parent.mkdir(parents=True, exist_ok=True)

        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow

        credentials = None
        if token_path.is_file() and token_path.stat().st_size:
            try:
                credentials = Credentials.from_authorized_user_file(
                    token_path, scopes=[DRIVE_READONLY_SCOPE]
                )
            except (OSError, ValueError):
                credentials = None

        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        elif not credentials or not credentials.valid:
            flow = InstalledAppFlow.from_client_secrets_file(
                client_secret_path, scopes=[DRIVE_READONLY_SCOPE]
            )
            self.stdout.write(
                "Open the authorization URL printed below in the development browser."
            )
            credentials = flow.run_local_server(
                host="localhost",
                bind_addr="0.0.0.0",
                port=8765,
                open_browser=False,
                authorization_prompt_message="{url}",
            )

        token_path.write_text(credentials.to_json(), encoding="utf-8")
        token_path.chmod(0o600)
        self.stdout.write(self.style.SUCCESS("Google Drive OAuth token saved securely."))
