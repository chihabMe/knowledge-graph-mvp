from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from retrieval.evaluation import EvaluationFixtureError, run_evaluation


class Command(BaseCommand):
    help = "Run the private operator evaluation fixture through the real query path."

    def add_arguments(self, parser):
        parser.add_argument("--dataset-dir", required=True, type=Path)

    def handle(self, *args, **options):
        try:
            summary = run_evaluation(options["dataset_dir"])
        except EvaluationFixtureError as exc:
            raise CommandError(str(exc)) from exc
        for result in summary.results:
            state = "PASS" if result.passed else "FAIL"
            self.stdout.write(
                f"{result.case_id} {result.case_type} {state} "
                f"{result.reason} {result.duration_ms}ms"
            )
        self.stdout.write(
            f"SUMMARY passed={summary.passed} failed={summary.failed} "
            f"duration_ms={summary.duration_ms}"
        )
        if summary.failed:
            raise CommandError("evaluation_failed")
