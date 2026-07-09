from django.core.management.base import BaseCommand

from graph.db import session
from graph.schema import CONSTRAINTS


class Command(BaseCommand):
    help = "Apply Neo4j constraints and indexes (idempotent)."

    def handle(self, *args, **options):
        with session() as db_session:
            for statement in CONSTRAINTS:
                db_session.run(statement)
                self.stdout.write(f"Applied: {statement}")

        self.stdout.write(self.style.SUCCESS(f"{len(CONSTRAINTS)} constraint(s) applied."))
