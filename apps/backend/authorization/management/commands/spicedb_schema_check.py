from django.core.management.base import BaseCommand, CommandError

from authorization.client import AuthzedSpiceDB, canonical_schema, schema_text


class Command(BaseCommand):
    help = "Fail unless SpiceDB contains the checked-in schema."

    def handle(self, *args, **options):
        try:
            actual = AuthzedSpiceDB().read_schema().strip()
        except Exception as exc:
            raise CommandError("SpiceDB schema check failed.") from exc
        if canonical_schema(actual) != canonical_schema(schema_text()):
            raise CommandError("SpiceDB schema differs from the checked-in schema.")
        self.stdout.write(self.style.SUCCESS("SpiceDB schema is current."))
