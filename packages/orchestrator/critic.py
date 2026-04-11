from __future__ import annotations

from packages.shared.quality import ValidationFinding, ValidatorReport


def build_critic_report(
    format_report: ValidatorReport,
    rule_report: ValidatorReport,
    *,
    output_mode: str,
    repair_count: int,
    clarification_count: int,
    repeated_failure_class: bool,
) -> dict[str, object]:
    findings = [*format_report.findings, *rule_report.findings]
    if not findings:
        return {
            "action": "finalize",
            "failure_class": None,
            "message": "Validation passed.",
        }

    primary = findings[0]

    if primary.ambiguous and clarification_count < 1:
        return {
            "action": "clarification",
            "failure_class": primary.failure_class,
            "message": primary.message,
            "clarification_question": primary.suggestion or "What should be clarified before the next attempt?",
        }

    if repair_count >= 2 or repeated_failure_class:
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
