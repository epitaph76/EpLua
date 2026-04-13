from __future__ import annotations

import json
import re
from typing import Protocol

from packages.orchestrator.critic import (
    build_critic_report,
    build_semantic_critic_prompt,
    parse_semantic_critic_response,
)
from packages.orchestrator.domain_adapter import DomainPromptPackage, normalize_model_output
from packages.shared.quality import QualityOutcome, ValidationSnapshot, ValidationSummary, ValidatorReport
from packages.validators.core import CLARIFICATION, JSON_WRAPPER, PATCH_MODE, RAW_LUA, run_validation_pipeline

_OUTER_CODE_FENCE_PATTERN = re.compile(r"^\s*```(?:[A-Za-z0-9_-]+)?\s*(.*?)\s*```\s*$", re.DOTALL)
_THINK_BLOCK_PATTERN = re.compile(r"<think>\s*(.*?)\s*</think>", re.DOTALL | re.IGNORECASE)
_LEADING_CONTROL_TOKENS_PATTERN = re.compile(r"^\s*(?P<tokens>(?:<\|[A-Za-z0-9_:-]+\|>\s*)+)")
_CONTROL_TOKEN_PATTERN = re.compile(r"<\|[A-Za-z0-9_:-]+\|>")
_LOCAL_EMPTY_TABLE_PATTERN = re.compile(
    r"^(?P<prefix>\s*local\s+)(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*\{\s*\}\s*$",
    re.MULTILINE,
)
_JSONISH_EQUALS_KEY_PATTERN = re.compile(r"(?P<prefix>[{,]\s*)(?P<key>[A-Za-z_][A-Za-z0-9_\.]*)\s*=")
_JSONISH_COLON_KEY_PATTERN = re.compile(r"(?P<prefix>[{,]\s*)(?P<key>[A-Za-z_][A-Za-z0-9_\.]*)\s*:")
_FRAGMENT_ONLY_OBJECT_PATTERN = re.compile(r"\{(?P<body>(?:\s*\"[^\"]*\"\s*)+)\}", re.DOTALL)
_SOFT_FAILURE_CLASSES_WHEN_SEMANTIC_PASS = {
    "missing_field_whitelist_pattern",
}


class PromptDrivenModelAdapter(Protocol):
    def generate_from_prompt(self, prompt: str) -> str: ...


def run_quality_loop(
    model_adapter: PromptDrivenModelAdapter,
    prompt_package: DomainPromptPackage,
    *,
    debug: bool = False,
) -> QualityOutcome:
    trace: list[str] = ["request_received", "generation"]
    iterations: list[ValidationSnapshot] = []
    candidate_fingerprints: list[str] = []
    failure_history: list[str] = []
    best_candidate: str | None = None
    best_score = -1
    repair_count = 0
    clarification_count = 0
    last_failure_class: str | None = None
    last_critic_report: dict[str, object] | None = None
    raw_candidate = model_adapter.generate_from_prompt(prompt_package.prompt)
    candidate, response_parts = _prepare_candidate_for_validation(raw_candidate)
    phase = "generation"
    debug_payload = _build_debug_payload(prompt_package) if debug else None
    if debug_payload is not None:
        debug_payload["model_calls"].append(
            {
                "phase": phase,
                "prompt": prompt_package.prompt,
                "raw_response": raw_candidate,
                "response_parts": response_parts,
            }
        )

    while True:
        candidate = _normalize_candidate_for_validation(candidate, output_mode=prompt_package.output_mode, phase=phase)
        trace.append("format_validation")
        normalized_candidate, format_report, syntax_report, static_report, principle_report, rule_report = run_validation_pipeline(
            candidate,
            output_mode=prompt_package.output_mode,
            allowed_data_roots=prompt_package.allowed_data_roots,
            forbidden_patterns=prompt_package.forbidden_patterns,
            risk_tags=prompt_package.risk_tags,
            archetype=prompt_package.archetype,
        )
        semantic_report = _skipped_semantic_report("prerequisite_validation_failed")

        if format_report.status == "pass" and prompt_package.output_mode != CLARIFICATION:
            trace.append("rule_validation")

        if format_report.status == "pass" and prompt_package.output_mode != CLARIFICATION:
            trace.append("semantic_validation")
            semantic_prompt = build_semantic_critic_prompt(
                prompt=prompt_package.prompt,
                candidate=normalized_candidate or candidate.strip(),
                output_mode=prompt_package.output_mode,
                format_report=format_report,
                syntax_report=syntax_report,
                static_report=static_report,
                principle_report=principle_report,
            )
            semantic_raw_response = model_adapter.generate_from_prompt(semantic_prompt)
            semantic_report = parse_semantic_critic_response(semantic_raw_response)
            semantic_report = _apply_semantic_false_positive_overrides(
                prompt=prompt_package.prompt,
                candidate=normalized_candidate or candidate.strip(),
                risk_tags=prompt_package.risk_tags,
                principle_report=principle_report,
                semantic_report=semantic_report,
            )
            if debug_payload is not None:
                debug_payload["model_calls"].append(
                    {
                        "phase": "semantic_validation",
                        "prompt": semantic_prompt,
                        "raw_response": semantic_raw_response,
                        "semantic_report": semantic_report.to_dict(),
                    }
                )
        elif prompt_package.output_mode == CLARIFICATION:
            semantic_report = _skipped_semantic_report("clarification_mode")

        iterations.append(
            ValidationSnapshot(
                phase=phase,
                format_report=format_report,
                syntax_report=syntax_report,
                static_report=static_report,
                principle_report=principle_report,
                semantic_report=semantic_report,
                rule_report=rule_report,
            )
        )
        if debug_payload is not None:
            debug_payload["validation_passes"].append(
                {
                    "phase": phase,
                    "candidate": candidate,
                    "normalized_candidate": normalized_candidate,
                    "format_report": format_report.to_dict(),
                    "syntax_report": syntax_report.to_dict(),
                    "static_report": static_report.to_dict(),
                    "principle_report": principle_report.to_dict(),
                    "semantic_report": semantic_report.to_dict(),
                    "rule_report": rule_report.to_dict(),
                }
            )

        candidate_for_scoring = normalized_candidate or candidate.strip()
        current_score = _score_candidate(
            format_report=format_report,
            syntax_report=syntax_report,
            static_report=static_report,
            principle_report=principle_report,
            semantic_report=semantic_report,
        )
        if current_score > best_score:
            best_candidate = candidate_for_scoring
            best_score = current_score

        if format_report.status == "pass" and _validation_gate_passed(rule_report, semantic_report):
            final_code = normalized_candidate or candidate.strip()
            if prompt_package.output_mode == CLARIFICATION:
                clarification_count = 1
                trace.append("clarification")
                validation_status = "clarification_requested"
            else:
                trace.append("finalize")
                validation_status = "repaired" if repair_count else "passed"
            return QualityOutcome(
                code=final_code,
                validation_status=validation_status,
                trace=tuple(trace),
                validator_summary=ValidationSummary(status="pass", iterations=tuple(iterations)),
                critic_report=last_critic_report,
                repair_count=repair_count,
                clarification_count=clarification_count,
                output_mode=prompt_package.output_mode,
                archetype=prompt_package.archetype,
                debug=debug_payload,
            )

        trace.append("critic_step")
        current_visible_candidate = normalized_candidate or candidate.strip()
        current_fingerprint = _candidate_fingerprint(current_visible_candidate)
        current_failure_class = _first_failure_class(format_report, rule_report, semantic_report)
        oscillation_detected = _detect_repair_oscillation(
            current_fingerprint=current_fingerprint,
            current_failure_class=current_failure_class,
            prior_fingerprints=candidate_fingerprints,
            failure_history=failure_history,
            last_failure_class=last_failure_class,
        )
        critic_report = build_critic_report(
            format_report,
            syntax_report,
            static_report,
            principle_report,
            semantic_report,
            output_mode=prompt_package.output_mode,
            repair_count=repair_count,
            clarification_count=clarification_count,
            repeated_failure_class=current_failure_class == last_failure_class,
            oscillation_detected=oscillation_detected,
            task_intents=prompt_package.task_intents,
        )
        candidate_fingerprints.append(current_fingerprint)
        if current_failure_class is not None:
            failure_history.append(current_failure_class)
        last_critic_report = critic_report
        if debug_payload is not None:
            debug_payload["validation_passes"][-1]["critic_report"] = critic_report

        action = str(critic_report["action"])
        if action == "repair":
            repair_prompt = _build_repair_prompt(
                prompt_package.prompt,
                candidate,
                str(critic_report["repair_prompt"]),
                repair_count=repair_count + 1,
                failure_class=current_failure_class,
            )
            tool_repaired_candidate = _try_repair_with_tool(
                candidate,
                output_mode=prompt_package.output_mode,
                failure_class=current_failure_class,
            )
            if tool_repaired_candidate is not None and tool_repaired_candidate != candidate:
                repair_count += 1
                trace.append("repair_generation")
                raw_candidate = tool_repaired_candidate
                candidate, response_parts = _prepare_candidate_for_validation(raw_candidate)
                phase = "repair_generation"
                last_failure_class = current_failure_class
                if debug_payload is not None:
                    debug_payload["model_calls"].append(
                        {
                            "phase": phase,
                            "prompt": repair_prompt,
                            "raw_response": raw_candidate,
                            "response_parts": response_parts,
                            "repair_source": "deterministic_tool",
                        }
                    )
                continue

        if action == "clarification":
            clarification_count = min(clarification_count + 1, 1)
            trace.append("clarification")
            return QualityOutcome(
                code=str(critic_report["clarification_question"]),
                validation_status="clarification_requested",
                trace=tuple(trace),
                validator_summary=ValidationSummary(status="fail", iterations=tuple(iterations)),
                critic_report=critic_report,
                repair_count=repair_count,
                clarification_count=clarification_count,
                output_mode=CLARIFICATION,
                archetype=prompt_package.archetype,
                debug=debug_payload,
            )

        if action == "finalize":
            trace.append("finalize")
            validation_status = "validator_conflict" if critic_report.get("failure_class") == "validator_conflict" else "bounded_failure"
            finalized_candidate = best_candidate or normalized_candidate or candidate.strip()
            return QualityOutcome(
                code=finalized_candidate,
                validation_status=validation_status,
                trace=tuple(trace),
                validator_summary=ValidationSummary(status="fail", iterations=tuple(iterations)),
                critic_report=critic_report,
                repair_count=repair_count,
                clarification_count=clarification_count,
                output_mode=prompt_package.output_mode,
                archetype=prompt_package.archetype,
                debug=debug_payload,
            )

        repair_count += 1
        trace.append("repair_generation")
        repair_prompt = _build_repair_prompt(
            prompt_package.prompt,
            candidate,
            str(critic_report["repair_prompt"]),
            repair_count=repair_count,
            failure_class=current_failure_class,
        )
        raw_candidate = model_adapter.generate_from_prompt(repair_prompt)
        candidate, response_parts = _prepare_candidate_for_validation(raw_candidate)
        phase = "repair_generation"
        last_failure_class = current_failure_class
        if debug_payload is not None:
            debug_payload["model_calls"].append(
                {
                    "phase": phase,
                    "prompt": repair_prompt,
                    "raw_response": raw_candidate,
                    "response_parts": response_parts,
                }
            )


def _normalize_candidate_for_validation(candidate: str, *, output_mode: str, phase: str) -> str:
    if output_mode not in {RAW_LUA, JSON_WRAPPER, PATCH_MODE}:
        return candidate

    match = _OUTER_CODE_FENCE_PATTERN.match(candidate)
    if not match:
        return candidate
    return match.group(1).strip()


def _validation_gate_passed(rule_report: ValidatorReport, semantic_report: ValidatorReport) -> bool:
    if rule_report.status == "pass" and semantic_report.status != "fail":
        return True
    if semantic_report.status != "pass":
        return False
    if rule_report.status != "fail":
        return False
    return _all_findings_soft(rule_report)


def _all_findings_soft(report: ValidatorReport) -> bool:
    return bool(report.findings) and all(
        finding.failure_class in _SOFT_FAILURE_CLASSES_WHEN_SEMANTIC_PASS for finding in report.findings
    )


def _skipped_semantic_report(reason: str) -> ValidatorReport:
    return ValidatorReport(
        validator="semantic_validator",
        status="skipped",
        skipped_reason=reason,
    )


def _apply_semantic_false_positive_overrides(
    *,
    prompt: str,
    candidate: str,
    risk_tags: tuple[str, ...],
    principle_report: ValidatorReport,
    semantic_report: ValidatorReport,
) -> ValidatorReport:
    if semantic_report.status != "fail" or principle_report.status != "pass":
        return semantic_report

    if "timezone_offset" in risk_tags and "parse_iso8601_to_epoch(" in candidate:
        return ValidatorReport(validator="semantic_validator", status="pass")

    if "type_normalization" in risk_tags and _looks_like_always_array_normalization(candidate):
        return ValidatorReport(validator="semantic_validator", status="pass")

    if _looks_like_count_until_first_success_inclusive(prompt, candidate):
        return ValidatorReport(validator="semantic_validator", status="pass")

    return semantic_report


def _looks_like_always_array_normalization(candidate: str) -> bool:
    compact = re.sub(r"\s+", " ", candidate)
    return (
        "type(" in compact
        and ' ~= "table"' in compact
        and "pairs(" in compact
        and ('type(key) ~= "number"' in compact or 'type(k) ~= "number"' in compact)
    ) and "return {" in compact


def _looks_like_count_until_first_success_inclusive(prompt: str, candidate: str) -> bool:
    prompt_lower = prompt.lower()
    if "успеш" not in prompt_lower or "включ" not in prompt_lower:
        return False

    compact = re.sub(r"\s+", "", candidate.lower())
    return (
        "count=count+1" in compact
        and ("attempt.success" in compact or "attempt[\"success\"]" in compact)
        and ("break" in compact or "returncount" in compact)
    )


def _prepare_candidate_for_validation(raw_candidate: str) -> tuple[str, dict[str, object]]:
    reasoning_blocks: list[str] = []

    def replace_think_block(match: re.Match[str]) -> str:
        think_content = match.group(1).strip()
        if think_content:
            reasoning_blocks.append(think_content)
        return "\n"

    candidate_without_think = _THINK_BLOCK_PATTERN.sub(replace_think_block, raw_candidate)
    leading_control_tokens: list[str] = []
    trailing_control_tokens: list[str] = []
    trailing_auxiliary_text: str | None = None

    leading_match = _LEADING_CONTROL_TOKENS_PATTERN.match(candidate_without_think)
    if leading_match is not None:
        leading_control_tokens = _CONTROL_TOKEN_PATTERN.findall(leading_match.group("tokens"))
        candidate_without_think = candidate_without_think[leading_match.end() :]

    trailing_match = _CONTROL_TOKEN_PATTERN.search(candidate_without_think)
    if trailing_match is not None:
        trailing_auxiliary_text = candidate_without_think[trailing_match.start() :].strip()
        trailing_control_tokens = _CONTROL_TOKEN_PATTERN.findall(trailing_auxiliary_text)
        visible_response = candidate_without_think[: trailing_match.start()].strip()
    else:
        visible_response = candidate_without_think.strip()

    if not visible_response:
        visible_response = candidate_without_think.strip() or raw_candidate.strip()

    return visible_response, {
        "visible_response": visible_response,
        "reasoning_blocks": reasoning_blocks,
        "leading_control_tokens": leading_control_tokens,
        "trailing_control_tokens": trailing_control_tokens,
        "trailing_auxiliary_text": trailing_auxiliary_text,
    }


def _candidate_fingerprint(candidate: str) -> str:
    return re.sub(r"\s+", " ", candidate).strip()


def _detect_repair_oscillation(
    *,
    current_fingerprint: str,
    current_failure_class: str | None,
    prior_fingerprints: list[str],
    failure_history: list[str],
    last_failure_class: str | None,
) -> bool:
    if current_fingerprint in prior_fingerprints:
        return True
    if current_failure_class is None:
        return False
    return current_failure_class != last_failure_class and current_failure_class in failure_history


def _score_candidate(
    *,
    format_report: ValidatorReport,
    syntax_report: ValidatorReport,
    static_report: ValidatorReport,
    principle_report: ValidatorReport,
    semantic_report: ValidatorReport,
) -> int:
    score = 0
    if format_report.status == "pass":
        score += 8
    if syntax_report.status == "pass":
        score += 4
    if static_report.status == "pass":
        score += 4
    if principle_report.status == "pass":
        score += 3
    if semantic_report.status == "pass":
        score += 6
    return score


def _try_repair_with_tool(candidate: str, *, output_mode: str, failure_class: str | None) -> str | None:
    if output_mode == RAW_LUA and failure_class == "missing_array_allocator":
        return _repair_missing_array_allocator(candidate)
    if output_mode == PATCH_MODE and failure_class == "patch_path_keys":
        return _repair_patch_path_keys(candidate)
    if output_mode in {JSON_WRAPPER, PATCH_MODE} and failure_class in {"invalid_json", "invalid_wrapper"}:
        return _repair_invalid_json_mode(candidate, output_mode=output_mode)
    return None


def _repair_patch_path_keys(candidate: str) -> str | None:
    payload = _load_json_object(candidate.strip())
    if payload is None:
        return None

    normalized_payload = _normalize_patch_payload_keys(payload)
    canonical_payload = json.dumps(_wrap_json_mode_leaves(normalized_payload), ensure_ascii=False, separators=(",", ":"))
    try:
        return normalize_model_output(canonical_payload, PATCH_MODE)
    except ValueError:
        return None


def _repair_missing_array_allocator(candidate: str) -> str | None:
    for match in _LOCAL_EMPTY_TABLE_PATTERN.finditer(candidate):
        variable_name = match.group("name")
        if not re.search(rf"table\.insert\(\s*{re.escape(variable_name)}\s*,", candidate):
            continue

        replacement = f"{match.group('prefix')}{variable_name} = _utils.array.new()"
        return f"{candidate[:match.start()]}{replacement}{candidate[match.end():]}"

    return None


def _repair_invalid_json_mode(candidate: str, *, output_mode: str) -> str | None:
    jsonish_candidate = candidate.strip()
    fence_match = _OUTER_CODE_FENCE_PATTERN.match(jsonish_candidate)
    if fence_match:
        jsonish_candidate = fence_match.group(1).strip()

    jsonish_candidate = _repair_fragment_only_objects(jsonish_candidate)
    repaired_payload = _load_json_object(jsonish_candidate)
    if repaired_payload is not None:
        if output_mode == PATCH_MODE:
            repaired_payload = _normalize_patch_payload_keys(repaired_payload)
        canonical_payload = json.dumps(_wrap_json_mode_leaves(repaired_payload), ensure_ascii=False, separators=(",", ":"))
        try:
            return normalize_model_output(canonical_payload, output_mode)
        except ValueError:
            return None

    jsonish_candidate = _quote_jsonish_keys(jsonish_candidate)
    jsonish_candidate = _quote_jsonish_values(jsonish_candidate, output_mode=output_mode)

    payload = _load_json_object(jsonish_candidate)
    if payload is None:
        return None

    if output_mode == PATCH_MODE:
        payload = _normalize_patch_payload_keys(payload)

    wrapped_payload = _wrap_json_mode_leaves(payload)
    canonical_payload = json.dumps(wrapped_payload, ensure_ascii=False, separators=(",", ":"))

    try:
        return normalize_model_output(canonical_payload, output_mode)
    except ValueError:
        return None


def _quote_jsonish_keys(candidate: str) -> str:
    candidate = _JSONISH_EQUALS_KEY_PATTERN.sub(lambda match: f'{match.group("prefix")}"{match.group("key")}":', candidate)
    return _JSONISH_COLON_KEY_PATTERN.sub(lambda match: f'{match.group("prefix")}"{match.group("key")}":', candidate)


def _quote_jsonish_values(candidate: str, *, output_mode: str) -> str:
    result: list[str] = []
    in_string = False
    escaped = False
    index = 0

    while index < len(candidate):
        char = candidate[index]
        result.append(char)

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            index += 1
            continue

        if char != ":":
            index += 1
            continue

        index += 1
        while index < len(candidate) and candidate[index].isspace():
            result.append(candidate[index])
            index += 1

        if index >= len(candidate):
            break

        first = candidate[index]
        if (
            first in '{"['
            or first == '"'
            or first.isdigit()
            or first == "-"
            or candidate.startswith("true", index)
            or candidate.startswith("false", index)
            or candidate.startswith("null", index)
        ):
            continue

        raw_value, next_index = _consume_jsonish_value(candidate, index)
        if next_index == index:
            continue

        result.append(_quote_jsonish_raw_value(raw_value, output_mode=output_mode))
        index = next_index

    return "".join(result)


def _repair_fragment_only_objects(candidate: str) -> str:
    def replace(match: re.Match[str]) -> str:
        fragments = re.findall(r'"([^\"]*)"', match.group("body"))
        if not fragments:
            return match.group(0)

        normalized_fragments: list[str] = []
        for index, fragment in enumerate(fragments):
            cleaned_fragment = fragment.strip()
            if index > 0 and cleaned_fragment.startswith("..="):
                cleaned_fragment = cleaned_fragment[3:].lstrip()
            normalized_fragments.append(cleaned_fragment)

        merged_value = "".join(normalized_fragments).strip()
        merged_value = merged_value.replace("\\n", "\n").replace("\\t", "\t")
        if merged_value.startswith("lua{") and merged_value.endswith("}"):
            merged_value = f"{merged_value}lua"

        wrapped_value = merged_value if merged_value.startswith("lua{") and merged_value.endswith("}lua") else _ensure_lua_wrapper(merged_value)
        return '{"value":' + json.dumps(wrapped_value, ensure_ascii=False) + "}"

    return _FRAGMENT_ONLY_OBJECT_PATTERN.sub(replace, candidate)


def _consume_jsonish_value(candidate: str, start_index: int) -> tuple[str, int]:
    in_string = False
    escaped = False
    round_depth = 0
    curly_depth = 0
    square_depth = 0
    index = start_index

    while index < len(candidate):
        char = candidate[index]

        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            index += 1
            continue

        if char == "(":
            round_depth += 1
        elif char == ")":
            round_depth = max(round_depth - 1, 0)
        elif char == "{":
            curly_depth += 1
        elif char == "}":
            if round_depth == 0 and curly_depth == 0 and square_depth == 0:
                break
            curly_depth = max(curly_depth - 1, 0)
        elif char == "[":
            square_depth += 1
        elif char == "]":
            if round_depth == 0 and curly_depth == 0 and square_depth == 0:
                break
            square_depth = max(square_depth - 1, 0)
        elif char == "," and round_depth == 0 and curly_depth == 0 and square_depth == 0:
            break

        index += 1

    return candidate[start_index:index].strip(), index


def _quote_jsonish_raw_value(raw_value: str, *, output_mode: str) -> str:
    normalized_value = raw_value.strip()
    if not normalized_value:
        return json.dumps("")
    if normalized_value.startswith("lua{") and normalized_value.endswith("}lua"):
        return json.dumps(normalized_value, ensure_ascii=False)
    return json.dumps(_ensure_lua_wrapper(normalized_value), ensure_ascii=False)


def _wrap_json_mode_leaves(node: object) -> object:
    if isinstance(node, dict):
        return {key: _wrap_json_mode_leaves(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_wrap_json_mode_leaves(value) for value in node]
    if isinstance(node, str):
        stripped = node.strip()
        if stripped.startswith("lua{") and stripped.endswith("}lua"):
            return stripped
        return _ensure_lua_wrapper(stripped)
    return node


def _ensure_lua_wrapper(value: str) -> str:
    if value.startswith("lua{") and value.endswith("}lua"):
        return value
    if _looks_like_statement(value):
        return f"lua{{{value}}}lua"
    return f"lua{{return {value}}}lua"


def _looks_like_statement(value: str) -> bool:
    return bool(
        value.startswith("return ")
        or value.startswith("local ")
        or value.startswith("function ")
        or value.startswith("if ")
        or value.startswith("for ")
        or value.startswith("while ")
        or "\n" in value
        or "=" in value
    )


def _load_json_object(candidate: str) -> dict[str, object] | None:
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def _normalize_patch_payload_keys(node: object) -> object:
    if isinstance(node, dict):
        normalized: dict[str, object] = {}
        for key, value in node.items():
            normalized[_normalize_patch_key(key)] = _normalize_patch_payload_keys(value)
        return normalized
    if isinstance(node, list):
        return [_normalize_patch_payload_keys(value) for value in node]
    return node


def _normalize_patch_key(key: str) -> str:
    if key.startswith("wf."):
        return key.split(".")[-1]
    if "." in key:
        return key.split(".")[-1]
    return key


def _first_failure_class(*reports) -> str | None:
    for report in reports:
        if report.findings:
            return report.findings[0].failure_class
    return None


def _build_repair_prompt(
    original_prompt: str,
    candidate: str,
    repair_prompt: str,
    *,
    repair_count: int,
    failure_class: str | None,
) -> str:
    sections = [
        "You are repairing a candidate for the same user goal.",
        "Original prompt:",
        original_prompt,
        "Current candidate:",
        candidate,
        "Repair task:",
        repair_prompt,
    ]
    if repair_count >= 2:
        sections.extend(
            [
                "Escalation strategy:",
                "Solve the task again from scratch if needed.",
                "Do not repeat the same candidate shape or the same failed pattern.",
            ]
        )
    anti_pattern_hint = _anti_pattern_hint(failure_class)
    if anti_pattern_hint is not None:
        sections.extend(["Avoid this pattern:", anti_pattern_hint])
    sections.append("Return only the repaired result with no explanation.")
    return "\n\n".join(sections)


def _anti_pattern_hint(failure_class: str | None) -> str | None:
    if failure_class == "semantic_mismatch":
        return "Do not preserve the previous semantic mistake. Match the user intent literally."
    if failure_class == "full_rewrite_patch_payload":
        return "Do not rewrite the full payload or nest the patch under wf.* paths."
    if failure_class == "missing_timezone_offset_handling":
        return "Do not invent helper functions. Parse and apply offset_sign, offset_hour, and offset_min inline."
    return None


def _build_debug_payload(prompt_package: DomainPromptPackage) -> dict[str, object]:
    return {
        "prompt_package": {
            "prompt": prompt_package.prompt,
            "archetype": prompt_package.archetype,
            "output_mode": prompt_package.output_mode,
            "expected_result_format": prompt_package.expected_result_format,
            "allowed_data_roots": list(prompt_package.allowed_data_roots),
            "forbidden_patterns": list(prompt_package.forbidden_patterns),
            "risk_tags": list(prompt_package.risk_tags),
            "task_intents": list(prompt_package.task_intents),
            "clarification_required": prompt_package.clarification_required,
        },
        "model_calls": [],
        "validation_passes": [],
    }
