from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from integrations.drive.google_client import DriveCredentialUnavailableError, GoogleDriveApiError
from integrations.models import DriveConnection, DriveSyncRun, SourceDocument
from integrations.tasks import run_drive_sync
from integrations.views import GoogleDriveMetadataClient, _active_or_bootstrap_connection


class Command(BaseCommand):
    help = (
        "Operator shortcut for the admin connection-selection flow: select a Drive "
        "root by ID and trigger a sync, without going through the HTTP API/session "
        "auth. Equivalent to POST /api/ingest/drive/connection/root/ followed by "
        "POST /api/ingest/drive/sync/."
    )

    def add_arguments(self, parser):
        parser.add_argument("root_id", help="Drive folder ID or Shared Drive ID to ingest from.")
        parser.add_argument(
            "--scope-type",
            choices=[DriveConnection.ScopeType.FOLDER, DriveConnection.ScopeType.SHARED_DRIVE],
            default=DriveConnection.ScopeType.FOLDER,
        )
        parser.add_argument(
            "--no-sync",
            action="store_true",
            help="Only select the root; do not queue a sync run.",
        )

    def handle(self, *args, **options):
        root_id = options["root_id"]
        scope_type = options["scope_type"]

        connection = _active_or_bootstrap_connection()
        try:
            candidates = GoogleDriveMetadataClient().list_root_candidates(connection)
        except DriveCredentialUnavailableError as exc:
            raise CommandError(f"drive_credential_unavailable: {exc}") from exc
        except GoogleDriveApiError as exc:
            raise CommandError(f"drive_api_error: {exc}") from exc

        selected = next(
            (
                candidate
                for candidate in candidates
                if candidate.scope_type == scope_type and candidate.root_id == root_id
            ),
            None,
        )
        if selected is None:
            visible = ", ".join(f"{c.scope_type}:{c.root_id} ({c.name})" for c in candidates)
            raise CommandError(
                f"root_not_visible: {scope_type}:{root_id} is not visible to the ingestion "
                f"credential. Visible roots: {visible or '(none)'}"
            )

        with transaction.atomic():
            connection = DriveConnection.objects.select_for_update().get(pk=connection.pk)
            previous_scope = (
                connection.scope_type,
                connection.root_folder_id,
                connection.shared_drive_id,
            )
            connection.scope_type = selected.scope_type
            if selected.scope_type == DriveConnection.ScopeType.SHARED_DRIVE:
                connection.root_folder_id = ""
                connection.shared_drive_id = selected.root_id
            else:
                connection.root_folder_id = selected.root_id
                connection.shared_drive_id = ""
            selected_scope = (
                connection.scope_type,
                connection.root_folder_id,
                connection.shared_drive_id,
            )
            connection.save(
                update_fields=["scope_type", "root_folder_id", "shared_drive_id", "updated_at"]
            )
            rescoped = 0
            if selected_scope != previous_scope:
                rescoped = SourceDocument.objects.filter(connection=connection).update(
                    retrieval_eligible=False, updated_at=timezone.now()
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"root selected (connection={connection.pk}, name={selected.name!r}, "
                f"rescoped_documents={rescoped})"
            )
        )

        if options["no_sync"]:
            return

        connection.refresh_from_db()
        if not connection.enabled:
            connection.enabled = True
            connection.save(update_fields=["enabled"])

        triggered_by = get_user_model().objects.filter(is_staff=True).order_by("pk").first()
        run = DriveSyncRun.create_for_connection(connection, triggered_by=triggered_by)
        run_drive_sync.delay(run.pk)
        self.stdout.write(self.style.SUCCESS(f"sync queued (run_id={run.pk}, status={run.status})"))
