from django.core.management.base import BaseCommand

from graph.db import session
from graph.schema import graph_setup_statements


class Command(BaseCommand):
    help = "Apply Neo4j constraints and indexes (idempotent)."

    def handle(self, *args, **options):
        statements = graph_setup_statements()
        with session() as db_session:
            for statement in statements:
                db_session.run(statement)
                self.stdout.write(f"Applied: {statement}")

        self.stdout.write(self.style.SUCCESS(f"{len(statements)} statement(s) applied."))
