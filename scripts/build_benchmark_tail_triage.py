from __future__ import annotations

import json
from datetime import datetime, UTC
from pathlib import Path

REPORT_PATH = Path(
    r"C:\project\luaMTS\artifacts\benchmark_runs\3_progon_2026-04-13_qwen3-coder-480b-cloud_full-128-report.json"
)
OUTPUT_PATH = Path(
    r"C:\project\luaMTS\artifacts\benchmark_runs\3_progon_2026-04-13_qwen3-coder-480b-cloud_tail-triage.json"
)

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


def main() -> None:
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    triaged_cases: list[dict[str, object]] = []

    for case in report["cases"]:
        result = case.get("result", {})
        validation_status = result.get("validation_status")
        if validation_status not in {"bounded_failure", "validator_conflict"}:
            continue

        manual = MANUAL_CASE_CLASSIFICATIONS.get(case["id"])
        if manual is None:
            continue

        iterations = result.get("validator_report", {}).get("iterations", [])
        last_iteration = iterations[-1] if iterations else {}
        semantic_report = last_iteration.get("semantic_report", {})
        rule_report = last_iteration.get("rule_report", {})
        critic_report = result.get("critic_report") or {}
        principle_evaluation = case.get("principle_evaluation") or {}

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
                "principle_status": principle_evaluation.get("status"),
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
            "source_report": str(REPORT_PATH),
            "output_path": str(OUTPUT_PATH),
        },
        "summary": {
            "triaged_case_count": len(triaged_cases),
            "primary_tail_class_counts": class_counts,
            "semantic_review_bucket_counts": bucket_counts,
        },
        "cases": triaged_cases,
    }

    OUTPUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
