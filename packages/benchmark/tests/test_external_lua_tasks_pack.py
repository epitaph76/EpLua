import json
from pathlib import Path

from packages.retrieval import selector as retrieval_selector


def test_external_lua_tasks_pack_exists_and_has_100_cases() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    dataset_path = repo_root / "benchmark" / "lua_tasks_100_cases.json"

    payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    cases = payload["cases"]

    assert payload["dataset_id"] == "localscript-lua-tasks-100-holdout"
    assert payload["retrieval_eligible"] is False
    assert len(cases) == 100
    assert all(case["primary_output_mode"] == "raw_lua" for case in cases)
    assert all(case["allowed_output_modes"] == ["raw_lua"] for case in cases)
    assert all(case["id"].startswith("lua100-task-") for case in cases)
    assert all("\n" not in case["category"] for case in cases)


def test_additional_lua_tasks_pack_exists_and_has_200_cases() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    dataset_path = repo_root / "benchmark" / "lua_tasks_additional_200_cases.json"

    payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    cases = payload["cases"]

    assert payload["dataset_id"] == "localscript-lua-tasks-additional-200-holdout"
    assert payload["retrieval_eligible"] is False
    assert len(cases) == 200
    assert all(case["primary_output_mode"] == "raw_lua" for case in cases)
    assert all(case["allowed_output_modes"] == ["raw_lua"] for case in cases)
    assert all(case["id"].startswith("lua-additional-200-task-") for case in cases)
    assert all("\n" not in case["category"] for case in cases)


def test_external_lua_tasks_packs_stay_out_of_retrieval_examples() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    dataset_paths = [
        repo_root / "benchmark" / "lua_tasks_100_cases.json",
        repo_root / "benchmark" / "lua_tasks_additional_200_cases.json",
    ]
    benchmark_ids: set[str] = set()
    for dataset_path in dataset_paths:
        benchmark_payload = json.loads(dataset_path.read_text(encoding="utf-8"))
        benchmark_ids.update(case["id"] for case in benchmark_payload["cases"])

    retrieval_selector._load_examples.cache_clear()
    retrieval_ids = {example["id"] for example in retrieval_selector._load_examples()}

    assert benchmark_ids.isdisjoint(retrieval_ids)


def test_semantic_judge_regression_pack_exists_with_review_buckets() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    dataset_path = repo_root / "benchmark" / "semantic_judge_regressions.json"
    regressions = json.loads(dataset_path.read_text(encoding="utf-8"))["cases"]

    assert any(case["bucket"] == "semantic_false_positive" for case in regressions)
    assert any(case["bucket"] == "ambiguous_spec" for case in regressions)
    assert any(case["id"] == "lua100-task-058" for case in regressions)
