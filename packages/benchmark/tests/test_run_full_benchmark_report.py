import json
from datetime import UTC, datetime

import pytest

import scripts.run_full_benchmark_report as report_script


def test_default_benchmark_report_path_does_not_overwrite_locked_5th_baseline() -> None:
    output_path = report_script._default_output_path(
        now=datetime(2026, 4, 13, 16, 30, tzinfo=UTC),
        model="qwen3-coder:480b-cloud",
    )

    assert output_path.name == "20260413T163000Z_qwen3-coder-480b-cloud_full-328-report.json"
    assert "5_progon_2026-04-13_qwen3-coder-480b-cloud_full-328-report.json" not in str(output_path)


def test_default_benchmark_model_is_local_when_env_is_missing(monkeypatch) -> None:
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)

    assert report_script._default_model() == "qwen3.5:9b"


def test_release_benchmark_rejects_cloud_model() -> None:
    with pytest.raises(SystemExit, match="Cloud Ollama model tags are not allowed"):
        report_script._ensure_benchmark_model_allowed("qwen3-coder:480b-cloud", "release")


def test_full_benchmark_report_omits_exact_match_metrics(tmp_path, monkeypatch) -> None:
    cases_path = tmp_path / "cases.json"
    cases_path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "case-exact-match-removed",
                        "title": "No canonical match metric",
                        "prompt": "Return a number.",
                        "context": None,
                        "archetype": "transformation",
                        "primary_output_mode": "raw_lua",
                        "input_roots": ["wf.vars.value"],
                        "risk_tags": [],
                        "source_ref": "test",
                        "expected_outputs": {"raw_lua": "return wf.vars.value"},
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    output_path = tmp_path / "report.json"

    class FakeGenerationService:
        def generate(self, **_request):
            return {"code": "return wf.vars.value", "validation_status": "passed"}

    monkeypatch.setattr(report_script, "DATASETS", (("fixture", cases_path),))
    monkeypatch.setattr(report_script, "OUTPUT_PATH", output_path)
    monkeypatch.setattr(report_script, "GenerationService", FakeGenerationService)
    monkeypatch.setattr(
        report_script,
        "evaluate_case_by_principles",
        lambda _case, _candidate: {"status": "pass"},
    )

    report_script.main()

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert "exact_match" not in report["cases"][0]
    assert "exact_match_count" not in report["summary"]

