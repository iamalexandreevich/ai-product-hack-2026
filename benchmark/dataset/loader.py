"""Load benchmark cases from YAML files."""

from __future__ import annotations

import logging
from pathlib import Path

import yaml
from pydantic import ValidationError

from schemas.case import BenchmarkCase

logger = logging.getLogger(__name__)


class DatasetLoadError(Exception):
    """A case file could not be read or does not satisfy the case schema."""

    def __init__(self, path: Path, message: str) -> None:
        self.path = path
        self.message = message
        super().__init__(f"{path}: {message}")


def load_case_file(path: Path) -> BenchmarkCase:
    """Parse one YAML file into a :class:`BenchmarkCase`."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise DatasetLoadError(path, f"cannot parse YAML: {exc}") from exc

    if not isinstance(raw, dict):
        raise DatasetLoadError(path, "case file must contain a YAML mapping")

    try:
        case = BenchmarkCase.model_validate(raw)
    except ValidationError as exc:
        raise DatasetLoadError(path, _format_validation_error(exc)) from exc

    case.source_path = str(path)
    return case


def load_dataset(
    path: Path,
    *,
    categories: list[str] | None = None,
    difficulties: list[str] | None = None,
    case_ids: list[str] | None = None,
) -> list[BenchmarkCase]:
    """Load every case under ``path`` (a directory or a single YAML file).

    Filters are applied after loading so that a filtered run still fails loudly on a
    broken file elsewhere in the dataset.
    """
    files = _iter_case_files(path)
    cases = [load_case_file(file) for file in files]

    if categories:
        wanted = set(categories)
        cases = [c for c in cases if c.attack_category in wanted]
    if difficulties:
        wanted = set(difficulties)
        cases = [c for c in cases if c.difficulty.value in wanted]
    if case_ids:
        wanted = set(case_ids)
        cases = [c for c in cases if c.id in wanted]

    cases.sort(key=lambda c: (c.attack_category, c.difficulty.value, c.id))
    logger.debug("loaded %d cases from %s", len(cases), path)
    return cases


def _iter_case_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise DatasetLoadError(path, "dataset path does not exist")
    files = sorted(p for p in path.rglob("*.yaml") if p.is_file())
    files += sorted(p for p in path.rglob("*.yml") if p.is_file())
    if not files:
        raise DatasetLoadError(path, "no YAML case files found")
    return sorted(set(files))


def _format_validation_error(exc: ValidationError) -> str:
    parts = []
    for err in exc.errors():
        loc = ".".join(str(item) for item in err["loc"]) or "<root>"
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)
