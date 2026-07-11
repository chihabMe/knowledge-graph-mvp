from django.core.management.base import BaseCommand, CommandError

from authorization.client import AuthzedSpiceDB, canonical_schema, schema_text


class Command(BaseCommand):
    help = "Idempotently apply the checked-in SpiceDB schema."

    def handle(self, *args, **options):
        expected = schema_text().strip()
        client = AuthzedSpiceDB()
        try:
            try:
                actual = client.read_schema().strip()
            except Exception:
                actual = ""
            if canonical_schema(actual) != canonical_schema(expected):
                client.apply_schema(expected)
        except Exception as exc:
            raise CommandError("SpiceDB schema application failed.") from exc
        self.stdout.write(self.style.SUCCESS("SpiceDB schema is current."))
