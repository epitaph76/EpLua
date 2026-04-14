from __future__ import annotations

from dataclasses import dataclass

from packages.shared.language import DEFAULT_LANGUAGE, normalize_language

RAW_LUA = "raw_lua"
JSON_WRAPPER = "json_wrapper"
PATCH_MODE = "patch_mode"
CLARIFICATION = "clarification"


@dataclass(frozen=True)
class TaskSpec:
    task_text: str
    language: str
    archetype: str
    operation: str
    output_mode: str
    input_roots: tuple[str, ...]
    expected_shape: str
    risk_tags: tuple[str, ...]
    edge_cases: tuple[str, ...]
    clarification_required: bool
    clarification_question: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "task_text": self.task_text,
            "language": self.language,
            "archetype": self.archetype,
            "operation": self.operation,
            "output_mode": self.output_mode,
            "input_roots": list(self.input_roots),
            "expected_shape": self.expected_shape,
            "risk_tags": list(self.risk_tags),
            "edge_cases": list(self.edge_cases),
            "clarification_required": self.clarification_required,
            "clarification_question": self.clarification_question,
        }


def build_task_spec(
    task_text: str,
    *,
    language: str = DEFAULT_LANGUAGE,
    archetype: str,
    output_mode: str,
    input_roots: tuple[str, ...] = (),
    risk_tags: tuple[str, ...] = (),
    clarification_required: bool = False,
) -> TaskSpec:
    normalized_language = normalize_language(language)
    normalized_input_roots = tuple(dict.fromkeys(input_roots))
    normalized_risk_tags = tuple(dict.fromkeys(risk_tags))
    operation = _resolve_operation(task_text, archetype=archetype, output_mode=output_mode)
    expected_shape = _resolve_expected_shape(archetype=archetype, operation=operation, output_mode=output_mode)
    edge_cases = _resolve_edge_cases(operation=operation, risk_tags=normalized_risk_tags)
    clarification_question = _default_clarification_question(normalized_language) if clarification_required else None

    return TaskSpec(
        task_text=task_text,
        language=normalized_language,
        archetype=archetype,
        operation=operation,
        output_mode=output_mode,
        input_roots=normalized_input_roots,
        expected_shape=expected_shape,
        risk_tags=normalized_risk_tags,
        edge_cases=edge_cases,
        clarification_required=clarification_required,
        clarification_question=clarification_question,
    )


def _resolve_operation(task_text: str, *, archetype: str, output_mode: str) -> str:
    lowered = task_text.lower()
    if archetype == "simple_extraction":
        if "послед" in lowered or "last" in lowered:
            return "last_array_item"
        if "перв" in lowered or "first" in lowered:
            return "first_array_item"
        return "direct_extraction"

    if archetype == "filtering":
        return "array_filter"
    if archetype == "datetime_conversion":
        return "datetime_conversion"
    if output_mode == PATCH_MODE:
        return "additive_patch"
    return f"{archetype}_default"


def _resolve_expected_shape(*, archetype: str, operation: str, output_mode: str) -> str:
    if output_mode == CLARIFICATION:
        return "clarification_question"
    if output_mode == PATCH_MODE:
        return "json_object_patch"
    if output_mode == JSON_WRAPPER:
        return "json_object_with_wrapped_code"
    if archetype == "simple_extraction" and operation in {"last_array_item", "first_array_item", "direct_extraction"}:
        return "scalar_or_nil"
    if archetype == "filtering":
        return "array"
    return "lua_value"


def _resolve_edge_cases(*, operation: str, risk_tags: tuple[str, ...]) -> tuple[str, ...]:
    edge_cases: list[str] = []
    if operation in {"last_array_item", "first_array_item"}:
        edge_cases.extend(["single_item", "empty_array"])
    if "nil_handling" in risk_tags:
        edge_cases.append("nil_input")
    return tuple(dict.fromkeys(edge_cases))


def _default_clarification_question(language: str) -> str:
    if language == "ru":
        return "Какой источник данных использовать: wf.vars.* или wf.initVariables.*?"
    return "Which data root should be used: wf.vars.* or wf.initVariables.*?"
