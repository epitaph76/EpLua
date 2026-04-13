from __future__ import annotations

import json
import re

from packages.shared.quality import ValidationFinding, ValidatorReport

_THINK_BLOCK_PATTERN = re.compile(r"<think>\s*(.*?)\s*</think>", re.DOTALL | re.IGNORECASE)
_CONTROL_TOKEN_PATTERN = re.compile(r"<\|[A-Za-z0-9_:-]+\|>")
_CODE_FENCE_PATTERN = re.compile(r"```(?:json|lua)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_PATTERN_BASED_PRINCIPLE_FAILURES = {
    "missing_field_whitelist_pattern",
}
_MAX_REPAIR_ATTEMPTS = 3


def build_critic_report(
    format_report: ValidatorReport,
    syntax_report: ValidatorReport,
    static_report: ValidatorReport,
    principle_report: ValidatorReport,
    semantic_report: ValidatorReport,
    *,
    output_mode: str,
    repair_count: int,
    clarification_count: int,
    repeated_failure_class: bool,
    oscillation_detected: bool,
    task_intents: tuple[str, ...] = (),
) -> dict[str, object]:
    findings = [
        *format_report.findings,
        *syntax_report.findings,
        *static_report.findings,
        *principle_report.findings,
        *semantic_report.findings,
    ]
    if not findings:
        return {
            "action": "finalize",
            "failure_class": None,
            "message": "Validation passed.",
        }

    primary = findings[0]

    if _has_validator_conflict(principle_report, semantic_report):
        semantic_priority_report = _semantic_priority_conflict_resolution(
            semantic_report=semantic_report,
            task_intents=task_intents,
            repair_count=repair_count,
        )
        if semantic_priority_report is not None:
            return semantic_priority_report
        return {
            "action": "finalize",
            "failure_class": "validator_conflict",
            "message": "Semantic and pattern-based validators disagree on the repair direction.",
        }

    if primary.ambiguous and clarification_count < 1:
        return {
            "action": "clarification",
            "failure_class": primary.failure_class,
            "message": primary.message,
            "clarification_question": primary.suggestion or "What should be clarified before the next attempt?",
        }

    if oscillation_detected:
        return {
            "action": "finalize",
            "failure_class": "repair_oscillation",
            "message": "Repair loop started oscillating between previously seen candidates or failure patterns.",
        }

    if repair_count >= _MAX_REPAIR_ATTEMPTS or repeated_failure_class:
        return {
            "action": "finalize",
            "failure_class": primary.failure_class,
            "message": "Repair budget exhausted or the same failure repeated after the latest repair.",
        }

    if not primary.repairable:
        if clarification_count < 1:
            return {
                "action": "clarification",
                "failure_class": primary.failure_class,
                "message": primary.message,
                "clarification_question": primary.suggestion
                or "What additional input is needed to safely continue?",
            }
        return {
            "action": "finalize",
            "failure_class": primary.failure_class,
            "message": "Validation failed with a non-repairable issue.",
        }

    repair_message, repair_prompt = _build_repair_instructions(primary, output_mode)
    return {
        "action": "repair",
        "failure_class": primary.failure_class,
        "message": repair_message,
        "repair_prompt": repair_prompt,
    }


def _build_repair_instructions(finding: ValidationFinding, output_mode: str) -> tuple[str, str]:
    if finding.failure_class == "semantic_mismatch":
        return (
            "Repair the candidate to match the task semantics without changing the user goal.",
            finding.suggestion
            or "Return the same output mode and user goal, but repair the candidate so it satisfies the task semantics. Return only the repaired result.",
        )

    if finding.failure_class == "markdown_fence" and output_mode == "raw_lua":
        return (
            "Remove markdown fences and keep the output in raw_lua mode.",
            "Return only raw Lua code. Remove markdown fences and any surrounding explanation without changing the user goal.",
        )

    if finding.failure_class == "invalid_wrapper":
        return (
            "Wrap every generated code string with lua{...}lua without changing the JSON shape.",
            "Return the same JSON object shape, but ensure every generated code string uses lua{...}lua wrappers and contains no extra prose.",
        )

    if finding.failure_class in {"disallowed_data_root", "mixed_root_families"}:
        return (
            "Keep the same user goal, but restrict the candidate to the allowed wf.* data roots.",
            "Repair the candidate by using only the allowed wf.* data roots. Do not invent new roots and do not change the user goal.",
        )

    if finding.failure_class.startswith("missing_") or finding.failure_class.startswith("empty_"):
        return (
            "Add the missing domain-specific element without changing the requested result shape.",
            "Repair the candidate by adding the missing domain-specific logic while preserving the requested output mode and user goal.",
        )

    return (
        f"Repair the candidate for failure class {finding.failure_class} without changing the user goal.",
        "Repair the current candidate using the validator finding. Keep the same output mode, preserve the user goal, and return only the repaired result.",
    )


def _has_validator_conflict(principle_report: ValidatorReport, semantic_report: ValidatorReport) -> bool:
    if principle_report.status != "fail" or semantic_report.status != "fail":
        return False
    return any(
        finding.failure_class in _PATTERN_BASED_PRINCIPLE_FAILURES
        for finding in principle_report.findings
    ) and any(
        finding.failure_class == "semantic_mismatch"
        for finding in semantic_report.findings
    )


def _semantic_priority_conflict_resolution(
    *,
    semantic_report: ValidatorReport,
    task_intents: tuple[str, ...],
    repair_count: int,
) -> dict[str, object] | None:
    if repair_count >= _MAX_REPAIR_ATTEMPTS:
        return None

    if not {"clear_target_fields", "remove_target_fields"} & set(task_intents):
        return None

    semantic_finding = next(
        (finding for finding in semantic_report.findings if finding.failure_class == "semantic_mismatch"),
        None,
    )
    if semantic_finding is None:
        return None

    return {
        "action": "repair",
        "failure_class": semantic_finding.failure_class,
        "message": "Prefer semantic intent over the pattern-based rule for explicit field-operation tasks.",
        "repair_prompt": semantic_finding.suggestion
        or "Repair the candidate to match the explicit field-operation intent and keep unrelated fields intact.",
    }


def build_semantic_critic_prompt(
    *,
    prompt: str,
    candidate: str,
    output_mode: str,
    format_report: ValidatorReport,
    syntax_report: ValidatorReport,
    static_report: ValidatorReport,
    principle_report: ValidatorReport,
) -> str:
    validator_summary = json.dumps(
        {
            "output_mode": output_mode,
            "format_report": format_report.to_dict(),
            "syntax_report": syntax_report.to_dict(),
            "static_report": static_report.to_dict(),
            "principle_report": principle_report.to_dict(),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return "\n".join(
        [
            "You are the semantic critic for a LocalScript Lua generation system.",
            "Judge whether the candidate actually satisfies the task and the provided context.",
            "Do not re-check markdown fences, syntax, or simple formatting unless they change the task meaning.",
            "Focus on semantic correctness: wrong field, wrong array item, wrong transformation, wrong return value, wrong payload shape, or missed user intent.",
            'Return JSON only with one of these shapes:',
            '{"status":"pass","message":"Short reason."}',
            '{"status":"fail","failure_class":"semantic_mismatch","message":"Short reason.","repairable":true,"ambiguous":false,"suggestion":"One repair instruction."}',
            "Generation prompt:",
            prompt,
            "Candidate:",
            candidate,
            "Existing validator reports:",
            validator_summary,
        ]
    )


def parse_semantic_critic_response(raw_response: str) -> ValidatorReport:
    cleaned = _THINK_BLOCK_PATTERN.sub("\n", raw_response or "")
    cleaned = _CONTROL_TOKEN_PATTERN.sub("", cleaned)
    fence_match = _CODE_FENCE_PATTERN.search(cleaned)
    if fence_match is not None:
        cleaned = fence_match.group(1).strip()

    object_start = cleaned.find("{")
    object_end = cleaned.rfind("}")
    if object_start == -1 or object_end == -1 or object_end < object_start:
        return ValidatorReport(
            validator="semantic_validator",
            status="skipped",
            skipped_reason="semantic_critic_invalid_response",
        )

    try:
        payload = json.loads(cleaned[object_start : object_end + 1])
    except json.JSONDecodeError:
        return ValidatorReport(
            validator="semantic_validator",
            status="skipped",
            skipped_reason="semantic_critic_invalid_response",
        )

    status = str(payload.get("status", "")).strip().lower()
    if status == "pass":
        return ValidatorReport(validator="semantic_validator", status="pass")

    if status != "fail":
        return ValidatorReport(
            validator="semantic_validator",
            status="skipped",
            skipped_reason="semantic_critic_invalid_response",
        )

    finding = ValidationFinding(
        validator="semantic_validator",
        failure_class=str(payload.get("failure_class") or "semantic_mismatch"),
        message=str(payload.get("message") or "Candidate does not satisfy the task semantics."),
        location="response",
        repairable=bool(payload.get("repairable", True)),
        ambiguous=bool(payload.get("ambiguous", False)),
        suggestion=(
            str(payload.get("suggestion"))
            if payload.get("suggestion") is not None
            else str(payload.get("repair_prompt"))
            if payload.get("repair_prompt") is not None
            else None
        ),
    )
    return ValidatorReport(
        validator="semantic_validator",
        status="fail",
        findings=(finding,),
    )
