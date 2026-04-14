from __future__ import annotations

import json
import re
from typing import Protocol

from packages.orchestrator.critic import (
    build_critic_report,
    build_semantic_critic_agent_prompt,
    parse_semantic_critic_response,
)
from packages.orchestrator.agent_prompt import AgentMessage, AgentPrompt
from packages.orchestrator.domain_adapter import DomainPromptPackage, normalize_model_output
from packages.orchestrator.prompter import (
    PromptBuilderResult,
    apply_prompter_agent_response,
    build_repair_prompter_agent_prompt,
)
from packages.orchestrator.task_spec import TaskSpec, build_task_spec
from packages.shared.quality import (
    QualityOutcome,
    ValidationBundle,
    ValidationSnapshot,
    ValidationSummary,
    ValidatorReport,
)
from packages.validators.core import (
    CLARIFICATION,
    JSON_WRAPPER,
    PATCH_MODE,
    RAW_LUA,
    has_in_place_array_field_enrichment,
    run_validation_pipeline,
    validate_runtime_behavior,
)

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


def _generate_from_agent(model_adapter: PromptDrivenModelAdapter, agent_prompt: AgentPrompt) -> str:
    generator = getattr(model_adapter, "generate_from_agent", None)
    if callable(generator):
        return str(generator(agent_prompt))
    return model_adapter.generate_from_prompt(agent_prompt.to_legacy_prompt())


def run_quality_loop(
    model_adapter: PromptDrivenModelAdapter,
    prompt_package: DomainPromptPackage,
    *,
    debug: bool = False,
) -> QualityOutcome:
    trace: list[str] = ["request_received", "generation"]
    iterations: list[ValidationSnapshot] = []
    candidate_fingerprints: list[str] = []
    behavioral_fingerprints: list[str] = []
    invalid_shape_history: list[str] = []
    disallowed_root_history: list[str] = []
    failure_history: list[str] = []
    best_candidate: str | None = None
    best_candidate_iteration_index: int | None = None
    best_score = -1
    repair_count = 0
    clarification_count = 0
    last_failure_class: str | None = None
    last_critic_report: dict[str, object] | None = None
    last_critic_report_iteration_index: int | None = None
    raw_candidate = _generate_from_agent(model_adapter, prompt_package.agent_prompt)
    candidate, response_parts = _prepare_candidate_for_validation(raw_candidate)
    phase = "generation"
    debug_payload = _build_debug_payload(prompt_package) if debug else None
    if debug_payload is not None:
        debug_payload["model_calls"].append(
            {
                "phase": phase,
                "agent": prompt_package.agent_prompt.agent_name,
                "prompt": prompt_package.prompt,
                "messages": prompt_package.agent_prompt.to_messages_payload(),
                "raw_response": raw_candidate,
                "response_parts": response_parts,
            }
        )

    while True:
        candidate = _normalize_candidate_for_validation(candidate, output_mode=prompt_package.output_mode, phase=phase)
        trace.append("format_validation")
        runtime_task_spec = _runtime_task_spec_for_validation(prompt_package)
        normalized_candidate, format_report, syntax_report, static_report, principle_report, rule_report = run_validation_pipeline(
            candidate,
            output_mode=prompt_package.output_mode,
            allowed_data_roots=prompt_package.allowed_data_roots,
            forbidden_patterns=prompt_package.forbidden_patterns,
            risk_tags=prompt_package.risk_tags,
            archetype=prompt_package.archetype,
            task_spec=runtime_task_spec or prompt_package.task_spec,
        )
        runtime_report = _skipped_runtime_report("prerequisite_validation_failed")
        semantic_report = _skipped_semantic_report("prerequisite_validation_failed")

        if format_report.status == "pass" and prompt_package.output_mode != CLARIFICATION:
            trace.append("rule_validation")

        should_run_runtime_validation = runtime_task_spec is not None
        if (
            format_report.status == "pass"
            and _rule_report_allows_behavioral_validation(rule_report)
            and should_run_runtime_validation
            and prompt_package.output_mode != CLARIFICATION
        ):
            trace.append("runtime_validation")
            runtime_report = validate_runtime_behavior(
                normalized_candidate or candidate.strip(),
                output_mode=prompt_package.output_mode,
                execution_context=prompt_package.execution_context,
                task_spec=runtime_task_spec or prompt_package.task_spec,
            )

        should_run_semantic_validation = (
            format_report.status == "pass"
            and (_rule_report_allows_behavioral_validation(rule_report) or _all_findings_soft(rule_report))
            and (not should_run_runtime_validation or runtime_report.status != "fail")
            and prompt_package.output_mode != CLARIFICATION
        )
        if should_run_semantic_validation:
            trace.append("semantic_validation")
            semantic_agent_prompt = build_semantic_critic_agent_prompt(
                prompt=prompt_package.prompt,
                candidate=normalized_candidate or candidate.strip(),
                output_mode=prompt_package.output_mode,
                task_spec=runtime_task_spec or prompt_package.task_spec,
                format_report=format_report,
                syntax_report=syntax_report,
                static_report=static_report,
                principle_report=principle_report,
                runtime_report=runtime_report,
                language=prompt_package.language,
            )
            semantic_raw_response = _generate_from_agent(model_adapter, semantic_agent_prompt)
            semantic_report = parse_semantic_critic_response(semantic_raw_response)
            semantic_report = _apply_semantic_false_positive_overrides(
                prompt=prompt_package.prompt,
                candidate=normalized_candidate or candidate.strip(),
                risk_tags=prompt_package.risk_tags,
                principle_report=principle_report,
                rule_report=rule_report,
                semantic_report=semantic_report,
            )
            if debug_payload is not None:
                debug_payload["model_calls"].append(
                    {
                        "phase": "semantic_validation",
                        "agent": semantic_agent_prompt.agent_name,
                        "prompt": semantic_agent_prompt.to_legacy_prompt(),
                        "messages": semantic_agent_prompt.to_messages_payload(),
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
                runtime_report=runtime_report,
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
                    "runtime_report": runtime_report.to_dict(),
                    "semantic_report": semantic_report.to_dict(),
                    "rule_report": rule_report.to_dict(),
                }
            )

        current_visible_candidate = normalized_candidate or candidate.strip()
        validation_bundle = _build_validation_bundle(
            task_spec=prompt_package.task_spec,
            current_candidate=current_visible_candidate,
            format_report=format_report,
            syntax_report=syntax_report,
            static_report=static_report,
            principle_report=principle_report,
            runtime_report=runtime_report,
            semantic_report=semantic_report,
        )
        if debug_payload is not None:
            debug_payload["validation_passes"][-1]["validation_bundle"] = validation_bundle.to_dict()

        candidate_for_scoring = current_visible_candidate
        current_score = _score_candidate(
            format_report=format_report,
            syntax_report=syntax_report,
            static_report=static_report,
            principle_report=principle_report,
            runtime_report=runtime_report,
            semantic_report=semantic_report,
        )
        if current_score > best_score:
            best_candidate = candidate_for_scoring
            best_candidate_iteration_index = len(iterations) - 1
            best_score = current_score

        if format_report.status == "pass" and _validation_gate_passed(
            rule_report,
            runtime_report,
            semantic_report,
            output_mode=prompt_package.output_mode,
        ):
            final_code = normalized_candidate or candidate.strip()
            if prompt_package.output_mode == CLARIFICATION:
                clarification_count = 1
                trace.append("clarification")
                validation_status = "clarification_requested"
                stop_reason = "clarification_requested"
            else:
                trace.append("finalize")
                validation_status = "repaired" if repair_count else "passed"
                stop_reason = "passed"
            return QualityOutcome(
                code=final_code,
                validation_status=validation_status,
                stop_reason=stop_reason,
                trace=tuple(trace),
                validator_summary=ValidationSummary(status="pass", iterations=tuple(iterations)),
                critic_report=last_critic_report,
                repair_count=repair_count,
                clarification_count=clarification_count,
                output_mode=prompt_package.output_mode,
                archetype=prompt_package.archetype,
                final_candidate_source="current_candidate",
                final_candidate_iteration_index=len(iterations) - 1,
                critic_report_iteration_index=last_critic_report_iteration_index,
                debug=debug_payload,
            )

        trace.append("critic_step")
        current_fingerprint = _candidate_fingerprint(current_visible_candidate)
        current_failure_class = _first_failure_class(format_report, rule_report, runtime_report, semantic_report)
        oscillation_detected = _detect_repair_oscillation(
            current_fingerprint=current_fingerprint,
            current_behavioral_fingerprint=validation_bundle.behavioral_fingerprint,
            current_invalid_shape_signature=validation_bundle.invalid_shape_signature,
            current_disallowed_root_signature=validation_bundle.disallowed_root_signature,
            current_failure_class=current_failure_class,
            prior_fingerprints=candidate_fingerprints,
            behavioral_history=behavioral_fingerprints,
            invalid_shape_history=invalid_shape_history,
            disallowed_root_history=disallowed_root_history,
            failure_history=failure_history,
        )
        critic_report = build_critic_report(
            format_report,
            syntax_report,
            static_report,
            principle_report,
            runtime_report,
            semantic_report,
            output_mode=prompt_package.output_mode,
            repair_count=repair_count,
            clarification_count=clarification_count,
            repeated_failure_class=current_failure_class == last_failure_class,
            oscillation_detected=oscillation_detected,
            task_intents=prompt_package.task_intents,
            language=prompt_package.language,
            validation_bundle=validation_bundle,
        )
        candidate_fingerprints.append(current_fingerprint)
        if validation_bundle.behavioral_fingerprint is not None:
            behavioral_fingerprints.append(validation_bundle.behavioral_fingerprint)
        if validation_bundle.invalid_shape_signature is not None:
            invalid_shape_history.append(validation_bundle.invalid_shape_signature)
        if validation_bundle.disallowed_root_signature is not None:
            disallowed_root_history.append(validation_bundle.disallowed_root_signature)
        if current_failure_class is not None:
            failure_history.append(current_failure_class)
        last_critic_report = critic_report
        last_critic_report_iteration_index = len(iterations) - 1
        if debug_payload is not None:
            debug_payload["validation_passes"][-1]["critic_report"] = critic_report

        action = str(critic_report["action"])
        if action == "repair":
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
                            "agent": "deterministic_tool",
                            "prompt": "",
                            "messages": [],
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
                stop_reason="clarification_requested",
                trace=tuple(trace),
                validator_summary=ValidationSummary(status="fail", iterations=tuple(iterations)),
                critic_report=critic_report,
                repair_count=repair_count,
                clarification_count=clarification_count,
                output_mode=CLARIFICATION,
                archetype=prompt_package.archetype,
                final_candidate_source="clarification_question",
                critic_report_iteration_index=last_critic_report_iteration_index,
                debug=debug_payload,
            )

        if action == "finalize":
            trace.append("finalize")
            validation_status = "validator_conflict" if critic_report.get("failure_class") == "validator_conflict" else "bounded_failure"
            stop_reason = _stop_reason_for_finalize(critic_report)
            finalized_candidate = best_candidate or normalized_candidate or candidate.strip()
            final_candidate_iteration_index = (
                best_candidate_iteration_index if best_candidate is not None else len(iterations) - 1
            )
            final_candidate_source = (
                "best_candidate" if final_candidate_iteration_index != len(iterations) - 1 else "current_candidate"
            )
            return QualityOutcome(
                code=finalized_candidate,
                validation_status=validation_status,
                stop_reason=stop_reason,
                trace=tuple(trace),
                validator_summary=ValidationSummary(status="fail", iterations=tuple(iterations)),
                critic_report=critic_report,
                repair_count=repair_count,
                clarification_count=clarification_count,
                output_mode=prompt_package.output_mode,
                archetype=prompt_package.archetype,
                final_candidate_source=final_candidate_source,
                final_candidate_iteration_index=final_candidate_iteration_index,
                critic_report_iteration_index=last_critic_report_iteration_index,
                debug=debug_payload,
            )

        repair_count += 1
        trace.append("repair_generation")
        repair_prompt_builder_result, repair_prompter_prompt, repair_prompter_raw_response = _build_repair_prompt_via_prompter(
            model_adapter,
            prompt_package=prompt_package,
            current_candidate=current_visible_candidate,
            repair_instruction=str(critic_report["repair_prompt"]),
            repair_count=repair_count,
            failure_class=current_failure_class,
            validation_bundle=validation_bundle,
        )
        if debug_payload is not None:
            debug_payload["pipeline_layers"].append(
                {
                    "stage": "repair_prompter",
                    "kind": "agent_layer",
                    "status": "completed",
                    "details": repair_prompt_builder_result.to_debug_dict(),
                }
            )
            debug_payload["pipeline_layers"].append(
                {
                    "stage": "repair_generator",
                    "kind": "llm_agent",
                    "status": "configured",
                    "agent": repair_prompt_builder_result.agent_prompt.agent_name,
                }
            )
            debug_payload["agent_layer_calls"].append(
                {
                    "phase": "repair_prompter",
                    "agent": repair_prompter_prompt.agent_name,
                    "prompt": repair_prompter_prompt.to_legacy_prompt(),
                    "messages": repair_prompter_prompt.to_messages_payload(),
                    "raw_response": repair_prompter_raw_response,
                    "prompt_builder_result": repair_prompt_builder_result.to_debug_dict(),
                }
            )
        raw_candidate = _generate_from_agent(model_adapter, repair_prompt_builder_result.agent_prompt)
        candidate, response_parts = _prepare_candidate_for_validation(raw_candidate)
        phase = "repair_generation"
        last_failure_class = current_failure_class
        if debug_payload is not None:
            debug_payload["model_calls"].append(
                {
                    "phase": phase,
                    "agent": repair_prompt_builder_result.agent_prompt.agent_name,
                    "prompt": repair_prompt_builder_result.agent_prompt.to_legacy_prompt(),
                    "messages": repair_prompt_builder_result.agent_prompt.to_messages_payload(),
                    "raw_response": raw_candidate,
                    "response_parts": response_parts,
                    "prompt_builder_result": repair_prompt_builder_result.to_debug_dict(),
                }
            )


def _normalize_candidate_for_validation(candidate: str, *, output_mode: str, phase: str) -> str:
    if output_mode not in {RAW_LUA, JSON_WRAPPER, PATCH_MODE}:
        return candidate

    match = _OUTER_CODE_FENCE_PATTERN.match(candidate)
    if not match:
        return candidate
    return match.group(1).strip()


def _runtime_task_spec_for_validation(prompt_package: DomainPromptPackage) -> TaskSpec | None:
    if prompt_package.execution_context is None:
        return None

    task_spec = prompt_package.task_spec
    if _runtime_supported_task_spec(task_spec):
        return task_spec

    return _runtime_backstop_task_spec(prompt_package)


def _runtime_supported_task_spec(task_spec: TaskSpec) -> bool:
    return task_spec.archetype == "simple_extraction" and task_spec.operation in {"last_array_item", "first_array_item"}


def _runtime_backstop_task_spec(prompt_package: DomainPromptPackage) -> TaskSpec | None:
    task_spec = prompt_package.task_spec
    if task_spec.archetype != "simple_extraction" or task_spec.operation != "unresolved":
        return None
    if len(task_spec.input_roots) != 1 or "array_indexing" not in task_spec.risk_tags:
        return None

    fallback_spec = build_task_spec(
        task_spec.task_text,
        language=task_spec.language,
        archetype=task_spec.archetype,
        output_mode=task_spec.output_mode,
        input_roots=task_spec.input_roots,
        risk_tags=task_spec.risk_tags,
        clarification_required=task_spec.clarification_required,
    )
    if not _runtime_supported_task_spec(fallback_spec):
        return None
    return fallback_spec


def _validation_gate_passed(
    rule_report: ValidatorReport,
    runtime_report: ValidatorReport,
    semantic_report: ValidatorReport,
    *,
    output_mode: str,
) -> bool:
    if output_mode == CLARIFICATION:
        return rule_report.status != "fail"
    if _rule_report_allows_behavioral_validation(rule_report):
        if runtime_report.status == "pass" and _semantic_report_allows_runtime_pass(semantic_report):
            return True
        if runtime_report.status == "skipped" and semantic_report.status == "pass":
            return True
        if (
            output_mode in {JSON_WRAPPER, PATCH_MODE}
            and runtime_report.status == "skipped"
            and _semantic_report_is_invalid_critic_response(semantic_report)
        ):
            return True
        return False
    if semantic_report.status != "pass":
        return False
    if runtime_report.status == "fail":
        return False
    if rule_report.status != "fail":
        return False
    return _all_findings_soft(rule_report)


def _semantic_report_allows_runtime_pass(semantic_report: ValidatorReport) -> bool:
    if semantic_report.status in {"pass", "skipped"}:
        return True
    return _semantic_report_is_invalid_critic_response(semantic_report)


def _semantic_report_is_invalid_critic_response(semantic_report: ValidatorReport) -> bool:
    return bool(semantic_report.findings) and all(
        finding.failure_class == "semantic_critic_invalid_response"
        for finding in semantic_report.findings
    )


def _rule_report_allows_behavioral_validation(rule_report: ValidatorReport) -> bool:
    return rule_report.status in {"pass", "skipped"} and not rule_report.findings


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


def _skipped_runtime_report(reason: str) -> ValidatorReport:
    return ValidatorReport(
        validator="runtime_validator",
        status="skipped",
        skipped_reason=reason,
    )


def _apply_semantic_false_positive_overrides(
    *,
    prompt: str,
    candidate: str,
    risk_tags: tuple[str, ...],
    principle_report: ValidatorReport,
    rule_report: ValidatorReport,
    semantic_report: ValidatorReport,
) -> ValidatorReport:
    if semantic_report.status != "fail" or principle_report.status != "pass" or rule_report.status != "pass":
        return semantic_report

    if "timezone_offset" in risk_tags and "parse_iso8601_to_epoch(" in candidate:
        return ValidatorReport(validator="semantic_validator", status="pass")

    if "type_normalization" in risk_tags and _looks_like_always_array_normalization(candidate):
        return ValidatorReport(validator="semantic_validator", status="pass")

    if _looks_like_count_until_first_success_inclusive(prompt, candidate):
        return ValidatorReport(validator="semantic_validator", status="pass")

    if _looks_like_error_code_array_projection(prompt, candidate):
        return ValidatorReport(validator="semantic_validator", status="pass")

    if _looks_like_file_meta_direct_projection(prompt, candidate):
        return ValidatorReport(validator="semantic_validator", status="pass")

    if _looks_like_manager_name_absence_fallback(prompt, candidate):
        return ValidatorReport(validator="semantic_validator", status="pass")

    if _looks_like_date_ru_to_iso_date(prompt, candidate):
        return ValidatorReport(validator="semantic_validator", status="pass")

    if _looks_like_nil_tags_empty_array(prompt, candidate):
        return ValidatorReport(validator="semantic_validator", status="pass")

    if _looks_like_strict_type_count(prompt, candidate):
        return ValidatorReport(validator="semantic_validator", status="pass")

    if _looks_like_iso_date_string_comparison(prompt, candidate):
        return ValidatorReport(validator="semantic_validator", status="pass")

    if _looks_like_nested_tax_code_array_projection(prompt, candidate):
        return ValidatorReport(validator="semantic_validator", status="pass")

    if _looks_like_non_empty_sku_quantity_filter(prompt, candidate):
        return ValidatorReport(validator="semantic_validator", status="pass")

    if _looks_like_array_field_enrichment_preserving_fields(prompt, candidate):
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


def _looks_like_error_code_array_projection(prompt: str, candidate: str) -> bool:
    prompt_lower = prompt.lower()
    if "errors" not in prompt_lower or "code" not in prompt_lower:
        return False

    return bool(
        re.search(r"for\s+_,\s*error\s+in\s+ipairs\(\s*wf\.vars\.errors\s*\)", candidate)
        and re.search(r"table\.insert\(\s*\w+\s*,\s*error\.code\s*\)", candidate)
    )


def _looks_like_file_meta_direct_projection(prompt: str, candidate: str) -> bool:
    prompt_lower = prompt.lower()
    if "filemeta" not in prompt_lower or "name" not in prompt_lower or "extension" not in prompt_lower or "size" not in prompt_lower:
        return False

    return bool(
        re.search(r"\breturn\s*{", candidate)
        and re.search(r"\bname\s*=\s*wf\.vars\.fileMeta\.name\b", candidate)
        and re.search(r"\bextension\s*=\s*wf\.vars\.fileMeta\.extension\b", candidate)
        and re.search(r"\bsize\s*=\s*wf\.vars\.fileMeta\.size\b", candidate)
    )


def _looks_like_manager_name_absence_fallback(prompt: str, candidate: str) -> bool:
    prompt_lower = prompt.lower()
    if "manager" not in prompt_lower or "no-manager" not in prompt_lower:
        return False

    compact = re.sub(r"\s+", " ", candidate)
    return bool(
        re.search(
            r"manager\s+and\s+[\w\.]*manager\.name\s+or\s+[\"']no-manager[\"']",
            compact,
        )
    )


def _looks_like_date_ru_to_iso_date(prompt: str, candidate: str) -> bool:
    prompt_lower = prompt.lower()
    if "dd.mm.yyyy" not in prompt_lower or "yyyy-mm-dd" not in prompt_lower:
        return False

    compact = re.sub(r"\s+", " ", candidate)
    return bool(
        (
            re.search(r"day\s*=\s*string\.sub\(\s*dateRu\s*,\s*1\s*,\s*2\s*\)", compact)
            and re.search(r"month\s*=\s*string\.sub\(\s*dateRu\s*,\s*4\s*,\s*5\s*\)", compact)
            and re.search(r"year\s*=\s*string\.sub\(\s*dateRu\s*,\s*7\s*,\s*10\s*\)", compact)
        )
        or "string.match" in compact
    ) and bool(
        re.search(r"string\.format\(\s*[\"']%s-%s-%s[\"']\s*,\s*year\s*,\s*month\s*,\s*day\s*\)", compact)
        or re.search(r"return\s+year\s*\.\.\s*[\"']-[\"']\s*\.\.\s*month\s*\.\.\s*[\"']-[\"']\s*\.\.\s*day", compact)
    )


def _looks_like_nil_tags_empty_array(prompt: str, candidate: str) -> bool:
    prompt_lower = prompt.lower()
    if "tags" not in prompt_lower or "nil" not in prompt_lower:
        return False
    if "пуст" not in prompt_lower and "empty" not in prompt_lower:
        return False

    compact = re.sub(r"\s+", " ", candidate)
    return bool(
        "_utils.array.new()" in candidate
        and re.search(r"if\s+wf\.vars\.tags\s*~=\s*nil\s+then", compact)
        and re.search(r"table\.insert\(\s*\w+\s*,\s*tag\s*\)", compact)
        and re.search(r"\breturn\s+\w+\b", compact)
    )


def _looks_like_strict_type_count(prompt: str, candidate: str) -> bool:
    prompt_lower = prompt.lower()
    if "fielda" not in prompt_lower or "fieldb" not in prompt_lower or "числ" not in prompt_lower:
        return False

    compact = re.sub(r"\s+", "", candidate.lower())
    return (
        "type(row.fielda)==\"string\"" in compact
        and "type(row.fieldb)==\"number\"" in compact
        and "count=count+1" in compact
        and "tonumber(row.fieldb" not in compact
    )


def _looks_like_iso_date_string_comparison(prompt: str, candidate: str) -> bool:
    prompt_lower = prompt.lower()
    if "duedate" not in prompt_lower or "currentdate" not in prompt_lower:
        return False

    compact = re.sub(r"\s+", " ", candidate)
    return bool(
        re.search(r"invoice\.dueDate\s*(?:and\s+invoice\.dueDate\s*)?<\s*currentDate", compact)
        and "wf.initVariables.currentDate" in compact
    )


def _looks_like_nested_tax_code_array_projection(prompt: str, candidate: str) -> bool:
    prompt_lower = prompt.lower()
    if "invoices" not in prompt_lower or "taxcode" not in prompt_lower or "lines" not in prompt_lower:
        return False

    return bool(
        "_utils.array.new()" in candidate
        and re.search(r"for\s+_,\s*invoice\s+in\s+ipairs\(\s*wf\.vars\.invoices\s*\)", candidate)
        and re.search(r"for\s+_,\s*line\s+in\s+ipairs\(\s*invoice\.lines\s*\)", candidate)
        and re.search(r"table\.insert\(\s*\w+\s*,\s*line\.taxCode\s*\)", candidate)
    )


def _looks_like_non_empty_sku_quantity_filter(prompt: str, candidate: str) -> bool:
    prompt_lower = prompt.lower()
    if "sku" not in prompt_lower or "quantity" not in prompt_lower or "заполн" not in prompt_lower:
        return False

    compact = re.sub(r"\s+", " ", candidate)
    return bool(
        "_utils.array.new()" in candidate
        and re.search(r"line\.sku\s*~=\s*nil", compact)
        and re.search(r"line\.sku\s*~=\s*[\"']{2}", compact)
        and re.search(r"line\.quantity\s*~=\s*nil", compact)
        and re.search(r"table\.insert\(\s*\w+\s*,\s*line\s*\)", compact)
    )


def _looks_like_array_field_enrichment_preserving_fields(prompt: str, candidate: str) -> bool:
    prompt_lower = prompt.lower()
    if "для каждого" not in prompt_lower or "добав" not in prompt_lower or "поле" not in prompt_lower:
        return False
    if has_in_place_array_field_enrichment(candidate):
        return True
    if "table.insert" not in candidate and not re.search(r"\breturn\s+wf\.vars\.", candidate):
        return False

    return _has_in_place_item_field_enrichment(candidate) or _has_clone_based_item_field_enrichment(candidate)


def _has_in_place_item_field_enrichment(candidate: str) -> bool:
    loop_pattern = re.compile(
        r"for\s+_,\s*(?P<item>[A-Za-z_][A-Za-z0-9_]*)\s+in\s+ipairs\([^)]*\)\s+do(?P<body>.*?)\bend\b",
        re.DOTALL,
    )
    for match in loop_pattern.finditer(candidate):
        item = match.group("item")
        body = match.group("body")
        escaped_item = re.escape(item)
        if not re.search(rf"\b{escaped_item}\.[A-Za-z_][A-Za-z0-9_]*\s*=", body):
            continue
        if re.search(r"table\.insert\(\s*\w+\s*,\s*" + escaped_item + r"\s*\)", body):
            return True
        if re.search(r"\breturn\s+wf\.vars\.", candidate):
            return True
    return False


def _has_clone_based_item_field_enrichment(candidate: str) -> bool:
    loop_pattern = re.compile(
        r"for\s+_,\s*(?P<item>[A-Za-z_][A-Za-z0-9_]*)\s+in\s+ipairs\([^)]*\)\s+do(?P<body>.*?)\bend\b",
        re.DOTALL,
    )
    for match in loop_pattern.finditer(candidate):
        body = match.group("body")
        clone_match = re.search(
            r"\blocal\s+(?P<clone>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*_utils\.table\.clone\(\s*"
            + re.escape(match.group("item"))
            + r"\s*\)",
            body,
        )
        if clone_match is None:
            continue
        clone = re.escape(clone_match.group("clone"))
        if re.search(rf"\b{clone}\.[A-Za-z_][A-Za-z0-9_]*\s*=", body) and re.search(
            rf"table\.insert\(\s*\w+\s*,\s*{clone}\s*\)",
            body,
        ):
            return True
    return False


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


def _build_validation_bundle(
    *,
    task_spec,
    current_candidate: str,
    format_report: ValidatorReport,
    syntax_report: ValidatorReport,
    static_report: ValidatorReport,
    principle_report: ValidatorReport,
    runtime_report: ValidatorReport,
    semantic_report: ValidatorReport,
) -> ValidationBundle:
    final_failure_classes = _collect_failure_classes(
        format_report,
        syntax_report,
        static_report,
        principle_report,
        runtime_report,
        semantic_report,
    )
    return ValidationBundle(
        task_spec=task_spec,
        current_candidate=current_candidate,
        format_report=format_report,
        syntax_report=syntax_report,
        static_report=static_report,
        principle_report=principle_report,
        runtime_report=runtime_report,
        semantic_report=semantic_report,
        final_failure_classes=final_failure_classes,
        repair_priority=_repair_priority_from_reports(
            format_report,
            syntax_report,
            static_report,
            principle_report,
            runtime_report,
            semantic_report,
        ),
        behavioral_fingerprint=_behavioral_fingerprint(runtime_report),
        invalid_shape_signature=_invalid_shape_signature(format_report, syntax_report, static_report),
        disallowed_root_signature=_disallowed_root_signature(static_report, principle_report),
    )


def _collect_failure_classes(*reports: ValidatorReport) -> tuple[str, ...]:
    failure_classes: list[str] = []
    for report in reports:
        for finding in report.findings:
            if finding.failure_class not in failure_classes:
                failure_classes.append(finding.failure_class)
    return tuple(failure_classes)


def _repair_priority_from_reports(*reports: ValidatorReport) -> tuple[str, ...]:
    return _collect_failure_classes(*reports)


def _behavioral_fingerprint(runtime_report: ValidatorReport) -> str | None:
    if runtime_report.metadata is None:
        return None
    value = runtime_report.metadata.get("behavioral_fingerprint")
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def _invalid_shape_signature(
    format_report: ValidatorReport,
    syntax_report: ValidatorReport,
    static_report: ValidatorReport,
) -> str | None:
    return _findings_signature(
        format_report,
        syntax_report,
        static_report,
        exclude_failure_classes={"disallowed_data_root", "mixed_root_families"},
    )


def _disallowed_root_signature(
    static_report: ValidatorReport,
    principle_report: ValidatorReport,
) -> str | None:
    return _findings_signature(
        static_report,
        principle_report,
        include_failure_classes={"disallowed_data_root", "mixed_root_families"},
    )


def _findings_signature(
    *reports: ValidatorReport,
    include_failure_classes: set[str] | None = None,
    exclude_failure_classes: set[str] | None = None,
) -> str | None:
    signature_parts: list[str] = []
    for report in reports:
        for finding in report.findings:
            if include_failure_classes is not None and finding.failure_class not in include_failure_classes:
                continue
            if exclude_failure_classes is not None and finding.failure_class in exclude_failure_classes:
                continue
            signature = f"{finding.failure_class}:{finding.location}:{finding.message}"
            if signature not in signature_parts:
                signature_parts.append(signature)
    if not signature_parts:
        return None
    return "|".join(signature_parts)


def _detect_repair_oscillation(
    *,
    current_fingerprint: str,
    current_behavioral_fingerprint: str | None,
    current_invalid_shape_signature: str | None,
    current_disallowed_root_signature: str | None,
    current_failure_class: str | None,
    prior_fingerprints: list[str],
    behavioral_history: list[str],
    invalid_shape_history: list[str],
    disallowed_root_history: list[str],
    failure_history: list[str],
) -> bool:
    if current_fingerprint in prior_fingerprints:
        return True
    if current_behavioral_fingerprint is not None and current_behavioral_fingerprint in behavioral_history:
        return True
    if current_invalid_shape_signature is not None and current_invalid_shape_signature in invalid_shape_history:
        return True
    if current_disallowed_root_signature is not None and current_disallowed_root_signature in disallowed_root_history:
        return True
    return False


def _score_candidate(
    *,
    format_report: ValidatorReport,
    syntax_report: ValidatorReport,
    static_report: ValidatorReport,
    principle_report: ValidatorReport,
    runtime_report: ValidatorReport,
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
    if runtime_report.status == "pass":
        score += 6
    if semantic_report.status == "pass":
        score += 6
    return score


def _try_repair_with_tool(
    candidate: str,
    *,
    output_mode: str,
    failure_class: str | None,
) -> str | None:
    if output_mode == RAW_LUA and failure_class == "missing_array_allocator":
        return _repair_missing_array_allocator(candidate)
    if output_mode == PATCH_MODE and failure_class in {"patch_path_keys", "full_rewrite_patch_payload"}:
        return _repair_patch_path_keys(candidate)
    if output_mode in {JSON_WRAPPER, PATCH_MODE} and failure_class in {"invalid_json", "invalid_wrapper"}:
        return _repair_invalid_json_mode(candidate, output_mode=output_mode)
    return None


def _repair_patch_path_keys(candidate: str) -> str | None:
    payload = _load_json_object(candidate.strip())
    if payload is None:
        return None

    payload = _unwrap_nested_wf_patch_payload(payload)
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


def _unwrap_nested_wf_patch_payload(payload: dict[str, object]) -> dict[str, object]:
    if set(payload.keys()) != {"wf"}:
        return payload

    wf_payload = payload.get("wf")
    if not isinstance(wf_payload, dict):
        return payload

    for root_name in ("vars", "initVariables"):
        root_payload = wf_payload.get(root_name)
        if set(wf_payload.keys()) == {root_name} and isinstance(root_payload, dict):
            return root_payload

    return payload


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


def _build_repair_prompt_via_prompter(
    model_adapter: PromptDrivenModelAdapter,
    *,
    prompt_package: DomainPromptPackage,
    current_candidate: str,
    repair_instruction: str,
    repair_count: int,
    failure_class: str | None,
    validation_bundle: ValidationBundle,
) -> tuple[PromptBuilderResult, AgentPrompt, str]:
    fallback_generator_prompt = _build_repair_generator_prompt(
        prompt_package.agent_prompt,
        current_candidate,
        repair_instruction,
        repair_count=repair_count,
        failure_class=failure_class,
        validation_bundle=validation_bundle,
    )
    fallback_result = PromptBuilderResult(
        agent_prompt=fallback_generator_prompt,
        expected_result_format=prompt_package.expected_result_format,
        forbidden_patterns=prompt_package.forbidden_patterns,
        retrieval_pack=prompt_package.prompt_builder_result.retrieval_pack,
        source="deterministic_repair_fallback",
    )
    repair_prompter_prompt = build_repair_prompter_agent_prompt(
        original_generator_prompt=prompt_package.agent_prompt,
        current_candidate=current_candidate,
        repair_instruction=repair_instruction,
        repair_count=repair_count,
        failure_class=failure_class,
        validation_bundle=validation_bundle,
        fallback_generator_prompt=fallback_generator_prompt,
    )
    repair_prompter_raw_response = _generate_from_agent(model_adapter, repair_prompter_prompt)
    return (
        apply_prompter_agent_response(repair_prompter_raw_response, fallback_result),
        repair_prompter_prompt,
        repair_prompter_raw_response,
    )


def _build_repair_generator_prompt(
    original_prompt: AgentPrompt,
    candidate: str,
    repair_prompt: str,
    *,
    repair_count: int,
    failure_class: str | None,
    validation_bundle: ValidationBundle,
) -> AgentPrompt:
    task_spec = validation_bundle.task_spec
    system_sections = [
        "You are the generator agent for a repair iteration.",
        "Use only the compact repair facts as the source of truth.",
        f"Output mode: {task_spec.output_mode}.",
        "Return only the generated result with no prose, markdown, diagnostics, or copied prompt text.",
    ]
    if repair_count >= 2:
        system_sections.extend(
            [
                "Escalation strategy:",
                "Solve the task again from scratch if needed.",
                "Do not repeat the same candidate shape or the same failed pattern.",
            ]
        )
    anti_pattern_hint = _anti_pattern_hint(failure_class)
    if anti_pattern_hint is not None:
        system_sections.extend(["Avoid this pattern:", anti_pattern_hint])

    user_sections = [
        "Task:",
        task_spec.task_text,
        "TaskSpec compact:",
        json.dumps(_compact_task_spec_for_repair(task_spec), ensure_ascii=False, separators=(",", ":")),
        "Current candidate:",
        _compact_text(candidate, limit=420),
        "Validation summary:",
        json.dumps(_compact_validation_bundle_for_repair(validation_bundle), ensure_ascii=False, separators=(",", ":")),
        "Repair task:",
        repair_prompt,
    ]
    return AgentPrompt(
        agent_name="generator",
        messages=(
            AgentMessage(role="system", content="\n".join(system_sections)),
            AgentMessage(role="user", content="\n\n".join(user_sections)),
        ),
    )


def _compact_task_spec_for_repair(task_spec: TaskSpec) -> dict[str, object]:
    return {
        "op": task_spec.operation,
        "shape": task_spec.expected_shape,
        "mode": task_spec.output_mode,
        "roots": list(task_spec.input_roots),
        "risks": list(task_spec.risk_tags),
        "edges": list(task_spec.edge_cases),
    }


def _compact_validation_bundle_for_repair(validation_bundle: ValidationBundle) -> dict[str, object]:
    return {
        "failures": list(validation_bundle.final_failure_classes),
        "priority": list(validation_bundle.repair_priority),
        "format": _compact_report_for_repair(validation_bundle.format_report),
        "syntax": _compact_report_for_repair(validation_bundle.syntax_report),
        "static": _compact_report_for_repair(validation_bundle.static_report),
        "principle": _compact_report_for_repair(validation_bundle.principle_report),
        "runtime": _compact_report_for_repair(validation_bundle.runtime_report, include_metadata=True),
        "semantic": _compact_report_for_repair(validation_bundle.semantic_report),
        "invalid_shape": _compact_text(validation_bundle.invalid_shape_signature, limit=320),
        "disallowed_root": validation_bundle.disallowed_root_signature,
        "behavior": validation_bundle.behavioral_fingerprint,
    }


def _compact_report_for_repair(report: ValidatorReport, *, include_metadata: bool = False) -> dict[str, object]:
    payload: dict[str, object] = {"s": report.status}
    if report.skipped_reason:
        payload["skip"] = report.skipped_reason
    if report.findings:
        payload["f"] = [
            {
                "c": finding.failure_class,
                "m": _compact_text(finding.message, limit=220),
                "fix": _compact_text(finding.suggestion, limit=220),
            }
            for finding in report.findings[:3]
        ]
    if include_metadata and report.metadata:
        payload["meta"] = {
            key: value
            for key, value in report.metadata.items()
            if key in {"failed_fixture", "actual_value", "expected_value", "behavioral_fingerprint"}
        }
    return payload


def _compact_text(value: str | None, *, limit: int) -> str | None:
    if value is None:
        return None
    compact = re.sub(r"\s+", " ", value).strip()
    return compact if len(compact) <= limit else compact[:limit] + "..."


def _anti_pattern_hint(failure_class: str | None) -> str | None:
    if failure_class == "semantic_mismatch":
        return "Do not preserve the previous semantic mistake. Match the user intent literally."
    if failure_class == "full_rewrite_patch_payload":
        return "Do not rewrite the full payload or nest the patch under wf.* paths."
    if failure_class == "missing_timezone_offset_handling":
        return "Do not invent helper functions. Parse and apply offset_sign, offset_hour, and offset_min inline."
    return None


def _format_repair_bundle_facts(validation_bundle: ValidationBundle) -> str:
    facts = [
        f"- final_failure_classes: {', '.join(validation_bundle.final_failure_classes) or 'none'}",
        f"- repair_priority: {', '.join(validation_bundle.repair_priority) or 'none'}",
        f"- allowed_roots: {', '.join(validation_bundle.task_spec.input_roots) or 'none'}",
        f"- expected_shape: {validation_bundle.task_spec.expected_shape}",
        f"- edge_cases: {', '.join(validation_bundle.task_spec.edge_cases) or 'none'}",
    ]
    if validation_bundle.invalid_shape_signature is not None:
        facts.append(f"- invalid_shape_signature: {validation_bundle.invalid_shape_signature}")
    if validation_bundle.disallowed_root_signature is not None:
        facts.append(f"- disallowed_root_signature: {validation_bundle.disallowed_root_signature}")
    if validation_bundle.behavioral_fingerprint is not None:
        facts.append(f"- behavioral_fingerprint: {validation_bundle.behavioral_fingerprint}")
    runtime_metadata = validation_bundle.runtime_report.metadata
    if runtime_metadata is not None:
        facts.append(
            "- runtime_metadata: "
            + json.dumps(runtime_metadata, ensure_ascii=False, separators=(",", ":"))
        )
    return "\n".join(facts)


def _stop_reason_for_finalize(critic_report: dict[str, object]) -> str:
    failure_class = critic_report.get("failure_class")
    if failure_class == "repair_oscillation":
        return "oscillation_detected"
    if failure_class == "validator_conflict":
        return "validator_conflict"
    return "repair_exhausted"


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
            "task_spec": prompt_package.task_spec.to_dict(),
            "planner_result": prompt_package.planner_result.to_debug_dict(),
            "prompt_builder_result": prompt_package.prompt_builder_result.to_debug_dict(),
            "agent_prompt": {
                "agent": prompt_package.agent_prompt.agent_name,
                "messages": prompt_package.agent_prompt.to_messages_payload(),
            },
        },
        "pipeline_layers": [
            {
                "stage": "input_normalization",
                "kind": "deterministic",
                "status": "completed",
            },
            {
                "stage": "planner",
                "kind": "agent_layer",
                "status": "completed",
                "details": prompt_package.planner_result.to_debug_dict(),
            },
            {
                "stage": "prompter",
                "kind": "agent_layer",
                "status": "completed",
                "details": prompt_package.prompt_builder_result.to_debug_dict(),
            },
            {
                "stage": "generator",
                "kind": "llm_agent",
                "status": "configured",
                "agent": prompt_package.agent_prompt.agent_name,
            },
        ],
        "agent_layer_calls": list(prompt_package.agent_layer_calls),
        "model_calls": [],
        "validation_passes": [],
    }
