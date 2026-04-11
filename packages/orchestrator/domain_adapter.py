from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

RAW_LUA = "raw_lua"
JSON_WRAPPER = "json_wrapper"
PATCH_MODE = "patch_mode"
CLARIFICATION = "clarification"
_VALID_OUTPUT_MODES = {RAW_LUA, JSON_WRAPPER, PATCH_MODE, CLARIFICATION}
_ROOT_PATTERN = re.compile(r"wf\.(?:vars|initVariables)\.[A-Za-z0-9_\.]+")
_CODE_FENCE_PATTERN = re.compile(r"```(?:lua|json)?\s*(.*?)```", re.DOTALL)
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ARCHETYPE_REGISTRY_PATH = _REPO_ROOT / "packages" / "task-archetypes" / "registry.json"
_TEMPLATE_PACK_PATH = _REPO_ROOT / "knowledge" / "templates" / "domain_prompt_templates.json"


@dataclass(frozen=True)
class DomainPromptPackage:
    prompt: str
    archetype: str
    output_mode: str
    expected_result_format: str
    allowed_data_roots: tuple[str, ...]
    forbidden_patterns: tuple[str, ...]
    risk_tags: tuple[str, ...]
    clarification_required: bool


def build_domain_prompt_package(
    task_text: str,
    provided_context: str | None,
    *,
    archetype: str,
    output_mode: str,
    input_roots: list[str] | None = None,
    risk_tags: list[str] | None = None,
) -> DomainPromptPackage:
    archetypes = _load_json(_ARCHETYPE_REGISTRY_PATH)
    templates = _load_json(_TEMPLATE_PACK_PATH)

    if archetype not in archetypes:
        raise ValueError(f"Unknown task archetype: {archetype}")
    if output_mode not in _VALID_OUTPUT_MODES:
        raise ValueError(f"Unknown output mode: {output_mode}")

    archetype_config = archetypes[archetype]
    if output_mode != CLARIFICATION and output_mode not in archetype_config["allowed_output_modes"]:
        raise ValueError(f"Output mode {output_mode} is not allowed for archetype {archetype}.")

    normalized_roots, explicit_input_basis = _normalize_input_roots(provided_context, input_roots)
    clarification_required = _requires_clarification(normalized_roots, explicit_input_basis)
    effective_output_mode = CLARIFICATION if clarification_required else output_mode

    output_mode_rules = templates["output_modes"][effective_output_mode]
    common_rules = templates["common_rules"]
    forbidden_patterns = tuple(templates["forbidden_patterns"])
    risk_tags = tuple(risk_tags or ())

    prompt_sections = [
        "You are generating LocalScript-compatible Lua 5.5 for the luaMTS domain.",
        f"Task archetype: {archetype}",
        f"Output mode: {effective_output_mode}",
        f"Archetype description: {archetype_config['description']}",
        f"Expected result format: {output_mode_rules['expected_result_format']}",
        "Common rules:",
        _format_list(common_rules),
        "Mode-specific rules:",
        _format_list(output_mode_rules["rules"]),
        "Allowed data roots: " + (", ".join(normalized_roots) if normalized_roots else "none explicitly provided"),
        "Forbidden patterns:",
        _format_list(forbidden_patterns),
    ]

    if risk_tags:
        risk_hints = [
            templates["risk_hints"][risk_tag]
            for risk_tag in risk_tags
            if risk_tag in templates["risk_hints"]
        ]
        if risk_hints:
            prompt_sections.extend(["Risk hints:", _format_list(risk_hints)])

    if clarification_required:
        prompt_sections.extend(
            [
                "Clarification requirement:",
                "- The context references both wf.vars.* and wf.initVariables.* without explicit input_roots.",
                "- Ask one focused clarification question instead of generating code.",
            ]
        )

    prompt_sections.extend(["Task:", task_text])
    if provided_context:
        prompt_sections.extend(["Provided context:", provided_context])

    return DomainPromptPackage(
        prompt="\n".join(prompt_sections),
        archetype=archetype,
        output_mode=effective_output_mode,
        expected_result_format=output_mode_rules["expected_result_format"],
        allowed_data_roots=normalized_roots,
        forbidden_patterns=forbidden_patterns,
        risk_tags=risk_tags,
        clarification_required=clarification_required,
    )


def normalize_model_output(response_text: str, output_mode: str) -> str:
    if output_mode == RAW_LUA:
        return _normalize_raw_lua(response_text)
    if output_mode in {JSON_WRAPPER, PATCH_MODE}:
        return _normalize_json_payload(response_text)
    if output_mode == CLARIFICATION:
        return _normalize_clarification(response_text)
    raise ValueError(f"Unknown output mode: {output_mode}")


def _normalize_input_roots(
    provided_context: str | None,
    input_roots: list[str] | None,
) -> tuple[tuple[str, ...], bool]:
    if input_roots:
        return tuple(dict.fromkeys(root.strip() for root in input_roots if root and root.strip())), True

    if not provided_context:
        return tuple(), False

    inferred_roots = list(dict.fromkeys(_ROOT_PATTERN.findall(provided_context)))
    for root in _infer_roots_from_json_context(provided_context):
        if root not in inferred_roots:
            inferred_roots.append(root)
    return tuple(inferred_roots), False


def _infer_roots_from_json_context(provided_context: str) -> tuple[str, ...]:
    try:
        payload = json.loads(provided_context)
    except json.JSONDecodeError:
        return tuple()

    if not isinstance(payload, dict):
        return tuple()

    roots: list[str] = []
    wf_payload = payload.get("wf")
    if not isinstance(wf_payload, dict):
        return tuple()

    for root_name in ("vars", "initVariables"):
        root_payload = wf_payload.get(root_name)
        if root_payload is not None:
            roots.extend(_collect_leaf_roots(root_payload, f"wf.{root_name}"))

    return tuple(dict.fromkeys(roots))


def _collect_leaf_roots(node: Any, prefix: str) -> list[str]:
    if isinstance(node, dict):
        collected: list[str] = []
        for key, value in node.items():
            collected.extend(_collect_leaf_roots(value, f"{prefix}.{key}"))
        return collected
    return [prefix]


def _requires_clarification(roots: tuple[str, ...], explicit_input_basis: bool) -> bool:
    if explicit_input_basis or not roots:
        return False
    root_families = {_root_family(root) for root in roots if _root_family(root)}
    return len(root_families) > 1


def _root_family(root: str) -> str | None:
    if root.startswith("wf.vars."):
        return "wf.vars"
    if root.startswith("wf.initVariables."):
        return "wf.initVariables"
    return None


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


def _format_list(values: list[str] | tuple[str, ...]) -> str:
    return "\n".join(f"- {value}" for value in values)


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        return json.load(file)
