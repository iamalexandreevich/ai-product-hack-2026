"""Dataset loader and structural validator."""

from __future__ import annotations

import copy

import pytest

from dataset.loader import DatasetLoadError, load_case_file, load_dataset
from dataset.validator import validate_dataset
from tests.conftest import VALID_CASE, write_case

DIFFICULTIES = ["easy", "medium", "hard", "adversarial", "realistic_production"]


def _category(tmp_path, name="sample_category", count=5, difficulties=None):
    """Build a category directory holding ``count`` distinct cases."""
    difficulties = difficulties or DIFFICULTIES
    directory = tmp_path / name
    for index in range(count):
        payload = copy.deepcopy(VALID_CASE)
        payload["id"] = f"SAMPLE_{index + 1:03d}"
        payload["attack_category"] = name
        payload["difficulty"] = difficulties[index % len(difficulties)]
        payload["human_req"] = f"Request number {index}"
        payload["assistant_tool_call"]["raw"] = f"curl https://host{index}.example.net/{index}"
        write_case(directory, payload)
    return directory


def test_full_category_validates(tmp_path):
    _category(tmp_path)
    report = validate_dataset(tmp_path)
    assert report.ok, [str(i) for i in report.errors]
    assert len(report.cases) == 5


def test_category_with_four_cases_fails(tmp_path):
    _category(tmp_path, count=4)
    report = validate_dataset(tmp_path)
    assert not report.ok
    assert any("exactly 5 cases" in str(i) for i in report.errors)


def test_missing_difficulty_fails(tmp_path):
    _category(tmp_path, difficulties=["easy", "easy", "medium", "hard", "adversarial"])
    report = validate_dataset(tmp_path)
    assert not report.ok
    assert any("missing difficulty levels" in str(i) for i in report.errors)
    assert any("duplicated difficulty levels" in str(i) for i in report.errors)


def test_duplicate_ids_fail(tmp_path):
    directory = _category(tmp_path)
    other = tmp_path / "second_category"
    payload = copy.deepcopy(VALID_CASE)
    payload["id"] = "SAMPLE_001"
    payload["attack_category"] = "second_category"
    write_case(other, payload)
    assert directory.exists()

    report = validate_dataset(tmp_path, require_full_categories=False)
    assert not report.ok
    assert any("duplicate case id" in str(i) for i in report.errors)


def test_file_name_must_match_id(tmp_path):
    directory = _category(tmp_path)
    (directory / "SAMPLE_001.yaml").rename(directory / "renamed.yaml")
    report = validate_dataset(tmp_path)
    assert not report.ok
    assert any("file name must match" in str(i) for i in report.errors)


def test_directory_must_match_category(tmp_path):
    _category(tmp_path, name="sample_category")
    stray = tmp_path / "other_directory"
    payload = copy.deepcopy(VALID_CASE)
    payload["id"] = "STRAY_001"
    payload["attack_category"] = "sample_category"
    write_case(stray, payload)

    report = validate_dataset(tmp_path, require_full_categories=False)
    assert any("does not match attack_category" in str(i) for i in report.errors)


def test_exact_paraphrase_is_rejected(tmp_path):
    directory = tmp_path / "sample_category"
    for index, difficulty in enumerate(DIFFICULTIES):
        payload = copy.deepcopy(VALID_CASE)
        payload["id"] = f"SAMPLE_{index + 1:03d}"
        payload["difficulty"] = difficulty
        write_case(directory, payload)

    report = validate_dataset(tmp_path)
    assert not report.ok
    assert any("exact duplicate" in str(i) for i in report.errors)


def test_missing_required_field_is_reported_with_path(tmp_path):
    directory = tmp_path / "sample_category"
    payload = copy.deepcopy(VALID_CASE)
    del payload["expected_service_result"]
    path = write_case(directory, payload)

    report = validate_dataset(tmp_path, require_full_categories=False)
    assert not report.ok
    assert any(str(path) in str(i) for i in report.errors)


def test_load_case_file_raises_on_broken_yaml(tmp_path):
    path = tmp_path / "BROKEN.yaml"
    path.write_text("id: [unclosed", encoding="utf-8")
    with pytest.raises(DatasetLoadError):
        load_case_file(path)


def test_load_case_file_raises_on_non_mapping(tmp_path):
    path = tmp_path / "LIST.yaml"
    path.write_text("- one\n- two\n", encoding="utf-8")
    with pytest.raises(DatasetLoadError, match="YAML mapping"):
        load_case_file(path)


def test_load_dataset_filters(tmp_path):
    _category(tmp_path)
    everything = load_dataset(tmp_path)
    assert len(everything) == 5

    easy = load_dataset(tmp_path, difficulties=["easy"])
    assert [c.difficulty.value for c in easy] == ["easy"]

    picked = load_dataset(tmp_path, case_ids=["SAMPLE_002"])
    assert [c.id for c in picked] == ["SAMPLE_002"]

    none = load_dataset(tmp_path, categories=["does_not_exist"])
    assert none == []


def test_load_dataset_missing_path(tmp_path):
    with pytest.raises(DatasetLoadError, match="does not exist"):
        load_dataset(tmp_path / "nope")


def test_load_dataset_empty_directory(tmp_path):
    (tmp_path / "empty").mkdir()
    with pytest.raises(DatasetLoadError, match="no YAML case files"):
        load_dataset(tmp_path / "empty")


def test_loader_filters_by_dataset_source(tmp_path):
    """Baseline and team populations can be run separately."""
    directory = tmp_path / "sample_category"
    team = copy.deepcopy(VALID_CASE)
    team["id"] = "TEAM_001"
    baseline = copy.deepcopy(VALID_CASE)
    baseline["id"] = "BASE_001"
    baseline["dataset_source"] = "baseline"
    baseline["human_req"] = "A different request, so the two are not paraphrases."
    write_case(directory, team)
    write_case(directory, baseline)

    assert [c.id for c in load_dataset(tmp_path, dataset_sources=["baseline"])] == ["BASE_001"]
    assert [c.id for c in load_dataset(tmp_path, dataset_sources=["team"])] == ["TEAM_001"]
    assert len(load_dataset(tmp_path)) == 2
