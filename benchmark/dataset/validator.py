"""Structural validation of the benchmark dataset.

Run before every benchmark execution. The validator fails when:

* one of the five required difficulty levels is missing from a category. Outside
  ``UNCAPPED_CATEGORIES`` a level may also not be used twice, which together pin such a
  category to five cases, or six once it adds the optional ``ultra_hard`` level; inside
  it (the ``benign_utility`` control group) levels repeat freely and the size is not
  capped;
* case ids are duplicated;
* required fields are missing or invalid (enforced by the pydantic schema at load time);
* ``human_req`` or ``assistant_tool_call`` is missing (schema);
* the directory name does not match ``attack_category``, or the file name does not match ``id``;
* two cases inside a category are exact paraphrases of each other.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from dataset.loader import DatasetLoadError, _iter_case_files, load_case_file
from schemas.case import (
    REQUIRED_DIFFICULTIES,
    UNCAPPED_CATEGORIES,
    BenchmarkCase,
    Difficulty,
)

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    scope: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity}] {self.scope}: {self.message}"


@dataclass
class ValidationReport:
    cases: list[BenchmarkCase] = field(default_factory=list)
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == SEVERITY_ERROR]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == SEVERITY_WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    def add_error(self, scope: str, message: str) -> None:
        self.issues.append(ValidationIssue(SEVERITY_ERROR, scope, message))

    def add_warning(self, scope: str, message: str) -> None:
        self.issues.append(ValidationIssue(SEVERITY_WARNING, scope, message))


def validate_dataset(path: Path, *, require_full_categories: bool = True) -> ValidationReport:
    """Load and validate the dataset at ``path``.

    ``require_full_categories`` is switched off when validating a subset (a single file
    or a filtered selection), where "every required difficulty present" cannot hold.
    """
    report = ValidationReport()

    try:
        files = _iter_case_files(path)
    except DatasetLoadError as exc:
        report.add_error(str(path), exc.message)
        return report

    for file in files:
        try:
            case = load_case_file(file)
        except DatasetLoadError as exc:
            report.add_error(str(exc.path), exc.message)
            continue
        report.cases.append(case)
        _check_file_layout(case, file, path, report)

    _check_unique_ids(report)
    if require_full_categories:
        _check_categories(report)
    _check_paraphrases(report)
    return report


def _check_file_layout(
    case: BenchmarkCase, file: Path, root: Path, report: ValidationReport
) -> None:
    if file.stem != case.id:
        report.add_error(str(file), f"file name must match case id {case.id!r}")
    if root.is_dir() and file.parent != root:
        directory = file.parent.name
        if directory != case.attack_category:
            report.add_error(
                str(file),
                f"directory {directory!r} does not match attack_category {case.attack_category!r}",
            )


def _check_unique_ids(report: ValidationReport) -> None:
    counts = Counter(case.id for case in report.cases)
    for case_id, count in sorted(counts.items()):
        if count > 1:
            paths = [c.source_path for c in report.cases if c.id == case_id]
            report.add_error("dataset", f"duplicate case id {case_id!r} in {paths}")


def _check_categories(report: ValidationReport) -> None:
    by_category: dict[str, list[BenchmarkCase]] = defaultdict(list)
    for case in report.cases:
        by_category[case.attack_category].append(case)

    for category, cases in sorted(by_category.items()):
        # No separate count check: "every required level present" and "no level twice"
        # already pin the size to five, or six with the optional ``ultra_hard``. The
        # second half is skipped for the control group, whose size is deliberately
        # uncapped — see ``UNCAPPED_CATEGORIES``.
        seen = Counter(case.difficulty for case in cases)
        missing = sorted(d.value for d in REQUIRED_DIFFICULTIES if d not in seen)
        if missing:
            report.add_error(category, f"missing difficulty levels: {', '.join(missing)}")
        if category in UNCAPPED_CATEGORIES:
            continue
        duplicated = sorted(d.value for d, n in seen.items() if n > 1)
        if duplicated:
            report.add_error(category, f"duplicated difficulty levels: {', '.join(duplicated)}")


def _check_paraphrases(report: ValidationReport) -> None:
    """Guard against a category filled with near-identical cases."""
    by_category: dict[str, list[BenchmarkCase]] = defaultdict(list)
    for case in report.cases:
        by_category[case.attack_category].append(case)

    for category, cases in sorted(by_category.items()):
        signatures: dict[tuple[str, str], str] = {}
        for case in sorted(cases, key=lambda c: c.id):
            signature = (
                " ".join(case.human_req.split()).lower(),
                " ".join(case.assistant_tool_call.raw.split()).lower(),
            )
            if signature in signatures:
                report.add_error(
                    category,
                    f"case {case.id} is an exact duplicate of {signatures[signature]} "
                    "(same human_req and raw action)",
                )
            else:
                signatures[signature] = case.id

        raws = [" ".join(c.assistant_tool_call.raw.split()).lower() for c in cases]
        for i, left in enumerate(raws):
            for right in raws[i + 1 :]:
                if left and right and _similarity(left, right) > 0.9:
                    report.add_warning(
                        category,
                        "two actions are more than 90% similar; check that the cases in "
                        "this category differ in more than wording",
                    )
                    break


def _similarity(left: str, right: str) -> float:
    left_tokens, right_tokens = set(left.split()), set(right.split())
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def difficulty_coverage(cases: list[BenchmarkCase]) -> dict[str, list[str]]:
    """Difficulty levels present per category — used by the CLI ``validate`` output."""
    coverage: dict[str, list[str]] = defaultdict(list)
    for case in sorted(cases, key=lambda c: (c.attack_category, c.difficulty.value)):
        coverage[case.attack_category].append(case.difficulty.value)
    return dict(coverage)


__all__ = [
    "Difficulty",
    "ValidationIssue",
    "ValidationReport",
    "difficulty_coverage",
    "validate_dataset",
]
