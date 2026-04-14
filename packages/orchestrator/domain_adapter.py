from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from packages.orchestrator.agent_prompt import AgentPrompt
from packages.orchestrator.planner import (
    PlannerResult,
    apply_planner_agent_response,
    build_planner_agent_prompt,
    plan_task,
)
from packages.orchestrator.prompter import (
    PromptBuilderResult,
    apply_prompter_agent_response,
    build_prompt_package_for_generation,
    build_prompter_agent_prompt,
)
from packages.orchestrator.task_spec import TaskSpec
from packages.shared.language import DEFAULT_LANGUAGE

RAW_LUA = "raw_lua"
JSON_WRAPPER = "json_wrapper"
PATCH_MODE = "patch_mode"
CLARIFICATION = "clarification"
_VALID_OUTPUT_MODES = {RAW_LUA, JSON_WRAPPER, PATCH_MODE, CLARIFICATION}
_DEFAULT_PLANNER_ARCHETYPE = "transformation"
_CODE_FENCE_PATTERN = re.compile(r"```(?:lua|json)?\s*(.*?)```", re.DOTALL)
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ARCHETYPE_REGISTRY_PATH = _REPO_ROOT / "packages" / "task-archetypes" / "registry.json"
_TEMPLATE_PACK_PATH = _REPO_ROOT / "knowledge" / "templates" / "domain_prompt_templates.json"


@dataclass(frozen=True)
class DomainPromptPackage:
    prompt: str
    agent_prompt: AgentPrompt
    archetype: str
    output_mode: str
    expected_result_format: str
    allowed_data_roots: tuple[str, ...]
    forbidden_patterns: tuple[str, ...]
    risk_tags: tuple[str, ...]
    task_intents: tuple[str, ...]
    clarification_required: bool
    task_spec: TaskSpec
    execution_context: Any | None
    planner_result: PlannerResult
    prompt_builder_result: PromptBuilderResult
    agent_layer_calls: tuple[dict[str, object], ...] = ()
    language: str = DEFAULT_LANGUAGE


def build_domain_prompt_package(
    task_text: str,
    provided_context: str | None,
    *,
    archetype: str | None = None,
    output_mode: str | None = None,
    input_roots: list[str] | None = None,
    risk_tags: list[str] | None = None,
    language: str = DEFAULT_LANGUAGE,
    agent_runner: Callable[[AgentPrompt], str] | None = None,
) -> DomainPromptPackage:
    archetypes = _load_json(_ARCHETYPE_REGISTRY_PATH)
    templates = _load_json(_TEMPLATE_PACK_PATH)
    explicit_archetype = archetype is not None
    explicit_output_mode = output_mode is not None
    fallback_archetype = archetype or _DEFAULT_PLANNER_ARCHETYPE
    fallback_output_mode = output_mode or RAW_LUA

    if fallback_archetype not in archetypes:
        raise ValueError(f"Unknown task archetype: {fallback_archetype}")
    if fallback_output_mode not in _VALID_OUTPUT_MODES:
        raise ValueError(f"Unknown output mode: {fallback_output_mode}")

    fallback_archetype_config = archetypes[fallback_archetype]
    if (
        explicit_archetype
        and explicit_output_mode
        and fallback_output_mode != CLARIFICATION
        and fallback_output_mode not in fallback_archetype_config["allowed_output_modes"]
    ):
        raise ValueError(f"Output mode {fallback_output_mode} is not allowed for archetype {fallback_archetype}.")

    risk_tags = tuple(risk_tags or ())
    agent_layer_calls: list[dict[str, object]] = []
    planner_result = plan_task(
        task_text,
        provided_context,
        language=language,
        archetype=fallback_archetype,
        output_mode=fallback_output_mode,
        input_roots=input_roots,
        risk_tags=risk_tags,
        explicit_archetype=explicit_archetype,
        explicit_output_mode=explicit_output_mode,
    )
    if agent_runner is not None:
        planner_agent_prompt = build_planner_agent_prompt(
            task_text=task_text,
            provided_context=provided_context,
            fallback_result=planner_result,
        )
        planner_raw_response = agent_runner(planner_agent_prompt)
        planner_result = apply_planner_agent_response(
            planner_raw_response,
            planner_result,
            allowed_archetypes=tuple(archetypes.keys()),
        )
        agent_layer_calls.append(
            {
                "phase": "planner",
                "agent": planner_agent_prompt.agent_name,
                "prompt": planner_agent_prompt.to_legacy_prompt(),
                "messages": planner_agent_prompt.to_messages_payload(),
                "raw_response": planner_raw_response,
                "planner_result": planner_result.to_debug_dict(),
            }
        )
    effective_archetype = planner_result.task_spec.archetype
    effective_output_mode = planner_result.task_spec.output_mode
    effective_risk_tags = planner_result.task_spec.risk_tags
    archetype_config = archetypes[effective_archetype]

    output_mode_rules = templates["output_modes"][effective_output_mode]
    common_rules = templates["common_rules"]
    forbidden_patterns = tuple(templates["forbidden_patterns"])
    prompt_builder_result = build_prompt_package_for_generation(
        task_text=task_text,
        provided_context=provided_context,
        archetype=effective_archetype,
        archetype_config=archetype_config,
        effective_output_mode=effective_output_mode,
        output_mode_rules=output_mode_rules,
        common_rules=common_rules,
        forbidden_patterns=forbidden_patterns,
        risk_tags=effective_risk_tags,
        planner_result=planner_result,
        templates=templates,
    )
    if agent_runner is not None:
        prompter_agent_prompt = build_prompter_agent_prompt(
            task_text=task_text,
            provided_context=provided_context,
            planner_result=planner_result,
            fallback_result=prompt_builder_result,
        )
        prompter_raw_response = agent_runner(prompter_agent_prompt)
        prompt_builder_result = apply_prompter_agent_response(prompter_raw_response, prompt_builder_result)
        agent_layer_calls.append(
            {
                "phase": "prompter",
                "agent": prompter_agent_prompt.agent_name,
                "prompt": prompter_agent_prompt.to_legacy_prompt(),
                "messages": prompter_agent_prompt.to_messages_payload(),
                "raw_response": prompter_raw_response,
                "prompt_builder_result": prompt_builder_result.to_debug_dict(),
            }
        )

    return DomainPromptPackage(
        prompt=prompt_builder_result.agent_prompt.to_legacy_prompt(),
        agent_prompt=prompt_builder_result.agent_prompt,
        archetype=effective_archetype,
        output_mode=effective_output_mode,
        expected_result_format=prompt_builder_result.expected_result_format,
        allowed_data_roots=planner_result.input_roots,
        forbidden_patterns=prompt_builder_result.forbidden_patterns,
        risk_tags=effective_risk_tags,
        task_intents=planner_result.task_intents,
        clarification_required=planner_result.clarification_required,
        task_spec=planner_result.task_spec,
        execution_context=planner_result.execution_context,
        planner_result=planner_result,
        prompt_builder_result=prompt_builder_result,
        agent_layer_calls=tuple(agent_layer_calls),
        language=planner_result.language,
    )


def normalize_model_output(response_text: str, output_mode: str) -> str:
    if output_mode == RAW_LUA:
        return _normalize_raw_lua(response_text)
    if output_mode in {JSON_WRAPPER, PATCH_MODE}:
        return _normalize_json_payload(response_text)
    if output_mode == CLARIFICATION:
        return _normalize_clarification(response_text)
    raise ValueError(f"Unknown output mode: {output_mode}")


def _normalize_raw_lua(response_text: str) -> str:
    candidate = _extract_code_fence(response_text) or response_text.strip()
    lines = [line.rstrip() for line in candidate.splitlines() if line.strip()]
    while lines and not _looks_like_lua(lines[0]):
        lines.pop(0)
    if not lines:
        raise ValueError("raw_lua response did not contain Lua code.")
    normalized = "\n".join(lines).strip()
    if normalized.startswith("{"):
        raise ValueError("raw_lua response cannot be a JSON object.")
    return normalized


def _normalize_json_payload(response_text: str) -> str:
    candidate = _extract_code_fence(response_text) or _extract_json_object(response_text)
    payload = json.loads(candidate)
    if not isinstance(payload, dict):
        raise ValueError("JSON-based output modes require a JSON object.")
    _validate_lua_wrappers(payload)
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _normalize_clarification(response_text: str) -> str:
    candidate = (_extract_code_fence(response_text) or response_text).strip()
    if candidate.startswith("{"):
        raise ValueError("clarification mode must return a plain clarification question.")
    return candidate


def _extract_code_fence(response_text: str) -> str | None:
    match = _CODE_FENCE_PATTERN.search(response_text)
    if not match:
        return None
    return match.group(1).strip()


def _extract_json_object(response_text: str) -> str:
    start = response_text.find("{")
    end = response_text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("JSON-based output mode did not contain a JSON object.")
    return response_text[start : end + 1].strip()


def _validate_lua_wrappers(node: Any) -> None:
    if isinstance(node, dict):
        for value in node.values():
            _validate_lua_wrappers(value)
        return
    if isinstance(node, list):
        for value in node:
            _validate_lua_wrappers(value)
        return
    if isinstance(node, str) and not (node.startswith("lua{") and node.endswith("}lua")):
        raise ValueError("JSON-based output modes require lua{...}lua wrappers for string values.")


def _looks_like_lua(line: str) -> bool:
    stripped = line.strip()
    return bool(
        stripped.startswith("return ")
        or stripped.startswith("local ")
        or stripped.startswith("function ")
        or stripped.startswith("if ")
        or stripped.startswith("for ")
        or stripped.startswith("while ")
        or stripped.startswith("_utils.")
        or "=" in stripped
    )


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)
