from django.core.management.base import BaseCommand, CommandError

from integrations.models import DriveConnection
from integrations.permission_authority import (
    PermissionAuthoritySwitchError,
    switch_permission_authority,
)


class Command(BaseCommand):
    help = "Fail-closed switch of one Drive connection's permission authority."

    def add_arguments(self, parser):
        parser.add_argument("connection_id", type=int)
        parser.add_argument(
            "authority",
            choices=DriveConnection.PermissionAuthority.values,
        )

    def handle(self, *args, **options):
        try:
            result = switch_permission_authority(
                connection_id=options["connection_id"],
                target_authority=options["authority"],
            )
        except PermissionAuthoritySwitchError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            self.style.SUCCESS(
                "permission authority ready "
                f"(connection={result.connection_id}, authority={result.permission_authority}, "
                f"documents_invalidated={result.documents_invalidated}, "
                f"authorizations_invalidated={result.authorizations_invalidated}, "
                f"relationships_deleted={result.relationships_deleted}, "
                f"changed={str(result.changed).lower()})"
            )
        )
