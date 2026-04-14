from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from packages.orchestrator.agent_prompt import AgentMessage, AgentPrompt
from packages.orchestrator.task_spec import TaskSpec
from packages.shared.language import normalize_language

_ROOT_PATTERN = re.compile(r"wf\.(?:vars|initVariables)\.[A-Za-z0-9_\.]+")


@dataclass(frozen=True)
class PlannerResult:
    task_spec: TaskSpec
    language: str
    input_roots: tuple[str, ...]
    explicit_input_basis: bool
    explicit_archetype: bool
    explicit_output_mode: bool
    task_intents: tuple[str, ...]
    clarification_required: bool
    execution_context: Any | None
    source: str = "deterministic"
    fallback_reason: str | None = None

    def to_debug_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "agent": "planner",
            "source": self.source,
            "language": self.language,
            "input_roots": list(self.input_roots),
            "explicit_input_basis": self.explicit_input_basis,
            "explicit_archetype": self.explicit_archetype,
            "explicit_output_mode": self.explicit_output_mode,
            "task_intents": list(self.task_intents),
            "clarification_required": self.clarification_required,
            "task_spec": self.task_spec.to_dict(),
        }
        if self.fallback_reason is not None:
            payload["fallback_reason"] = self.fallback_reason
        return payload


def plan_task(
    task_text: str,
    provided_context: str | None,
    *,
    language: str,
    archetype: str,
    output_mode: str,
    input_roots: list[str] | None,
    risk_tags: tuple[str, ...],
    explicit_archetype: bool = False,
    explicit_output_mode: bool = False,
) -> PlannerResult:
    normalized_language = normalize_language(language)
    normalized_roots, explicit_input_basis = _normalize_input_roots(provided_context, input_roots)
    task_spec = TaskSpec(
        task_text=task_text,
        language=normalized_language,
        archetype=archetype,
        operation="unresolved",
        output_mode=output_mode,
        input_roots=normalized_roots,
        expected_shape="unknown",
        risk_tags=risk_tags,
        edge_cases=tuple(),
        clarification_required=False,
        clarification_question=None,
    )
    return PlannerResult(
        task_spec=task_spec,
        language=normalized_language,
        input_roots=normalized_roots,
        explicit_input_basis=explicit_input_basis,
        explicit_archetype=explicit_archetype,
        explicit_output_mode=explicit_output_mode,
        task_intents=tuple(),
        clarification_required=False,
        execution_context=_parse_context_object(provided_context),
        source="deterministic_structural",
    )


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


def _parse_context_object(provided_context: str | None) -> Any | None:
    if not provided_context:
        return None

    try:
        return json.loads(provided_context)
    except json.JSONDecodeError:
        return None


def _compact_context_excerpt(provided_context: str | None, *, limit: int = 360) -> str:
    if not provided_context:
        return ""
    compact = re.sub(r"\s+", " ", provided_context).strip()
    return compact if len(compact) <= limit else compact[:limit] + "..."


def build_planner_agent_prompt(
    *,
    task_text: str,
    provided_context: str | None,
    fallback_result: PlannerResult,
) -> AgentPrompt:
    system_prompt = "\n".join(
        [
            "You are the planner agent for the luaMTS validation pipeline.",
            "Do not generate Lua code.",
            "Return one compact minified JSON object only.",
            "Use short keys to fit small num_predict budgets.",
            "Short key map: arch=archetype, op=operation, mode=output_mode, roots=input_roots, shape=expected_shape, risks=risk_tags, edges=edge_cases, intents=task_intents, clar=clarification_required, q=clarification_question.",
            "You are responsible for semantic choices: arch, op, mode, shape, intents, risks, and clar.",
            "Use the deterministic fallback only as structural evidence about language, context roots, and safe defaults.",
            "If explicit_archetype is true, preserve the given archetype. Otherwise choose the best archetype for the task.",
            "If explicit_output_mode is true, preserve the given output mode. Otherwise choose the best output mode for the task.",
            "Required compact JSON shape:",
            '{"arch":"simple_extraction","op":"last_array_item","mode":"raw_lua","roots":["wf.vars.emails"],"shape":"scalar_or_nil","risks":["array_indexing"],"edges":["single_item","empty_array"],"clar":false,"q":null,"intents":[]}',
        ]
    )
    user_prompt = "\n".join(
        [
            "Task:",
            task_text,
            "Context excerpt:",
            _compact_context_excerpt(provided_context),
            "Facts:",
            json.dumps(
                {
                    "lang": fallback_result.language,
                    "archetype": fallback_result.task_spec.archetype,
                    "mode": fallback_result.task_spec.output_mode,
                    "output_mode": fallback_result.task_spec.output_mode,
                    "roots": list(fallback_result.input_roots),
                    "explicit_roots": fallback_result.explicit_input_basis,
                    "explicit_input_basis": fallback_result.explicit_input_basis,
                    "explicit_archetype": fallback_result.explicit_archetype,
                    "explicit_output_mode": fallback_result.explicit_output_mode,
                    "risks": list(fallback_result.task_spec.risk_tags),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        ]
    )
    return AgentPrompt(
        agent_name="planner",
        messages=(
            AgentMessage(role="system", content=system_prompt),
            AgentMessage(role="user", content=user_prompt),
        ),
    )


def build_lowcode_planner_agent_prompt(
    *,
    task_text: str,
    provided_context: str | None,
    fallback_result: PlannerResult,
) -> AgentPrompt:
    system_prompt = "\n".join(
        [
            "Ты planner-агент validation pipeline для luaMTS.",
            "Не генерируй Lua-код.",
            "Верни только один компактный JSON object без markdown и пояснений.",
            "Используй короткие ключи, чтобы уложиться в небольшой num_predict budget.",
            "Карта ключей: arch=archetype, op=operation, mode=output_mode, roots=input_roots, shape=expected_shape, risks=risk_tags, edges=edge_cases, intents=task_intents, clar=clarification_required, q=clarification_question.",
            "Выбери семантику задачи: arch, op, mode, shape, intents, risks и clar.",
            "Детерминированный fallback используй только как факты о языке, корнях context и безопасных defaults.",
            "Если explicit_archetype=true, сохрани переданный archetype.",
            "Если explicit_output_mode=true, сохрани переданный output_mode.",
            "Форма ответа:",
            '{"arch":"simple_extraction","op":"last_array_item","mode":"raw_lua","roots":["wf.vars.emails"],"shape":"scalar_or_nil","risks":["array_indexing"],"edges":["single_item","empty_array"],"clar":false,"q":null,"intents":[]}',
        ]
    )
    user_prompt = "\n".join(
        [
            "Задача:",
            task_text,
            "Фрагмент контекста:",
            _compact_context_excerpt(provided_context),
            "Факты:",
            json.dumps(
                {
                    "lang": fallback_result.language,
                    "archetype": fallback_result.task_spec.archetype,
                    "mode": fallback_result.task_spec.output_mode,
                    "output_mode": fallback_result.task_spec.output_mode,
                    "roots": list(fallback_result.input_roots),
                    "explicit_roots": fallback_result.explicit_input_basis,
                    "explicit_input_basis": fallback_result.explicit_input_basis,
                    "explicit_archetype": fallback_result.explicit_archetype,
                    "explicit_output_mode": fallback_result.explicit_output_mode,
                    "risks": list(fallback_result.task_spec.risk_tags),
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        ]
    )
    return AgentPrompt(
        agent_name="planner",
        messages=(
            AgentMessage(role="system", content=system_prompt),
            AgentMessage(role="user", content=user_prompt),
        ),
    )


def apply_planner_agent_response(
    raw_response: str,
    fallback_result: PlannerResult,
    *,
    allowed_archetypes: tuple[str, ...] | None = None,
) -> PlannerResult:
    payload = _extract_json_payload(raw_response)
    source = "agent"
    fallback_reason = None
    if payload is None:
        payload = _extract_partial_planner_payload(raw_response)
        if payload is None:
            return _planner_fallback(fallback_result, "planner_invalid_json")
        source = "agent_partial"
        fallback_reason = "planner_truncated_json"
    payload = _normalize_planner_payload_aliases(payload)
    if not {"operation", "output_mode", "input_roots", "expected_shape"}.issubset(payload.keys()):
        return _planner_fallback(fallback_result, "planner_missing_schema")

    operation = _string_or(payload.get("operation"), fallback_result.task_spec.operation)
    expected_shape = _string_or(payload.get("expected_shape"), fallback_result.task_spec.expected_shape)
    output_mode = fallback_result.task_spec.output_mode
    if not fallback_result.explicit_output_mode:
        output_mode = _string_or(payload.get("output_mode"), output_mode)
        if output_mode not in {"raw_lua", "json_wrapper", "patch_mode", "clarification"}:
            output_mode = fallback_result.task_spec.output_mode

    archetype = fallback_result.task_spec.archetype
    if not fallback_result.explicit_archetype:
        archetype = _string_or(payload.get("archetype"), archetype)
        if allowed_archetypes and archetype not in allowed_archetypes:
            archetype = fallback_result.task_spec.archetype

    input_roots = _string_tuple(payload.get("input_roots"))
    if not input_roots or not all(_is_allowed_root(root) for root in input_roots):
        input_roots = fallback_result.input_roots

    risk_tags = _string_tuple(payload.get("risk_tags")) or fallback_result.task_spec.risk_tags
    edge_cases = _string_tuple(payload.get("edge_cases")) or fallback_result.task_spec.edge_cases
    task_intents = _string_tuple(payload.get("task_intents")) or fallback_result.task_intents
    clarification_required = _bool_or(payload.get("clarification_required"), fallback_result.clarification_required)
    clarification_question = payload.get("clarification_question")
    if not isinstance(clarification_question, str) or not clarification_question.strip():
        clarification_question = fallback_result.task_spec.clarification_question
    if clarification_required:
        output_mode = "clarification"

    task_spec = TaskSpec(
        task_text=fallback_result.task_spec.task_text,
        language=fallback_result.language,
        archetype=archetype,
        operation=operation,
        output_mode=output_mode,
        input_roots=input_roots,
        expected_shape=expected_shape,
        risk_tags=risk_tags,
        edge_cases=edge_cases,
        clarification_required=clarification_required,
        clarification_question=clarification_question if clarification_required else None,
    )
    return PlannerResult(
        task_spec=task_spec,
        language=fallback_result.language,
        input_roots=input_roots,
        explicit_input_basis=fallback_result.explicit_input_basis,
        explicit_archetype=fallback_result.explicit_archetype,
        explicit_output_mode=fallback_result.explicit_output_mode,
        task_intents=task_intents,
        clarification_required=clarification_required,
        execution_context=fallback_result.execution_context,
        source=source,
        fallback_reason=fallback_reason,
    )


def _planner_fallback(fallback_result: PlannerResult, reason: str) -> PlannerResult:
    return PlannerResult(
        task_spec=fallback_result.task_spec,
        language=fallback_result.language,
        input_roots=fallback_result.input_roots,
        explicit_input_basis=fallback_result.explicit_input_basis,
        explicit_archetype=fallback_result.explicit_archetype,
        explicit_output_mode=fallback_result.explicit_output_mode,
        task_intents=fallback_result.task_intents,
        clarification_required=fallback_result.clarification_required,
        execution_context=fallback_result.execution_context,
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


def _extract_partial_planner_payload(raw_response: str) -> dict[str, Any] | None:
    payload: dict[str, Any] = {}
    for key in ("archetype", "operation", "output_mode", "expected_shape", "clarification_question", "arch", "op", "mode", "shape", "q"):
        value = _extract_partial_string_field(raw_response, key)
        if value is not None:
            payload[key] = value

    for key in ("input_roots", "risk_tags", "edge_cases", "task_intents", "roots", "risks", "edges", "intents"):
        values = _extract_partial_string_list_field(raw_response, key)
        if values:
            payload[key] = values

    clarification_required = _extract_partial_bool_field(raw_response, "clarification_required")
    if clarification_required is None:
        clarification_required = _extract_partial_bool_field(raw_response, "clar")
    if clarification_required is not None:
        payload["clarification_required"] = clarification_required

    payload = _normalize_planner_payload_aliases(payload)
    if {"operation", "output_mode", "input_roots", "expected_shape"}.issubset(payload.keys()):
        return payload
    return None


def _normalize_planner_payload_aliases(payload: dict[str, Any]) -> dict[str, Any]:
    alias_map = {
        "arch": "archetype",
        "op": "operation",
        "mode": "output_mode",
        "roots": "input_roots",
        "shape": "expected_shape",
        "risks": "risk_tags",
        "edges": "edge_cases",
        "intents": "task_intents",
        "clar": "clarification_required",
        "q": "clarification_question",
    }
    normalized = dict(payload)
    for alias, canonical in alias_map.items():
        if canonical not in normalized and alias in normalized:
            normalized[canonical] = normalized[alias]
    return normalized


def _extract_partial_string_field(raw_response: str, key: str) -> str | None:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*"(?P<value>(?:[^"\\]|\\.)*)"', raw_response)
    if not match:
        return None
    try:
        value = json.loads(f'"{match.group("value")}"')
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, str) and value.strip() else None


def _extract_partial_string_list_field(raw_response: str, key: str) -> list[str]:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*\[(?P<body>[^\]]*)\]', raw_response)
    if not match:
        return []

    values: list[str] = []
    for value_match in re.finditer(r'"(?P<value>(?:[^"\\]|\\.)*)"', match.group("body")):
        try:
            value = json.loads(f'"{value_match.group("value")}"')
        except json.JSONDecodeError:
            continue
        if isinstance(value, str) and value.strip() and value not in values:
            values.append(value)
    return values


def _extract_partial_bool_field(raw_response: str, key: str) -> bool | None:
    match = re.search(rf'"{re.escape(key)}"\s*:\s*(?P<value>true|false)', raw_response)
    if not match:
        return None
    return match.group("value") == "true"


def _string_or(value: object, fallback: str) -> str:
    return value.strip() if isinstance(value, str) and value.strip() else fallback


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return tuple()
    return tuple(dict.fromkeys(item.strip() for item in value if isinstance(item, str) and item.strip()))


def _bool_or(value: object, fallback: bool) -> bool:
    return value if isinstance(value, bool) else fallback


def _is_allowed_root(root: str) -> bool:
    return root.startswith("wf.vars.") or root.startswith("wf.initVariables.")
