"""Operator-run evaluation with privacy-safe, non-persistent results."""

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

from retrieval.services import QueryResult, answer_query

_MAX_FIXTURE_BYTES = 1_048_576
_MAX_CASES_PER_FILE = 100
_USER_FIELDS = {"id", "email", "description", "can_access", "cannot_access"}
_QUESTION_FIELDS = {
    "id",
    "asked_by",
    "question",
    "expected_answer",
    "expected_source",
    "source_type",
    "notes",
}
_REFUSAL_FIELDS = {
    "id",
    "question",
    "restricted_source",
    "allowed_user",
    "denied_user",
    "expected_answer_for_allowed",
    "leak_type",
    "notes",
}


class EvaluationFixtureError(ValueError):
    """The private operator fixture does not match the tracked schema."""


@dataclass(frozen=True)
class EvaluationCaseResult:
    case_id: str
    case_type: str
    passed: bool
    reason: str
    duration_ms: int


@dataclass(frozen=True)
class EvaluationSummary:
    results: tuple[EvaluationCaseResult, ...]
    duration_ms: int

    @property
    def passed(self) -> int:
        return sum(result.passed for result in self.results)

    @property
    def failed(self) -> int:
        return len(self.results) - self.passed


@dataclass(frozen=True)
class _EvaluationDataset:
    users: dict[str, str]
    questions: tuple[dict, ...]
    refusals: tuple[dict, ...]


def _nonempty_string(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _load_rows(path: Path, *, root_key: str, fields: set[str], required: set[str]) -> list[dict]:
    try:
        if path.stat().st_size > _MAX_FIXTURE_BYTES:
            raise EvaluationFixtureError(f"{path.name}: fixture_too_large")
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise EvaluationFixtureError(f"{path.name}: unreadable_fixture") from exc
    if not isinstance(payload, dict) or set(payload) != {root_key}:
        raise EvaluationFixtureError(f"{path.name}: invalid_top_level_schema")
    rows = payload[root_key]
    if not isinstance(rows, list) or not 1 <= len(rows) <= _MAX_CASES_PER_FILE:
        raise EvaluationFixtureError(f"{path.name}: invalid_row_count")
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or not set(row).issubset(fields):
            raise EvaluationFixtureError(f"{path.name}: row_{index}_unknown_or_invalid_fields")
        if not required.issubset(row) or not all(_nonempty_string(row[key]) for key in required):
            raise EvaluationFixtureError(f"{path.name}: row_{index}_missing_required_field")
    return rows


def load_evaluation_dataset(directory: Path) -> _EvaluationDataset:
    users = _load_rows(
        directory / "users.yaml",
        root_key="users",
        fields=_USER_FIELDS,
        required={"id", "email"},
    )
    questions = _load_rows(
        directory / "questions.yaml",
        root_key="questions",
        fields=_QUESTION_FIELDS,
        required={"id", "asked_by", "question", "expected_answer", "expected_source"},
    )
    refusals = _load_rows(
        directory / "refusals.yaml",
        root_key="refusals",
        fields=_REFUSAL_FIELDS,
        required={
            "id",
            "question",
            "restricted_source",
            "allowed_user",
            "denied_user",
            "expected_answer_for_allowed",
        },
    )
    identifiers = [row["id"] for row in users]
    case_identifiers = [row["id"] for row in (*questions, *refusals)]
    if len(set(identifiers)) != len(identifiers):
        raise EvaluationFixtureError("users.yaml: duplicate_id")
    if len(set(case_identifiers)) != len(case_identifiers):
        raise EvaluationFixtureError("evaluation_cases: duplicate_id")
    user_map = {row["id"]: row["email"] for row in users}
    referenced_users = {
        *(row["asked_by"] for row in questions),
        *(row["allowed_user"] for row in refusals),
        *(row["denied_user"] for row in refusals),
    }
    if not referenced_users.issubset(user_map):
        raise EvaluationFixtureError("evaluation_cases: unknown_user_reference")
    return _EvaluationDataset(user_map, tuple(questions), tuple(refusals))


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def _has_expected_source(result: QueryResult, expected_source: str) -> bool:
    expected = _normalize(expected_source)
    expected_name = _normalize(Path(expected_source).name)
    return any(
        _normalize(str(citation.get("title", ""))) in {expected, expected_name}
        for citation in result.citations
    )


def _score_positive(result: QueryResult, *, expected_answer: str, expected_source: str) -> str:
    if result.refused:
        return "unexpected_refusal"
    if _normalize(expected_answer) not in _normalize(result.answer):
        return "answer_mismatch"
    if not _has_expected_source(result, expected_source):
        return "source_missing"
    return "passed"


def _timed_query(
    query_runner: Callable[[str, str], QueryResult],
    question: str,
    email: str,
) -> tuple[QueryResult, int]:
    started = time.monotonic()
    result = query_runner(question, email)
    return result, round((time.monotonic() - started) * 1000)


def run_evaluation(
    directory: Path,
    *,
    query_runner: Callable[[str, str], QueryResult] = answer_query,
) -> EvaluationSummary:
    """Run real permission-safe queries; retain only IDs, outcomes, and timings."""
    dataset = load_evaluation_dataset(directory)
    started = time.monotonic()
    results: list[EvaluationCaseResult] = []
    for case in dataset.questions:
        result, duration = _timed_query(
            query_runner,
            case["question"],
            dataset.users[case["asked_by"]],
        )
        reason = _score_positive(
            result,
            expected_answer=case["expected_answer"],
            expected_source=case["expected_source"],
        )
        results.append(
            EvaluationCaseResult(case["id"], "positive", reason == "passed", reason, duration)
        )
    for case in dataset.refusals:
        allowed, allowed_duration = _timed_query(
            query_runner,
            case["question"],
            dataset.users[case["allowed_user"]],
        )
        allowed_reason = _score_positive(
            allowed,
            expected_answer=case["expected_answer_for_allowed"],
            expected_source=case["restricted_source"],
        )
        results.append(
            EvaluationCaseResult(
                f"{case['id']}:allowed",
                "leak_allowed",
                allowed_reason == "passed",
                allowed_reason,
                allowed_duration,
            )
        )
        denied, denied_duration = _timed_query(
            query_runner,
            case["question"],
            dataset.users[case["denied_user"]],
        )
        if not denied.refused:
            denied_reason = "denied_not_refused"
        elif denied.citations:
            denied_reason = "denied_citations_present"
        else:
            denied_reason = "passed"
        results.append(
            EvaluationCaseResult(
                f"{case['id']}:denied",
                "leak_denied",
                denied_reason == "passed",
                denied_reason,
                denied_duration,
            )
        )
    return EvaluationSummary(tuple(results), round((time.monotonic() - started) * 1000))
