"""Load recorded inspection cases without depending on execution code."""

from pathlib import Path

import yaml
from pydantic import ValidationError

from dataset.loader import DatasetLoadError, _format_validation_error, _iter_case_files
from schemas.inspect import InspectCase


def load_inspection_file(path: Path) -> InspectCase:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        return InspectCase.model_validate(raw)
    except ValidationError as exc:
        raise DatasetLoadError(path, _format_validation_error(exc)) from exc
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise DatasetLoadError(path, f"cannot parse YAML: {exc}") from exc


def load_inspection_files(path: Path) -> list[tuple[Path, InspectCase]]:
    return [(file, load_inspection_file(file)) for file in _iter_case_files(path)]


def load_inspections(path: Path) -> list[InspectCase]:
    cases = [case for _, case in load_inspection_files(path)]
    if len({c.id for c in cases}) != len(cases):
        raise DatasetLoadError(path, "duplicate inspection case ids")
    return cases
