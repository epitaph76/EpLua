from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from packages.orchestrator.agent_prompt import AgentMessage, AgentPrompt
from packages.orchestrator.planner import PlannerResult
from packages.orchestrator.task_spec import TaskSpec
from packages.retrieval.selector import RetrievalPack
from packages.shared.quality import ValidationBundle
from packages.shared.language import natural_language_name


@dataclass(frozen=True)
class PromptBuilderResult:
    agent_prompt: AgentPrompt
    expected_result_format: str
    forbidden_patterns: tuple[str, ...]
    retrieval_pack: RetrievalPack
    source: str = "deterministic"
    fallback_reason: str | None = None

    def to_debug_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "agent": "prompter",
            "source": self.source,
            "generator_agent": self.agent_prompt.agent_name,
            "expected_result_format": self.expected_result_format,
            "forbidden_patterns": list(self.forbidden_patterns),
            "retrieval": {
                "examples": [example["id"] for example in self.retrieval_pack.examples],
                "has_archetype_template": self.retrieval_pack.archetype_template is not None,
                "has_format_rules": self.retrieval_pack.format_rules is not None,
            },
        }
        if self.fallback_reason is not None:
            payload["fallback_reason"] = self.fallback_reason
        return payload


def build_prompt_package_for_generation(
    *,
    task_text: str,
    provided_context: str | None,
    archetype: str,
    archetype_config: dict[str, object],
    effective_output_mode: str,
    output_mode_rules: dict[str, object],
    common_rules: list[str],
    forbidden_patterns: tuple[str, ...],
    risk_tags: tuple[str, ...],
    planner_result: PlannerResult,
    templates: dict[str, object],
) -> PromptBuilderResult:
    retrieval_pack = RetrievalPack(examples=tuple(), archetype_template=None, format_rules=None)

    system_sections = [
        "You are generating LocalScript-compatible Lua 5.5 for the luaMTS domain.",
        f"Use {natural_language_name(planner_result.language)} for any clarification question, explanation, or repair note.",
        f"Task archetype: {archetype}",
        f"Output mode: {effective_output_mode}",
        f"Archetype description: {archetype_config['description']}",
        f"Expected result format: {output_mode_rules['expected_result_format']}",
        "Common rules:",
        _format_list(common_rules),
        "Mode-specific rules:",
        _format_list(list(output_mode_rules["rules"])),
        "Allowed data roots: " + (", ".join(planner_result.input_roots) if planner_result.input_roots else "none explicitly provided"),
        "Forbidden patterns:",
        _format_list(forbidden_patterns),
        "TaskSpec:",
        _format_task_spec(planner_result.task_spec),
    ]

    intent_hints = _intent_hints(planner_result.task_intents)
    if planner_result.task_intents:
        system_sections.extend(
            [
                "Resolved task intents:",
                _format_list(list(planner_result.task_intents)),
            ]
        )
    if intent_hints:
        system_sections.extend(["Intent hints:", _format_list(intent_hints)])

    if risk_tags:
        risk_hints = [
            templates["risk_hints"][risk_tag]
            for risk_tag in risk_tags
            if risk_tag in templates["risk_hints"]
        ]
        if risk_hints:
            system_sections.extend(["Risk hints:", _format_list(risk_hints)])

    if planner_result.clarification_required:
        system_sections.extend(
            [
                "Clarification requirement:",
                "- The context references both wf.vars.* and wf.initVariables.* without explicit input_roots.",
                "- Ask one focused clarification question instead of generating code.",
            ]
        )

    user_sections = ["Task:", task_text]
    if provided_context:
        user_sections.extend(["Provided context:", provided_context])

    return PromptBuilderResult(
        agent_prompt=AgentPrompt(
            agent_name="generator",
            messages=(
                AgentMessage(role="system", content="\n".join(system_sections)),
                AgentMessage(role="user", content="\n".join(user_sections)),
            ),
        ),
        expected_result_format=str(output_mode_rules["expected_result_format"]),
        forbidden_patterns=forbidden_patterns,
        retrieval_pack=retrieval_pack,
    )


def build_prompter_agent_prompt(
    *,
    task_text: str,
    provided_context: str | None,
    planner_result: PlannerResult,
    fallback_result: PromptBuilderResult,
) -> AgentPrompt:
    system_prompt = "\n".join(
        [
            "You are the prompter agent for the luaMTS validation pipeline.",
            "Do not generate Lua code.",
            "Return one compact minified JSON object only.",
            "Do not echo the full fallback prompt.",
            "Return only short additions for the generator prompt.",
            "Preserve TaskSpec, output mode, allowed roots, and hard domain rules.",
            'Required compact JSON shape: {"sys":["short system hint"],"user":["short user hint"]}',
        ]
    )
    user_prompt = "\n".join(
        [
            "TaskSpec:",
            json.dumps(planner_result.task_spec.to_dict(), ensure_ascii=False, separators=(",", ":")),
            "Task intents:",
            json.dumps(list(planner_result.task_intents), ensure_ascii=False, separators=(",", ":")),
            "Task:",
            task_text,
            "Context excerpt:",
            _compact_context_excerpt(provided_context),
            "Fallback summary:",
            json.dumps(_prompt_builder_summary(fallback_result), ensure_ascii=False, separators=(",", ":")),
            "Return only additions; the full generator prompt is built locally.",
        ]
    )
    return AgentPrompt(
        agent_name="prompter",
        messages=(
            AgentMessage(role="system", content=system_prompt),
            AgentMessage(role="user", content=user_prompt),
        ),
    )


def build_repair_prompter_agent_prompt(
    *,
    original_generator_prompt: AgentPrompt,
    current_candidate: str,
    repair_instruction: str,
    repair_count: int,
    failure_class: str | None,
    validation_bundle: ValidationBundle,
    fallback_generator_prompt: AgentPrompt,
) -> AgentPrompt:
    system_prompt = "\n".join(
        [
            "You are the prompter agent for a repair iteration in the luaMTS validation pipeline.",
            "Do not generate Lua code yourself.",
            "Return one compact minified JSON object only.",
            "Do not echo the full fallback prompt.",
            "Return only short additions for the next generator prompt.",
            "Use ValidationBundle and the critic instruction as the source of truth.",
            "Make the generator task explicit enough to avoid repeating the same failed candidate shape.",
            "Preserve TaskSpec, output mode, allowed roots, and hard domain rules.",
            'Required compact JSON shape: {"sys":["short system hint"],"user":["short user hint"]}',
        ]
    )
    user_prompt = "\n".join(
        [
            "Repair iteration:",
            str(repair_count),
            "Failure class:",
            failure_class or "unknown",
            "Current candidate:",
            current_candidate,
            "Validation summary:",
            json.dumps(_validation_bundle_summary(validation_bundle), ensure_ascii=False, separators=(",", ":")),
            "Critic repair instruction:",
            repair_instruction,
            "Prompt summary:",
            json.dumps(
                {
                    "original_agent": original_generator_prompt.agent_name,
                    "fallback_agent": fallback_generator_prompt.agent_name,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            "Return only additions; the full generator prompt is built locally.",
        ]
    )
    return AgentPrompt(
        agent_name="prompter",
        messages=(
            AgentMessage(role="system", content=system_prompt),
            AgentMessage(role="user", content=user_prompt),
        ),
    )


def apply_prompter_agent_response(
    raw_response: str,
    fallback_result: PromptBuilderResult,
) -> PromptBuilderResult:
    payload = _extract_json_payload(raw_response)
    if payload is None:
        return _prompter_fallback(fallback_result, "prompter_invalid_json")

    patch_result = _apply_prompt_patch_payload(payload, fallback_result)
    if patch_result is not None:
        return patch_result

    system_message = payload.get("system_message")
    user_message = payload.get("user_message")
    if not isinstance(system_message, str) or not system_message.strip():
        return _prompter_fallback(fallback_result, "prompter_missing_system_message")
    if not isinstance(user_message, str) or not user_message.strip():
        return _prompter_fallback(fallback_result, "prompter_missing_user_message")
    if "TaskSpec" not in system_message + "\n" + user_message:
        return _prompter_fallback(fallback_result, "prompter_missing_taskspec")

    return PromptBuilderResult(
        agent_prompt=AgentPrompt(
            agent_name="generator",
            messages=(
                AgentMessage(role="system", content=system_message.strip()),
                AgentMessage(role="user", content=user_message.strip()),
            ),
        ),
        expected_result_format=fallback_result.expected_result_format,
        forbidden_patterns=fallback_result.forbidden_patterns,
        retrieval_pack=fallback_result.retrieval_pack,
        source="agent",
    )


def _apply_prompt_patch_payload(
    payload: dict[str, Any],
    fallback_result: PromptBuilderResult,
) -> PromptBuilderResult | None:
    system_additions = _string_list_from_payload(payload, "sys", "system_additions", "system_hints")
    user_additions = _string_list_from_payload(payload, "user", "user_additions", "user_hints")
    if not system_additions and not user_additions:
        return None

    fallback_messages = fallback_result.agent_prompt.messages
    if len(fallback_messages) < 2:
        return None

    system_message = fallback_messages[0].content
    user_message = fallback_messages[1].content
    if system_additions:
        system_message = "\n".join([system_message, "Prompter agent additions:", _format_list(system_additions)])
    if user_additions:
        user_message = "\n".join([user_message, "Prompter agent additions:", _format_list(user_additions)])

    return PromptBuilderResult(
        agent_prompt=AgentPrompt(
            agent_name="generator",
            messages=(
                AgentMessage(role="system", content=system_message.strip()),
                AgentMessage(role="user", content=user_message.strip()),
            ),
        ),
        expected_result_format=fallback_result.expected_result_format,
        forbidden_patterns=fallback_result.forbidden_patterns,
        retrieval_pack=fallback_result.retrieval_pack,
        source="agent_patch",
    )


def _string_list_from_payload(payload: dict[str, Any], *keys: str) -> list[str]:
    values: list[str] = []
    for key in keys:
        raw_value = payload.get(key)
        if isinstance(raw_value, str) and raw_value.strip():
            raw_items = [raw_value]
        elif isinstance(raw_value, list):
            raw_items = raw_value
        else:
            continue
        for item in raw_items:
            if isinstance(item, str) and item.strip() and item.strip() not in values:
                values.append(item.strip())
    return values


def _prompter_fallback(fallback_result: PromptBuilderResult, reason: str) -> PromptBuilderResult:
    return PromptBuilderResult(
        agent_prompt=fallback_result.agent_prompt,
        expected_result_format=fallback_result.expected_result_format,
        forbidden_patterns=fallback_result.forbidden_patterns,
        retrieval_pack=fallback_result.retrieval_pack,
        source="deterministic_fallback",
        fallback_reason=reason,
    )


def _extract_json_payload(raw_response: str) -> dict[str, Any] | None:
    start = raw_response.find("{")
    end = raw_response.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    try:
        payload = json.loads(raw_response[start : end + 1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _compact_context_excerpt(provided_context: str | None, *, limit: int = 360) -> str:
    if not provided_context:
        return ""
    compact = " ".join(provided_context.split())
    return compact if len(compact) <= limit else compact[:limit] + "..."


def _prompt_builder_summary(fallback_result: PromptBuilderResult) -> dict[str, object]:
    return {
        "expected_result_format": fallback_result.expected_result_format,
        "forbidden_count": len(fallback_result.forbidden_patterns),
        "retrieval_examples": [example["id"] for example in fallback_result.retrieval_pack.examples],
        "has_archetype_template": fallback_result.retrieval_pack.archetype_template is not None,
        "has_format_rules": fallback_result.retrieval_pack.format_rules is not None,
    }


def _validation_bundle_summary(validation_bundle: ValidationBundle) -> dict[str, object]:
    return {
        "task_spec": validation_bundle.task_spec.to_dict(),
        "current_candidate": validation_bundle.current_candidate,
        "final_failure_classes": list(validation_bundle.final_failure_classes),
        "repair_priority": list(validation_bundle.repair_priority),
        "invalid_shape_signature": validation_bundle.invalid_shape_signature,
        "disallowed_root_signature": validation_bundle.disallowed_root_signature,
        "behavioral_fingerprint": validation_bundle.behavioral_fingerprint,
        "runtime_metadata": validation_bundle.runtime_report.metadata,
        "findings": _first_findings_summary(
            validation_bundle.format_report,
            validation_bundle.static_report,
            validation_bundle.principle_report,
            validation_bundle.runtime_report,
            validation_bundle.semantic_report,
        ),
    }


def _first_findings_summary(*reports: Any) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    for report in reports:
        for finding in report.findings:
            findings.append(
                {
                    "validator": finding.validator,
                    "failure_class": finding.failure_class,
                    "message": finding.message,
                    "suggestion": finding.suggestion,
                }
            )
            if len(findings) >= 4:
                return findings
    return findings


def _format_list(values: list[str] | tuple[str, ...]) -> str:
    return "\n".join(f"- {value}" for value in values)


def _format_task_spec(task_spec: TaskSpec) -> str:
    lines = [
        f"- operation: {task_spec.operation}",
        f"- expected_shape: {task_spec.expected_shape}",
        f"- input_roots: {', '.join(task_spec.input_roots) if task_spec.input_roots else 'none'}",
        f"- risk_tags: {', '.join(task_spec.risk_tags) if task_spec.risk_tags else 'none'}",
    ]
    if task_spec.edge_cases:
        lines.append(f"- edge_cases: {', '.join(task_spec.edge_cases)}")
    if task_spec.clarification_required and task_spec.clarification_question:
        lines.append(f"- clarification_question: {task_spec.clarification_question}")
    return "\n".join(lines)


def _intent_hints(task_intents: tuple[str, ...]) -> list[str]:
    hints: list[str] = []
    if "clear_target_fields" in task_intents:
        hints.append("When the task says to clear field values, update only those named fields and preserve unrelated fields.")
    if "remove_target_fields" in task_intents:
        hints.append("When the task says to remove fields, delete only the named keys instead of filtering the full object shape.")
    if "keep_only_target_fields" in task_intents:
        hints.append("When the task says to keep only certain fields, explicitly drop unrelated keys and return the reduced shape.")
    if "preserve_untouched_fields" in task_intents:
        hints.append("Untouched fields must remain available unless the task explicitly says to remove them.")
    if "mutate_in_place" in task_intents:
        hints.append("Prefer in-place mutation only when the task wording explicitly asks to update the existing structure.")
    return hints

