"""Structural validation of the inspect corpus.

Runs before every inspect execution, exactly as ``dataset/validator.py`` does for the
decide dataset, so a broken case never reaches the service. It reuses that module's
``ValidationReport`` rather than growing a second reporting shape.

What it enforces, and why each rule earns its place (see ``attacks/inspect/taxonomy.md``):

* file name = ``id``, directory name = ``category``, ids unique across the corpus — the
  same layout discipline the decide dataset has, for the same reason: a case you cannot
  find by id is a case nobody maintains;
* no two cases in a category share an ``output`` under the same provenance, which is how
  a copy-paste case that measures nothing new gets caught;
* a ``tier: detector`` case must declare ``expected_rule_id_prefix``. In this corpus that
  field is **scored**, unlike the decide dataset where it is informational: knowing the
  text was held back is not enough, because a regression in which an injection is caught
  by the long-blob detector instead would otherwise pass unnoticed;
* ``request_overrides`` and ``api_refusal`` live only in the ``api_refusal`` category,
  so wire-level malformation cannot leak into a case that claims to measure detection;
* every provenance kind the contract defines appears somewhere in the corpus — an
  injection in a workspace file and one in a fetched page are different risks, and a
  corpus that only ever exercises one of them cannot say so;
* the benign control group is not tiny (warning, not error): preservation and
  intervention rates are read off it and their resolution is 1/N.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

from dataset.inspect_loader import load_inspection_file
from dataset.loader import DatasetLoadError, _iter_case_files
from dataset.validator import ValidationReport
from schemas.inspect import InspectCase

REFUSAL_CATEGORY = "api_refusal"
PROVENANCE_KINDS = ("file", "web", "mcp", "shell", "subagent", "unknown")
MIN_BENIGN_CASES = 10


def validate_inspect_dataset(path: Path, *, require_full_corpus: bool = True) -> ValidationReport:
    """Load and check the corpus at ``path``; a failing report aborts the run."""
    report = ValidationReport()
    try:
        files = _iter_case_files(path)
    except DatasetLoadError as exc:
        report.add_error(str(exc.path), exc.message)
        return report
    for case_path in files:
        try:
            case = load_inspection_file(case_path)
        except DatasetLoadError as exc:
            report.add_error(str(exc.path), exc.message)
            continue
        report.cases.append(case)
        _check_layout(report, case_path, case)
        _check_tier(report, case)
        _check_refusals(report, case)

    _check_unique_ids(report)
    _check_duplicate_outputs(report)
    if require_full_corpus:
        _check_provenance_coverage(report)
        _check_control_group(report)
    return report


def _check_layout(report: ValidationReport, path: Path, case: InspectCase) -> None:
    if path.stem != case.id:
        report.add_error(case.id, f"file name {path.stem!r} does not match id {case.id!r}")
    if path.parent.name != case.category:
        report.add_error(
            case.id, f"directory {path.parent.name!r} does not match category {case.category!r}"
        )


def _check_tier(report: ValidationReport, case: InspectCase) -> None:
    # Only an attack case names a rule. A benign case settled at this tier is one no
    # detector fires on (that is *why* stage 1 settles it), and an API refusal never
    # reaches a detector at all — neither has a rule id to declare.
    names_a_rule = case.tier == "detector" and not case.benign and not case.api_refusal
    if names_a_rule and not case.expected_rule_id_prefix:
        report.add_error(
            case.id,
            "a tier=detector attack case must declare expected_rule_id_prefix: in this corpus the "
            "rule that fired is scored, so that a verdict reached by the wrong detector still fails",
        )
    if case.tier == "detector" and case.benign and case.expected_rule_id_prefix:
        report.add_error(
            case.id,
            "a benign case settled at tier=detector fires no detector, so it can name no rule id; "
            "if a detector does fire on it, the case belongs in tier=classifier",
        )


def _check_refusals(report: ValidationReport, case: InspectCase) -> None:
    if case.api_refusal and case.category != REFUSAL_CATEGORY:
        report.add_error(
            case.id, f"api_refusal cases belong in {REFUSAL_CATEGORY}/, not {case.category!r}"
        )
    if case.category == REFUSAL_CATEGORY and not case.api_refusal:
        report.add_error(case.id, f"a case in {REFUSAL_CATEGORY}/ must set api_refusal: true")


def _check_unique_ids(report: ValidationReport) -> None:
    for case_id, count in Counter(c.id for c in report.cases).items():
        if count > 1:
            report.add_error(case_id, f"id used by {count} cases")


def _check_duplicate_outputs(report: ValidationReport) -> None:
    seen: dict[tuple[str, str, str, str], str] = {}
    for case in report.cases:
        # API refusals deliberately reuse one innocuous output: what distinguishes them
        # is the malformed wire field, so that is what makes them distinct here too.
        key = (
            case.category,
            case.output,
            json.dumps(case.provenance.model_dump(), sort_keys=True),
            json.dumps(case.request_overrides, sort_keys=True),
        )
        if key in seen:
            report.add_error(
                case.id,
                f"same output and provenance as {seen[key]} in category {case.category!r}",
            )
        else:
            seen[key] = case.id


def _check_provenance_coverage(report: ValidationReport) -> None:
    present = {c.provenance.kind for c in report.cases}
    missing = [kind for kind in PROVENANCE_KINDS if kind not in present]
    if missing:
        report.add_error(
            "corpus",
            f"no case carries provenance {', '.join(missing)}: the contract defines these and "
            "the corpus cannot say whether the guard treats them differently",
        )


def _check_control_group(report: ValidationReport) -> None:
    benign = [c for c in report.cases if c.benign]
    if not benign:
        report.add_error(
            "corpus",
            "no benign control cases: without them a guard that drops everything scores "
            "perfectly on containment and destroys the agent",
        )
        return
    if len(benign) < MIN_BENIGN_CASES:
        report.add_warning(
            "corpus",
            f"only {len(benign)} benign control case(s); preservation and intervention rates "
            f"have resolution 1/N, so fewer than {MIN_BENIGN_CASES} makes them coarse",
        )


def tier_coverage(cases: list[InspectCase]) -> dict[str, list[str]]:
    """Which categories exercise which tier — printed by ``cli.py inspect --validate``."""
    coverage: dict[str, list[str]] = defaultdict(list)
    for case in cases:
        coverage[case.category].append(case.tier)
    return {category: sorted(set(tiers)) for category, tiers in sorted(coverage.items())}
