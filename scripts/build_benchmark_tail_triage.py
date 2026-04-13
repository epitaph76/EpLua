from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, UTC
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS_DIR = REPO_ROOT / "artifacts" / "benchmark_runs"

MANUAL_CASE_CLASSIFICATIONS: dict[str, dict[str, str]] = {
    "case-03-restbody-cleanup": {
        "primary_tail_class": "prompt_guidance_conflict",
        "semantic_review_bucket": "ambiguous_spec",
        "notes": "Task wording, expected output, and semantic judge disagree about whether to clear target values or preserve only the named fields.",
        "suggested_next_action": "Split the intent into clear-value vs keep-only-field modes and update guidance plus semantic prompts together.",
    },
    "case-07-add-squared-variable": {
        "primary_tail_class": "true_generation_failure",
        "semantic_review_bucket": "true_model_error",
        "notes": "Patch candidate keeps drifting into full payload rewrite instead of additive patch fields.",
        "suggested_next_action": "Strengthen additive patch repair prompts and add a negative example for full payload rewrites.",
    },
    "case-08-unix-time-conversion": {
        "primary_tail_class": "benchmark_validator_conflict",
        "semantic_review_bucket": "ambiguous_spec",
        "notes": "Benchmark/gold allows the domain helper parse_iso8601_to_epoch, while validators previously required inline timezone parsing.",
        "suggested_next_action": "Keep parse_iso8601_to_epoch as an allowed domain helper or rewrite the benchmark gold to self-contained Lua.",
    },
    "synthetic-case-08-patch-add-total": {
        "primary_tail_class": "true_generation_failure",
        "semantic_review_bucket": "true_model_error",
        "notes": "Patch output keeps adding unnecessary nesting instead of the minimal additive payload.",
        "suggested_next_action": "Add another deterministic patch normalizer or stronger patch-mode repair wording.",
    },
    "synthetic-case-09-phones-to-array": {
        "primary_tail_class": "critic_prompt_ambiguity",
        "semantic_review_bucket": "ambiguous_spec",
        "notes": "Always-array candidate includes the expected non-array table wrapping pattern, but semantic critic keeps treating the contract as unresolved.",
        "suggested_next_action": "Use the deterministic always-array contract to override this semantic false positive when principle checks pass.",
    },
    "synthetic-case-11-line-items-array": {
        "primary_tail_class": "critic_prompt_ambiguity",
        "semantic_review_bucket": "ambiguous_spec",
        "notes": "The candidate checks numeric keys and wraps non-array tables, but semantic critic still flags singleton-object handling.",
        "suggested_next_action": "Use the deterministic always-array contract to override this semantic false positive when principle checks pass.",
    },
    "synthetic-case-18-reminder-unix": {
        "primary_tail_class": "benchmark_validator_conflict",
        "semantic_review_bucket": "ambiguous_spec",
        "notes": "Gold uses parse_iso8601_to_epoch, while validators previously treated the helper as undefined and not explicit enough.",
        "suggested_next_action": "Keep parse_iso8601_to_epoch as an allowed domain helper or rewrite the benchmark gold to self-contained Lua.",
    },
    "synthetic-case-20-completed-at-unix": {
        "primary_tail_class": "benchmark_validator_conflict",
        "semantic_review_bucket": "ambiguous_spec",
        "notes": "Gold uses parse_iso8601_to_epoch, while validators previously treated the helper as undefined and not explicit enough.",
        "suggested_next_action": "Keep parse_iso8601_to_epoch as an allowed domain helper or rewrite the benchmark gold to self-contained Lua.",
    },
    "lua100-task-008": {
        "primary_tail_class": "true_generation_failure",
        "semantic_review_bucket": "true_model_error",
        "notes": "Whitespace-cleaning candidate misses spaces in the gsub pattern.",
        "suggested_next_action": "Add a small string-cleanup regression example and verify direct space stripping in semantic hints.",
    },
    "lua100-task-014": {
        "primary_tail_class": "true_generation_failure",
        "semantic_review_bucket": "true_model_error",
        "notes": "Filter logic ignores empty-string handling for quantity.",
        "suggested_next_action": "Teach the semantic repair prompt to mention empty-string guards for fill-state checks.",
    },
    "lua100-task-021": {
        "primary_tail_class": "critic_prompt_ambiguity",
        "semantic_review_bucket": "ambiguous_spec",
        "notes": "The candidate wraps non-array tables and returns empty array for nil, but semantic critic still flags normalization.",
        "suggested_next_action": "Use the deterministic always-array contract to override this semantic false positive when principle checks pass.",
    },
    "lua100-task-058": {
        "primary_tail_class": "semantic_false_positive",
        "semantic_review_bucket": "semantic_false_positive",
        "notes": "Semantic message is internally contradictory about whether the successful attempt should be counted.",
        "suggested_next_action": "Manually review this case and add it to the semantic-judge regression set before changing generation prompts.",
    },
    "lua100-task-088": {
        "primary_tail_class": "true_generation_failure",
        "semantic_review_bucket": "true_model_error",
        "notes": "Generated package-volume result drops untouched package fields.",
        "suggested_next_action": "Add a preservation-focused repair hint for per-item transformations that must keep sibling fields.",
    },
    "lua100-task-093": {
        "primary_tail_class": "critic_prompt_ambiguity",
        "semantic_review_bucket": "ambiguous_spec",
        "notes": "The candidate returns the normalized array directly and wraps non-array tables, but semantic critic still flags the always-array contract.",
        "suggested_next_action": "Use the deterministic always-array contract to override this semantic false positive when principle checks pass.",
    },
}


def main(argv: list[str] | None = None) -> None:
    report_path, output_path = _resolve_paths(argv)
    build_tail_triage(report_path, output_path)


def build_tail_triage(report_path: Path, output_path: Path) -> None:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    triaged_cases: list[dict[str, object]] = []

    for case in report["cases"]:
        result = case.get("result", {})
        validation_status = result.get("validation_status")
        principle_evaluation = case.get("principle_evaluation") or {}
        principle_status = principle_evaluation.get("status")
        if validation_status not in {"bounded_failure", "validator_conflict"} and principle_status != "fail":
            continue

        manual = MANUAL_CASE_CLASSIFICATIONS.get(case["id"]) or {
            "primary_tail_class": "unclassified",
            "semantic_review_bucket": "unclassified",
            "notes": "No manual classification has been recorded for this tail case yet.",
            "suggested_next_action": "Review validator, critic, principle checks, and final candidate metadata before changing generation prompts.",
        }

        iterations = result.get("validator_report", {}).get("iterations", [])
        last_iteration = iterations[-1] if iterations else {}
        semantic_report = last_iteration.get("semantic_report", {})
        rule_report = last_iteration.get("rule_report", {})
        critic_report = result.get("critic_report") or {}
        principle_failed_checks = [
            check.get("name")
            for check in principle_evaluation.get("checks", [])
            if check.get("status") == "fail"
        ]

        semantic_message = None
        if semantic_report.get("findings"):
            semantic_message = semantic_report["findings"][0].get("message")

        rule_message = None
        if rule_report.get("findings"):
            rule_message = rule_report["findings"][0].get("message")

        triaged_cases.append(
            {
                "id": case["id"],
                "dataset": case["dataset"],
                "validation_status": validation_status,
                "critic_failure_class": critic_report.get("failure_class"),
                "principle_status": principle_status,
                "principle_failed_checks": principle_failed_checks,
                "final_candidate_source": result.get("final_candidate_source"),
                "final_candidate_iteration_index": result.get("final_candidate_iteration_index"),
                "critic_report_iteration_index": result.get("critic_report_iteration_index"),
                "primary_tail_class": manual["primary_tail_class"],
                "semantic_review_bucket": manual["semantic_review_bucket"],
                "semantic_message": semantic_message,
                "rule_message": rule_message,
                "notes": manual["notes"],
                "suggested_next_action": manual["suggested_next_action"],
            }
        )

    class_counts: dict[str, int] = {}
    bucket_counts: dict[str, int] = {}
    for item in triaged_cases:
        class_counts[item["primary_tail_class"]] = class_counts.get(item["primary_tail_class"], 0) + 1
        bucket_counts[item["semantic_review_bucket"]] = bucket_counts.get(item["semantic_review_bucket"], 0) + 1

    payload = {
        "meta": {
            "generated_at": datetime.now(UTC).isoformat(),
            "source_report": str(report_path),
            "output_path": str(output_path),
        },
        "summary": {
            "triaged_case_count": len(triaged_cases),
            "primary_tail_class_counts": class_counts,
            "semantic_review_bucket_counts": bucket_counts,
        },
        "cases": triaged_cases,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _resolve_paths(argv: list[str] | None) -> tuple[Path, Path]:
    parser = argparse.ArgumentParser(description="Build a tail triage JSON from a benchmark report.")
    parser.add_argument("--report-path", type=Path, default=None)
    parser.add_argument("--output-path", type=Path, default=None)
    args = parser.parse_args(argv)

    report_path = args.report_path or _env_path("BENCHMARK_REPORT_PATH") or _latest_report_path()
    output_path = args.output_path or _env_path("BENCHMARK_TRIAGE_OUTPUT_PATH") or _default_output_path(report_path)
    return report_path, output_path


def _env_path(name: str) -> Path | None:
    value = os.environ.get(name)
    return Path(value) if value else None


def _latest_report_path() -> Path:
    candidates = sorted(
        ARTIFACTS_DIR.glob("*full-*-report.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(f"No full benchmark reports found in {ARTIFACTS_DIR}.")
    return candidates[0]


def _default_output_path(report_path: Path) -> Path:
    stem = report_path.stem
    if stem.endswith("-report"):
        stem = stem[: -len("-report")]
    return report_path.with_name(f"{stem}_tail-triage.json")


if __name__ == "__main__":
    main()
