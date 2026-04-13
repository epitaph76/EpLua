import json
from pathlib import Path

from packages.benchmark.principles import evaluate_case_by_principles


def test_principle_evaluator_accepts_semantically_equivalent_transformation() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    public_cases = json.loads((repo_root / "benchmark" / "public_cases.json").read_text(encoding="utf-8"))["cases"]
    case = next(item for item in public_cases if item["id"] == "case-04-iso8601-format")

    candidate = "\n".join(
        [
            "local DATUM = wf.vars.json.IDOC.ZCDF_HEAD.DATUM",
            "local TIME = wf.vars.json.IDOC.ZCDF_HEAD.TIME",
            "local function safe_sub(str, start, finish)",
            "  if type(str) ~= \"string\" then return \"00\" end",
            "  local s = string.sub(str, start, math.min(finish, #str))",
            "  return s ~= \"\" and s or \"00\"",
            "end",
            "local year = safe_sub(DATUM, 1, 4)",
            "local month = safe_sub(DATUM, 5, 6)",
            "local day = safe_sub(DATUM, 7, 8)",
            "local hour = safe_sub(TIME, 1, 2)",
            "local minute = safe_sub(TIME, 3, 4)",
            "local second = safe_sub(TIME, 5, 6)",
            "return string.format('%s-%s-%sT%s:%s:%s.000000Z', year, month, day, hour, minute, second)",
        ]
    )

    report = evaluate_case_by_principles(case, candidate)

    assert report["status"] == "pass"
    assert report["summary"]["passed"] >= report["summary"]["required"]


def test_principle_evaluator_rejects_wrong_patch_mode_shape() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    public_cases = json.loads((repo_root / "benchmark" / "public_cases.json").read_text(encoding="utf-8"))["cases"]
    case = next(item for item in public_cases if item["id"] == "case-07-add-squared-variable")

    candidate = '{"wf.vars.squared":"lua{return (wf.vars.number or 0) * (wf.vars.number or 0)}lua"}'
    report = evaluate_case_by_principles(case, candidate)

    assert report["status"] == "fail"
    assert any(check["name"] == "patch_expected_keys_present" and check["status"] == "fail" for check in report["checks"])


def test_principle_evaluator_accepts_direct_named_field_clearing_for_whitelist_like_task() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    public_cases = json.loads((repo_root / "benchmark" / "public_cases.json").read_text(encoding="utf-8"))["cases"]
    case = next(item for item in public_cases if item["id"] == "case-03-restbody-cleanup")

    candidate = "\n".join(
        [
            "local result = wf.vars.RESTbody.result",
            "for _, filteredEntry in pairs(result) do",
            '  filteredEntry["ID"] = nil',
            '  filteredEntry["ENTITY_ID"] = nil',
            '  filteredEntry["CALL"] = nil',
            "end",
            "",
            "return result",
        ]
    )

    report = evaluate_case_by_principles(case, candidate)

    assert report["status"] == "pass"
    assert any(
        check["name"] == "field_value_clearing" and check["status"] == "pass"
        for check in report["checks"]
    )


def test_synthetic_benchmark_pack_exists_and_has_cases() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    synthetic_path = repo_root / "benchmark" / "synthetic_cases.json"
    synthetic_cases = json.loads(synthetic_path.read_text(encoding="utf-8"))["cases"]

    assert len(synthetic_cases) >= 20
