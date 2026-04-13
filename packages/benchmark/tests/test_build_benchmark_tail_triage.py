import json

from scripts.build_benchmark_tail_triage import main


def test_tail_triage_uses_explicit_report_and_output_paths(tmp_path) -> None:
    report_path = tmp_path / "full-report.json"
    output_path = tmp_path / "tail-triage.json"
    report_path.write_text(
        json.dumps(
            {
                "cases": [
                    {
                        "id": "case-07-add-squared-variable",
                        "dataset": "fixture",
                        "result": {
                            "validation_status": "bounded_failure",
                            "critic_report": {"failure_class": "repair_oscillation"},
                            "validator_report": {
                                "iterations": [
                                    {
                                        "semantic_report": {
                                            "findings": [{"message": "semantic mismatch"}],
                                        },
                                        "rule_report": {
                                            "findings": [{"message": "rule mismatch"}],
                                        },
                                    }
                                ]
                            },
                        },
                        "principle_evaluation": {"status": "fail"},
                    },
                    {
                        "id": "new-holdout-case",
                        "dataset": "fixture",
                        "result": {
                            "validation_status": "bounded_failure",
                            "critic_report": {"failure_class": "semantic_mismatch"},
                            "validator_report": {
                                "iterations": [
                                    {
                                        "semantic_report": {
                                            "findings": [{"message": "new semantic mismatch"}],
                                        },
                                        "rule_report": {"findings": []},
                                    }
                                ]
                            },
                        },
                        "principle_evaluation": {"status": "pass"},
                    },
                    {
                        "id": "principle-fail-only-case",
                        "dataset": "fixture",
                        "result": {
                            "validation_status": "passed",
                            "critic_report": None,
                            "validator_report": {"iterations": []},
                        },
                        "principle_evaluation": {
                            "status": "fail",
                            "checks": [
                                {
                                    "name": "type_normalization_guard",
                                    "status": "fail",
                                    "message": "Normalization guard missing.",
                                }
                            ],
                        },
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    main(["--report-path", str(report_path), "--output-path", str(output_path)])

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["meta"]["source_report"] == str(report_path)
    assert payload["summary"]["triaged_case_count"] == 3
    assert payload["cases"][0]["id"] == "case-07-add-squared-variable"
    assert payload["cases"][1]["id"] == "new-holdout-case"
    assert payload["cases"][1]["primary_tail_class"] == "unclassified"
    assert payload["cases"][2]["id"] == "principle-fail-only-case"
    assert payload["cases"][2]["principle_failed_checks"] == ["type_normalization_guard"]
