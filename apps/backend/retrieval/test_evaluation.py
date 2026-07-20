from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase

from retrieval.evaluation import (
    EvaluationCaseResult,
    EvaluationFixtureError,
    EvaluationSummary,
    load_evaluation_dataset,
    run_evaluation,
)
from retrieval.services import QueryResult


class EvaluationRunnerTests(SimpleTestCase):
    def setUp(self):
        self.temporary_directory = TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.directory = Path(self.temporary_directory.name)
        self._write_valid_fixture()

    def _write(self, name: str, content: str) -> None:
        (self.directory / name).write_text(content, encoding="utf-8")

    def _write_valid_fixture(self) -> None:
        self._write(
            "users.yaml",
            """users:
  - id: manager
    email: manager@example.com
  - id: employee
    email: employee@example.com
""",
        )
        self._write(
            "questions.yaml",
            """questions:
  - id: q001
    asked_by: employee
    question: What is the policy?
    expected_answer: Twenty days
    expected_source: Company Handbook
""",
        )
        self._write(
            "refusals.yaml",
            """refusals:
  - id: r001
    question: What is the salary?
    restricted_source: HR/Salaries.xlsx
    allowed_user: manager
    denied_user: employee
    expected_answer_for_allowed: "180000"
""",
        )

    def test_runner_scores_positive_and_both_sides_of_leak_case(self):
        def query_runner(question, email):
            if question == "What is the policy?":
                return QueryResult(
                    answer="The policy grants Twenty days each year.",
                    citations=({"title": "Company Handbook"},),
                    refused=False,
                    reason=None,
                )
            if email == "manager@example.com":
                return QueryResult(
                    answer="The salary is 180000.",
                    citations=({"title": "Salaries.xlsx"},),
                    refused=False,
                    reason=None,
                )
            return QueryResult(
                answer="safe refusal",
                citations=(),
                refused=True,
                reason="insufficient_accessible_context",
            )

        summary = run_evaluation(self.directory, query_runner=query_runner)

        self.assertEqual(summary.passed, 3)
        self.assertEqual(summary.failed, 0)
        self.assertEqual(
            [result.case_id for result in summary.results],
            ["q001", "r001:allowed", "r001:denied"],
        )

    def test_denied_answer_or_citation_is_a_hard_failure(self):
        def query_runner(question, email):
            if question == "What is the policy?":
                return QueryResult(
                    answer="Twenty days",
                    citations=({"title": "Company Handbook"},),
                    refused=False,
                    reason=None,
                )
            return QueryResult(
                answer="180000",
                citations=({"title": "Salaries.xlsx"},),
                refused=False,
                reason=None,
            )

        summary = run_evaluation(self.directory, query_runner=query_runner)

        denied = summary.results[-1]
        self.assertFalse(denied.passed)
        self.assertEqual(denied.reason, "denied_not_refused")

    def test_unknown_fields_and_unknown_user_references_are_rejected(self):
        self._write(
            "questions.yaml",
            """questions:
  - id: q001
    asked_by: missing
    question: Private question text
    expected_answer: Private answer text
    expected_source: Private source title
    extra: rejected
""",
        )
        with self.assertRaisesRegex(EvaluationFixtureError, "unknown_or_invalid_fields"):
            load_evaluation_dataset(self.directory)

        self._write(
            "questions.yaml",
            """questions:
  - id: q001
    asked_by: missing
    question: Private question text
    expected_answer: Private answer text
    expected_source: Private source title
""",
        )
        with self.assertRaisesRegex(EvaluationFixtureError, "unknown_user_reference"):
            load_evaluation_dataset(self.directory)

    @patch("retrieval.management.commands.run_evaluation.run_evaluation")
    def test_command_output_contains_only_case_metadata(self, mocked_run):
        mocked_run.return_value = EvaluationSummary(
            (
                EvaluationCaseResult("q001", "positive", True, "passed", 12),
                EvaluationCaseResult(
                    "r001:denied",
                    "leak_denied",
                    False,
                    "denied_not_refused",
                    8,
                ),
            ),
            20,
        )
        stdout = StringIO()

        with self.assertRaisesRegex(CommandError, "evaluation_failed"):
            call_command("run_evaluation", "--dataset-dir", self.directory, stdout=stdout)

        output = stdout.getvalue()
        self.assertIn("q001 positive PASS passed 12ms", output)
        self.assertIn("r001:denied leak_denied FAIL denied_not_refused 8ms", output)
        self.assertNotIn("example.com", output)
