import json
import re
from pathlib import Path

from packages.orchestrator.agent_prompt import AgentPrompt
from packages.orchestrator import repair_loop
from packages.orchestrator.domain_adapter import build_domain_prompt_package
from packages.orchestrator.planner import apply_planner_agent_response, plan_task
from packages.orchestrator.repair_loop import _detect_repair_oscillation, run_quality_loop
from packages.shared.language import DEFAULT_LANGUAGE
from packages.shared.quality import ValidatorReport
from services import generation as generation_module
from services.generation import GenerationService


SEMANTIC_PASS_RESPONSE = '{"status":"pass","message":"Semantic validation passed."}'


class ScriptedModelAdapter:
    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self.prompts: list[str] = []
        self.agent_calls: list[dict[str, object]] = []

    def generate_from_prompt(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self._next_response()

    def generate_from_agent(self, agent_prompt: AgentPrompt) -> str:
        self.agent_calls.append(
            {
                "agent": agent_prompt.agent_name,
                "messages": agent_prompt.to_messages_payload(),
                "legacy_prompt": agent_prompt.to_legacy_prompt(),
            }
        )
        if agent_prompt.agent_name == "planner":
            return _default_planner_response(agent_prompt)
        if agent_prompt.agent_name == "prompter":
            return "not-json"
        return self._next_response()

    def _next_response(self) -> str:
        if not self._responses:
            raise AssertionError("No scripted responses left for the model adapter.")
        return self._responses.pop(0)


class LegacyQualityLoopService:
    def __init__(self, model_adapter: ScriptedModelAdapter) -> None:
        self._model_adapter = model_adapter

    def generate(
        self,
        task_text: str,
        provided_context: str | None = None,
        *,
        archetype: str | None = None,
        output_mode: str | None = None,
        input_roots: list[str] | None = None,
        risk_tags: list[str] | None = None,
        debug: bool = False,
        language: str = DEFAULT_LANGUAGE,
        **_: object,
    ) -> dict[str, object]:
        agent_runner = getattr(self._model_adapter, "generate_from_agent", None)
        prompt_package = build_domain_prompt_package(
            task_text,
            provided_context,
            archetype=archetype,
            output_mode=output_mode,
            input_roots=input_roots,
            risk_tags=risk_tags,
            language=language,
            agent_runner=agent_runner if callable(agent_runner) else None,
        )
        return run_quality_loop(self._model_adapter, prompt_package, debug=debug).to_dict()


class AgenticPromptModelAdapter(ScriptedModelAdapter):
    def generate_from_agent(self, agent_prompt: AgentPrompt) -> str:
        self.agent_calls.append(
            {
                "agent": agent_prompt.agent_name,
                "messages": agent_prompt.to_messages_payload(),
                "legacy_prompt": agent_prompt.to_legacy_prompt(),
            }
        )
        if agent_prompt.agent_name == "planner":
            return json.dumps(
                {
                    "operation": "last_array_item",
                    "output_mode": "raw_lua",
                    "input_roots": ["wf.vars.emails"],
                    "expected_shape": "scalar_or_nil",
                    "risk_tags": ["array_indexing", "empty_array"],
                    "edge_cases": ["single_item", "empty_array"],
                    "clarification_required": False,
                    "clarification_question": None,
                    "task_intents": [],
                }
            )
        if agent_prompt.agent_name == "prompter":
            return json.dumps(
                {
                    "system_message": "TaskSpec\nAGENT PROMPTER SYSTEM",
                    "user_message": "TaskSpec\nAGENT PROMPTER USER",
                }
            )
        return self._next_response()


class PlannerOwnedMetadataModelAdapter(ScriptedModelAdapter):
    def generate_from_agent(self, agent_prompt: AgentPrompt) -> str:
        self.agent_calls.append(
            {
                "agent": agent_prompt.agent_name,
                "messages": agent_prompt.to_messages_payload(),
                "legacy_prompt": agent_prompt.to_legacy_prompt(),
            }
        )
        if agent_prompt.agent_name == "planner":
            return json.dumps(
                {
                    "archetype": "simple_extraction",
                    "operation": "last_array_item",
                    "output_mode": "raw_lua",
                    "input_roots": ["wf.vars.emails"],
                    "expected_shape": "scalar_or_nil",
                    "risk_tags": ["array_indexing", "empty_array"],
                    "edge_cases": ["single_item", "empty_array"],
                    "clarification_required": False,
                    "clarification_question": None,
                    "task_intents": [],
                }
            )
        if agent_prompt.agent_name == "prompter":
            return json.dumps(
                {
                    "system_message": "TaskSpec\nAGENT PROMPTER SYSTEM",
                    "user_message": "TaskSpec\nAGENT PROMPTER USER",
                }
            )
        return self._next_response()


class EmptyPlannerPrompterModelAdapter(ScriptedModelAdapter):
    def generate_from_agent(self, agent_prompt: AgentPrompt) -> str:
        self.agent_calls.append(
            {
                "agent": agent_prompt.agent_name,
                "messages": agent_prompt.to_messages_payload(),
                "legacy_prompt": agent_prompt.to_legacy_prompt(),
            }
        )
        if agent_prompt.agent_name in {"planner", "prompter"}:
            return ""
        return self._next_response()


class TruncatedPlannerModelAdapter(ScriptedModelAdapter):
    def generate_from_agent(self, agent_prompt: AgentPrompt) -> str:
        self.agent_calls.append(
            {
                "agent": agent_prompt.agent_name,
                "messages": agent_prompt.to_messages_payload(),
                "legacy_prompt": agent_prompt.to_legacy_prompt(),
            }
        )
        if agent_prompt.agent_name == "planner":
            return (
                '{"operation":"last_array_item","output_mode":"raw_lua",'
                '"input_roots":["wf.vars.emails"],"expected_shape":"scalar_or_nil",'
                '"risk_tags":["array_indexing"],"edge_cases":["single_item","empty_array"],"clar'
            )
        if agent_prompt.agent_name == "prompter":
            return ""
        return self._next_response()


class CompactAgentProtocolModelAdapter(ScriptedModelAdapter):
    def generate_from_agent(self, agent_prompt: AgentPrompt) -> str:
        self.agent_calls.append(
            {
                "agent": agent_prompt.agent_name,
                "messages": agent_prompt.to_messages_payload(),
                "legacy_prompt": agent_prompt.to_legacy_prompt(),
            }
        )
        if agent_prompt.agent_name == "planner":
            return (
                '{"op":"last_array_item","mode":"raw_lua","roots":["wf.vars.emails"],'
                '"shape":"scalar_or_nil","risks":["array_indexing"],"edges":["single_item","empty_array"],'
                '"clar":false,"intents":[]}'
            )
        if agent_prompt.agent_name == "prompter":
            return '{"sys":["Return the last array item, not the whole array."],"user":["Use wf.vars.emails[#wf.vars.emails]."]}'
        return self._next_response()


class StructuredQuestionPlannerModelAdapter(ScriptedModelAdapter):
    def generate_from_agent(self, agent_prompt: AgentPrompt) -> str:
        self.agent_calls.append(
            {
                "agent": agent_prompt.agent_name,
                "messages": agent_prompt.to_messages_payload(),
                "legacy_prompt": agent_prompt.to_legacy_prompt(),
            }
        )
        if agent_prompt.agent_name == "clarifier":
            return '{"clar":false,"questions":[]}'
        if agent_prompt.agent_name == "planner":
            return json.dumps(
                {
                    "arch": "datetime_conversion",
                    "op": "datetime_formatting",
                    "mode": "clarification",
                    "roots": ["wf.vars.date", "wf.vars.time"],
                    "shape": "clarification_question",
                    "risks": ["invalid_date", "invalid_time"],
                    "edges": ["invalid_format"],
                    "clar": True,
                    "questions": [
                        {
                            "id": "invalid_datetime_behavior",
                            "question": "Что вернуть, если дата или время некорректны?",
                            "options": [
                                {"id": "empty_string", "label": "пустую строку", "description": ""},
                                {"id": "nil", "label": "nil", "description": ""},
                            ],
                            "default_option_id": "empty_string",
                        }
                    ],
                    "intents": ["datetime_conversion"],
                },
                ensure_ascii=False,
            )
        raise AssertionError(f"unexpected agent call: {agent_prompt.agent_name}")


class ClarifierQuestionModelAdapter(ScriptedModelAdapter):
    def generate_from_agent(self, agent_prompt: AgentPrompt) -> str:
        self.agent_calls.append(
            {
                "agent": agent_prompt.agent_name,
                "messages": agent_prompt.to_messages_payload(),
                "legacy_prompt": agent_prompt.to_legacy_prompt(),
            }
        )
        if agent_prompt.agent_name == "clarifier":
            return json.dumps(
                {
                    "clar": True,
                    "questions": [
                        {
                            "id": "empty_array_behavior",
                            "question": "Что вернуть, если список email пустой?",
                            "options": [
                                {"id": "nil", "label": "nil", "description": ""},
                                {"id": "empty_string", "label": "пустую строку", "description": ""},
                            ],
                            "default_option_id": "nil",
                        }
                    ],
                },
                ensure_ascii=False,
            )
        if agent_prompt.agent_name == "planner":
            return json.dumps(
                {
                    "arch": "simple_extraction",
                    "op": "last_array_item",
                    "mode": "lowcode_json",
                    "roots": ["wf.vars.emails"],
                    "shape": "scalar_or_nil",
                    "risks": ["array_indexing"],
                    "edges": ["empty_array"],
                    "clar": False,
                    "questions": [],
                    "intents": ["extract_last_email"],
                },
                ensure_ascii=False,
            )
        raise AssertionError(f"unexpected agent call: {agent_prompt.agent_name}")


class RussianPrompterPatchModelAdapter(ScriptedModelAdapter):
    def generate_from_agent(self, agent_prompt: AgentPrompt) -> str:
        self.agent_calls.append(
            {
                "agent": agent_prompt.agent_name,
                "messages": agent_prompt.to_messages_payload(),
                "legacy_prompt": agent_prompt.to_legacy_prompt(),
            }
        )
        if agent_prompt.agent_name == "planner":
            return json.dumps(
                {
                    "arch": "simple_extraction",
                    "op": "last_array_item",
                    "mode": "raw_lua",
                    "roots": ["wf.vars.emails"],
                    "shape": "scalar_or_nil",
                    "risks": ["array_indexing", "empty_array"],
                    "edges": ["single_item", "empty_array"],
                    "clar": False,
                    "q": None,
                    "intents": [],
                },
                ensure_ascii=False,
            )
        if agent_prompt.agent_name == "prompter":
            return json.dumps(
                {
                    "sys": ["Учитывай TaskSpec: нужно вернуть последний элемент массива, а не весь массив."],
                    "user": ["Используй Lua-индексацию wf.vars.emails[#wf.vars.emails]."],
                },
                ensure_ascii=False,
            )
        if agent_prompt.agent_name == "semantic_critic":
            return SEMANTIC_PASS_RESPONSE
        if agent_prompt.agent_name == "assisted_repair_summarizer":
            return ""
        return self._next_response()


class AgenticAssistedRepairSummarizerModelAdapter(RussianPrompterPatchModelAdapter):
    def generate_from_agent(self, agent_prompt: AgentPrompt) -> str:
        self.agent_calls.append(
            {
                "agent": agent_prompt.agent_name,
                "messages": agent_prompt.to_messages_payload(),
                "legacy_prompt": agent_prompt.to_legacy_prompt(),
            }
        )
        if agent_prompt.agent_name == "planner":
            return json.dumps(
                {
                    "arch": "simple_extraction",
                    "op": "last_array_item",
                    "mode": "raw_lua",
                    "roots": ["wf.vars.emails"],
                    "shape": "scalar_or_nil",
                    "risks": ["array_indexing", "empty_array"],
                    "edges": ["single_item", "empty_array"],
                    "clar": False,
                    "q": None,
                    "intents": [],
                },
                ensure_ascii=False,
            )
        if agent_prompt.agent_name == "prompter":
            return json.dumps(
                {
                    "sys": ["Учитывай TaskSpec: нужно вернуть последний элемент массива, а не весь массив."],
                    "user": ["Используй Lua-индексацию wf.vars.emails[#wf.vars.emails]."],
                },
                ensure_ascii=False,
            )
        if agent_prompt.agent_name == "semantic_critic":
            return SEMANTIC_PASS_RESPONSE
        if agent_prompt.agent_name == "assisted_repair_summarizer":
            return json.dumps(
                {
                    "summary": "Код всё ещё приходит с markdown-ограждениями вместо чистого raw_lua.",
                    "options": [
                        {
                            "id": "return_plain_output",
                            "label": "Убрать markdown",
                            "effect": "Вернуть только raw_lua без markdown и пояснений.",
                        },
                        {
                            "id": "simplify_result",
                            "label": "Упростить результат",
                            "effect": "Сохранить цель пользователя, но выбрать более простую форму результата.",
                        },
                        {
                            "id": "custom",
                            "label": "Свой вариант",
                            "effect": "Пользователь вводит свою инструкцию для следующей итерации.",
                        },
                    ],
                },
                ensure_ascii=False,
            )
        raise AssertionError(f"unexpected agent call: {agent_prompt.agent_name}")


class TruncatedGeneratorContinuationModelAdapter(RussianPrompterPatchModelAdapter):
    def __init__(self, payloads: list[dict[str, object]]) -> None:
        super().__init__([])
        self._payloads = payloads

    def generate_from_prompt_with_metadata(self, prompt: str) -> dict[str, object]:
        self.prompts.append(prompt)
        if not self._payloads:
            raise AssertionError("No scripted metadata payloads left for the model adapter.")
        return self._payloads.pop(0)


class TransformationArrayItemPlannerModelAdapter(ScriptedModelAdapter):
    def generate_from_agent(self, agent_prompt: AgentPrompt) -> str:
        self.agent_calls.append(
            {
                "agent": agent_prompt.agent_name,
                "messages": agent_prompt.to_messages_payload(),
                "legacy_prompt": agent_prompt.to_legacy_prompt(),
            }
        )
        if agent_prompt.agent_name == "planner":
            return (
                '{"arch":"transformation","op":"last_array_item","mode":"raw_lua","roots":["wf.vars.emails"],'
                '"shape":"scalar_or_nil","risks":["array_indexing"],"edges":["single_item","empty_array"],'
                '"clar":false,"intents":["extract_last_email"]}'
            )
        if agent_prompt.agent_name == "prompter":
            return '{"sys":["Return the last array item."],"user":["Use wf.vars.emails[#wf.vars.emails]."]}'
        if agent_prompt.agent_name == "semantic_critic":
            return SEMANTIC_PASS_RESPONSE
        return self._next_response()


class EmptyPlannerPatchPrompterModelAdapter(ScriptedModelAdapter):
    def generate_from_agent(self, agent_prompt: AgentPrompt) -> str:
        self.agent_calls.append(
            {
                "agent": agent_prompt.agent_name,
                "messages": agent_prompt.to_messages_payload(),
                "legacy_prompt": agent_prompt.to_legacy_prompt(),
            }
        )
        if agent_prompt.agent_name == "planner":
            return ""
        if agent_prompt.agent_name == "prompter":
            return '{"sys":["Return the last array item, not the whole array."],"user":["Use wf.vars.emails[#wf.vars.emails]."]}'
        return self._next_response()


def _default_planner_response(agent_prompt: AgentPrompt) -> str:
    user_message = agent_prompt.messages[1].content
    lowered = user_message.lower()
    roots = tuple(dict.fromkeys(re.findall(r"wf\.(?:vars|initVariables)\.[A-Za-z0-9_\.]+", user_message)))
    output_mode = _planned_output_mode(lowered)
    clarification_required = (
        '"explicit_input_basis":false' in lowered
        and any(root.startswith("wf.vars.") for root in roots)
        and any(root.startswith("wf.initVariables.") for root in roots)
    )
    operation = _planned_operation(lowered, output_mode)
    expected_shape = _planned_expected_shape(operation, output_mode)
    edge_cases = ["single_item", "empty_array"] if operation in {"last_array_item", "first_array_item"} else []
    task_intents = _planned_task_intents(lowered)
    return json.dumps(
        {
            "operation": operation,
            "output_mode": "clarification" if clarification_required else output_mode,
            "input_roots": list(roots),
            "expected_shape": "clarification_question" if clarification_required else expected_shape,
            "risk_tags": [],
            "edge_cases": edge_cases,
            "clarification_required": clarification_required,
            "clarification_question": (
                "Which data root should I use: wf.vars.emails or wf.initVariables.recallTime?"
                if clarification_required
                else None
            ),
            "task_intents": task_intents,
        }
    )


def _planned_output_mode(lowered_prompt: str) -> str:
    if '"output_mode":"lowcode_json"' in lowered_prompt:
        return "lowcode_json"
    if '"output_mode":"patch_mode"' in lowered_prompt:
        return "patch_mode"
    if '"output_mode":"json_wrapper"' in lowered_prompt:
        return "json_wrapper"
    if '"output_mode":"clarification"' in lowered_prompt:
        return "clarification"
    return "raw_lua"


def _planned_operation(lowered_prompt: str, output_mode: str) -> str:
    if "послед" in lowered_prompt or "last" in lowered_prompt:
        return "last_array_item"
    if "перв" in lowered_prompt or "first" in lowered_prompt:
        return "first_array_item"
    if "datetime_conversion" in lowered_prompt:
        return "datetime_conversion"
    if output_mode == "patch_mode":
        return "additive_patch"
    if "filtering" in lowered_prompt:
        return "array_filter"
    return "direct_extraction"


def _planned_expected_shape(operation: str, output_mode: str) -> str:
    if output_mode == "patch_mode":
        return "json_object_patch"
    if output_mode == "json_wrapper":
        return "json_object_with_wrapped_code"
    if operation in {"last_array_item", "first_array_item", "direct_extraction"}:
        return "scalar_or_nil"
    if operation == "array_filter":
        return "array"
    return "lua_value"


def _planned_task_intents(lowered_prompt: str) -> list[str]:
    intents: list[str] = []
    if "очист" in lowered_prompt or "clear " in lowered_prompt:
        intents.append("clear_target_fields")
    if "удали" in lowered_prompt or "remove" in lowered_prompt:
        intents.append("remove_target_fields")
    if "оставь только" in lowered_prompt or "keep only" in lowered_prompt:
        intents.append("keep_only_target_fields")
    if "не трогай" in lowered_prompt or "остальные поля" in lowered_prompt or "preserve untouched" in lowered_prompt:
        intents.append("preserve_untouched_fields")
    if "в существующ" in lowered_prompt or "in place" in lowered_prompt:
        intents.append("mutate_in_place")
    return list(dict.fromkeys(intents))


def test_planner_agent_partial_response_falls_back_to_structural_plan() -> None:
    fallback = plan_task(
        "Get the last email from the list.",
        '{"wf":{"vars":{"emails":["user@example.com"]}}}',
        language="en",
        archetype="simple_extraction",
        output_mode="raw_lua",
        input_roots=["wf.vars.emails"],
        risk_tags=("array_indexing",),
    )

    result = apply_planner_agent_response('{"operation":"last_array_item"}', fallback)

    assert result.source == "deterministic_fallback"
    assert result.fallback_reason == "planner_missing_schema"
    assert result.task_spec.operation == "unresolved"
    assert result.task_spec.expected_shape == "unknown"


def test_planner_agent_salvages_truncated_response_when_core_fields_are_present() -> None:
    fallback = plan_task(
        "Get the last email from the list.",
        '{"wf":{"vars":{"emails":["user@example.com"]}}}',
        language="en",
        archetype="simple_extraction",
        output_mode="raw_lua",
        input_roots=["wf.vars.emails"],
        risk_tags=("array_indexing",),
    )

    result = apply_planner_agent_response(
        (
            '{"operation":"last_array_item","output_mode":"raw_lua",'
            '"input_roots":["wf.vars.emails"],"expected_shape":"scalar_or_nil",'
            '"risk_tags":["array_indexing"],"edge_cases":["single_item","empty_array"],"clar'
        ),
        fallback,
    )

    assert result.source == "agent_partial"
    assert result.fallback_reason == "planner_truncated_json"
    assert result.task_spec.operation == "last_array_item"
    assert result.task_spec.expected_shape == "scalar_or_nil"
    assert result.task_spec.edge_cases == ("single_item", "empty_array")


def test_planner_agent_accepts_compact_response_keys() -> None:
    fallback = plan_task(
        "Get the last email from the list.",
        '{"wf":{"vars":{"emails":["user@example.com"]}}}',
        language="en",
        archetype="simple_extraction",
        output_mode="raw_lua",
        input_roots=["wf.vars.emails"],
        risk_tags=("array_indexing",),
    )

    result = apply_planner_agent_response(
        '{"op":"last_array_item","mode":"raw_lua","roots":["wf.vars.emails"],"shape":"scalar_or_nil","edges":["empty_array"],"clar":false}',
        fallback,
    )

    assert result.source == "agent"
    assert result.task_spec.operation == "last_array_item"
    assert result.task_spec.output_mode == "raw_lua"
    assert result.task_spec.input_roots == ("wf.vars.emails",)
    assert result.task_spec.expected_shape == "scalar_or_nil"
    assert result.task_spec.edge_cases == ("empty_array",)


def test_planner_agent_accepts_lowcode_json_output_mode() -> None:
    fallback = plan_task(
        "Get the last email from the list.",
        '{"wf":{"vars":{"emails":["user@example.com"]}}}',
        language="en",
        archetype="simple_extraction",
        output_mode="raw_lua",
        input_roots=["wf.vars.emails"],
        risk_tags=("array_indexing",),
    )

    result = apply_planner_agent_response(
        '{"op":"last_array_item","mode":"lowcode_json","roots":["wf.vars.emails"],"shape":"scalar_or_nil","edges":["empty_array"],"clar":false}',
        fallback,
    )

    assert result.source == "agent"
    assert result.task_spec.operation == "last_array_item"
    assert result.task_spec.output_mode == "lowcode_json"
    assert result.task_spec.input_roots == ("wf.vars.emails",)
    assert result.task_spec.expected_shape == "scalar_or_nil"
    assert result.task_spec.edge_cases == ("empty_array",)


def test_planner_agent_accepts_structured_questions() -> None:
    fallback = plan_task(
        "Преобразуй дату и время в ISO 8601.",
        '{"wf":{"vars":{"date":"2026-04-14","time":"10:11:12"}}}',
        language="ru",
        archetype="datetime_conversion",
        output_mode="raw_lua",
        input_roots=["wf.vars.date", "wf.vars.time"],
        risk_tags=("invalid_date",),
    )

    result = apply_planner_agent_response(
        json.dumps(
            {
                "arch": "datetime_conversion",
                "op": "datetime_formatting",
                "mode": "clarification",
                "roots": ["wf.vars.date", "wf.vars.time"],
                "shape": "clarification_question",
                "risks": ["invalid_date", "invalid_time"],
                "edges": ["invalid_format"],
                "clar": True,
                "questions": [
                    {
                        "id": "invalid_datetime_behavior",
                        "question": "Что вернуть, если дата или время некорректны?",
                        "options": [
                            {"id": "empty_string", "label": "пустую строку", "description": ""},
                            {"id": "nil", "label": "nil", "description": ""},
                        ],
                        "default_option_id": "empty_string",
                    }
                ],
                "intents": ["datetime_conversion"],
            },
            ensure_ascii=False,
        ),
        fallback,
    )

    assert result.source == "agent"
    assert result.task_spec.output_mode == "clarification"
    assert result.task_spec.clarification_required is True
    assert result.task_spec.clarification_questions == (
        {
            "id": "invalid_datetime_behavior",
            "question": "Что вернуть, если дата или время некорректны?",
            "options": (
                {"id": "empty_string", "label": "пустую строку", "description": ""},
                {"id": "nil", "label": "nil", "description": ""},
            ),
            "default_option_id": "empty_string",
        },
    )


def test_generation_service_plan_returns_questions_without_generator_call() -> None:
    model_adapter = StructuredQuestionPlannerModelAdapter([])
    service = GenerationService(model_adapter=model_adapter)

    result = service.plan(
        task_text="Преобразуй DATUM и TIME в ISO 8601.",
        provided_context=json.dumps({"wf": {"vars": {"date": "2026-04-14", "time": "10:11:12"}}}),
        debug=True,
    )

    assert result["trace"] == ["request_received", "clarifier", "planner", "response_ready"]
    assert result["clarification_required"] is True
    assert result["questions"] == [
        {
            "id": "invalid_datetime_behavior",
            "question": "Что вернуть, если дата или время некорректны?",
            "options": [
                {"id": "empty_string", "label": "пустую строку", "description": ""},
                {"id": "nil", "label": "nil", "description": ""},
            ],
            "default_option_id": "empty_string",
        }
    ]
    assert result["task_spec"]["clarification_questions"] == [
        {
            "id": "invalid_datetime_behavior",
            "question": "Что вернуть, если дата или время некорректны?",
            "options": [
                {"id": "empty_string", "label": "пустую строку", "description": ""},
                {"id": "nil", "label": "nil", "description": ""},
            ],
            "default_option_id": "empty_string",
        }
    ]
    assert model_adapter.prompts == []
    assert [call["agent"] for call in model_adapter.agent_calls] == ["clarifier", "planner"]
    assert result["debug"]["clarifier_result"]["clarification_required"] is False
    assert result["debug"]["planner_result"]["clarification_required"] is True


def test_generation_service_plan_uses_clarifier_agent_for_targeted_questions() -> None:
    model_adapter = ClarifierQuestionModelAdapter([])
    service = GenerationService(model_adapter=model_adapter)

    result = service.plan(
        task_text="Из полученного списка email получи последний.",
        provided_context=json.dumps({"wf": {"vars": {"emails": ["a@example.com", "b@example.com"]}}}),
        debug=True,
    )

    assert result["trace"] == ["request_received", "clarifier", "planner", "response_ready"]
    assert result["clarification_required"] is True
    assert result["questions"] == [
        {
            "id": "empty_array_behavior",
            "question": "Что вернуть, если список email пустой?",
            "options": [
                {"id": "nil", "label": "nil", "description": ""},
                {"id": "empty_string", "label": "пустую строку", "description": ""},
            ],
            "default_option_id": "nil",
        }
    ]
    assert result["task_spec"]["operation"] == "last_array_item"
    assert result["task_spec"]["clarification_required"] is True
    assert result["task_spec"]["clarification_question"] == "Что вернуть, если список email пустой?"
    assert result["task_spec"]["clarification_questions"] == result["questions"]
    assert [call["agent"] for call in model_adapter.agent_calls] == ["clarifier", "planner"]
    assert result["debug"]["clarifier_result"]["source"] == "agent"
    assert result["debug"]["planner_result"]["task_spec"]["operation"] == "last_array_item"


def test_generation_service_feedback_rerun_marks_trace_and_replans() -> None:
    model_adapter = RussianPrompterPatchModelAdapter(
        [
            '{"result":"lua{return wf.vars.emails[#wf.vars.emails]}lua"}',
        ]
    )
    service = GenerationService(model_adapter=model_adapter)

    result = service.generate(
        task_text="Get the last email from the list.",
        provided_context=json.dumps({"wf": {"vars": {"emails": ["a@example.com", "b@example.com"]}}}),
        input_roots=["wf.vars.emails"],
        feedback_text="Если массив пустой, верни nil.",
        previous_candidate='{"result":"lua{return wf.vars.emails[#wf.vars.emails]}lua"}',
        debug=True,
    )

    assert result["validation_status"] == "passed"
    assert result["trace"] == [
        "request_received",
        "feedback_received",
        "planner",
        "prompter",
        "generation",
        "deterministic_validation",
        "semantic_validation",
        "response_ready",
    ]
    assert [call["agent"] for call in model_adapter.agent_calls] == ["planner", "prompter", "semantic_critic"]
    planner_prompt = model_adapter.agent_calls[0]["legacy_prompt"]
    assert "Исходная задача:" in planner_prompt
    assert "Предыдущий кандидат:" in planner_prompt
    assert "Обратная связь пользователя:" in planner_prompt
    assert "Если массив пустой, верни nil." in planner_prompt


def test_generation_service_defaults_lowcode_json_mode_for_lowcode_path() -> None:
    model_adapter = ScriptedModelAdapter(
        [
            '{"result":"lua{return wf.vars.emails[#wf.vars.emails]}lua"}',
            SEMANTIC_PASS_RESPONSE,
        ]
    )
    service = GenerationService(model_adapter=model_adapter)

    result = service.generate(
        task_text="Get the last email from the list.",
        provided_context=json.dumps({"wf": {"vars": {"emails": ["a@example.com", "b@example.com"]}}}),
        debug=True,
    )

    assert result["validation_status"] == "passed"
    assert result["output_mode"] == "lowcode_json"
    assert [call["agent"] for call in model_adapter.agent_calls] == ["planner", "prompter", "semantic_critic"]
    planner_prompt = model_adapter.agent_calls[0]["legacy_prompt"]
    assert '"output_mode":"lowcode_json"' in planner_prompt
    assert '"mode":"lowcode_json"' in planner_prompt


def test_generation_service_runs_semantic_critic_after_deterministic_validation() -> None:
    class SemanticFailModelAdapter(RussianPrompterPatchModelAdapter):
        def __init__(self, responses: list[str], semantic_response: str) -> None:
            super().__init__(responses)
            self._semantic_response = semantic_response

        def generate_from_agent(self, agent_prompt: AgentPrompt) -> str:
            if agent_prompt.agent_name != "semantic_critic":
                return super().generate_from_agent(agent_prompt)
            self.agent_calls.append(
                {
                    "agent": agent_prompt.agent_name,
                    "messages": agent_prompt.to_messages_payload(),
                    "legacy_prompt": agent_prompt.to_legacy_prompt(),
                }
            )
            return self._semantic_response

    candidate = '{"result":"lua{return wf.vars.emails[1]}lua"}'
    semantic_fail = (
        '{"s":"fail","c":"semantic_mismatch",'
        '"m":"Кандидат возвращает первый элемент, а нужно вернуть последний email.",'
        '"fix":"Верни wf.vars.emails[#wf.vars.emails]."}'
    )
    model_adapter = SemanticFailModelAdapter([candidate], semantic_fail)
    service = GenerationService(model_adapter=model_adapter)

    result = service.generate(
        task_text="Get the last email from the list.",
        provided_context=json.dumps({"wf": {"vars": {"emails": ["a@example.com", "b@example.com"]}}}),
        archetype="simple_extraction",
        output_mode="raw_lua",
        input_roots=["wf.vars.emails"],
        repair_budget=1,
        debug=True,
    )

    assert result["validation_status"] == "failed"
    assert result["stop_reason"] == "repair_exhausted"
    assert result["trace"][:7] == [
        "request_received",
        "planner",
        "prompter",
        "generation",
        "deterministic_validation",
        "semantic_validation",
        "assisted_repair_summarizer",
    ]
    assert result["critic_report"]["action"] == "repair"
    assert result["critic_report"]["failure_class"] == "semantic_mismatch"
    first_iteration = result["validator_report"]["iterations"][0]
    assert first_iteration["rule_report"]["status"] == "pass"
    assert first_iteration["semantic_report"]["status"] == "fail"
    assert first_iteration["semantic_report"]["findings"][0]["failure_class"] == "semantic_mismatch"
    assert [call["agent"] for call in model_adapter.agent_calls] == [
        "planner",
        "prompter",
        "semantic_critic",
        "assisted_repair_summarizer",
    ]
    semantic_prompt = model_adapter.agent_calls[2]["legacy_prompt"]
    assert "Validators:" in semantic_prompt
    assert "Candidate:" in semantic_prompt
    assert candidate in semantic_prompt
    debug = result["debug"]
    assert debug is not None
    assert [call["phase"] for call in debug["model_calls"]] == ["generation", "semantic_validation"]
    assert debug["model_calls"][1]["agent"] == "semantic_critic"


def test_generation_service_adds_russian_prompter_context_to_lowcode_generator_prompt() -> None:
    model_adapter = RussianPrompterPatchModelAdapter(
        [
            '{"result":"lua{return wf.vars.emails[#wf.vars.emails]}lua"}',
        ]
    )
    service = GenerationService(model_adapter=model_adapter)

    result = service.generate(
        task_text="Get the last email from the list.",
        provided_context=json.dumps(
            {
                "wf": {
                    "vars": {
                        "emails": [
                            "user1@example.com",
                            "user2@example.com",
                        ]
                    }
                }
            }
        ),
        archetype="simple_extraction",
        output_mode="raw_lua",
        input_roots=["wf.vars.emails"],
        risk_tags=["array_indexing", "empty_array"],
        debug=True,
    )

    assert result["code"] == '{"result":"lua{return wf.vars.emails[#wf.vars.emails]}lua"}'
    assert result["validation_status"] == "passed"
    assert result["stop_reason"] == "passed"
    assert result["trace"] == [
        "request_received",
        "planner",
        "prompter",
        "generation",
        "deterministic_validation",
        "semantic_validation",
        "response_ready",
    ]
    assert result["validator_report"]["status"] == "pass"
    assert result["critic_report"]["action"] == "finalize"
    assert result["critic_report"]["failure_class"] is None
    assert result["repair_count"] == 0
    assert result["clarification_count"] == 0
    assert result["output_mode"] == "raw_lua"
    assert result["archetype"] == "simple_extraction"
    assert [call["agent"] for call in model_adapter.agent_calls] == ["planner", "prompter", "semantic_critic"]
    assert len(model_adapter.prompts) == 1

    debug = result["debug"]
    assert debug is not None
    generator_prompt = model_adapter.prompts[0]
    assert debug["prompt_package"]["prompt"] == generator_prompt
    assert debug["prompt_package"]["planner_result"]["source"] == "agent"
    assert debug["prompt_package"]["prompt_builder_result"]["source"] == "agent_patch"
    assert "Ты генерируешь Lua 5.5 выражения/скрипты для LowCode." in generator_prompt
    assert "Верни только JSON object." in generator_prompt
    assert "Каждое значение, которое содержит Lua, должно быть строкой в формате lua{<Lua код>}lua." in generator_prompt
    assert "Lua внутри lua{...}lua должен возвращать значение через return." in generator_prompt
    assert "Не записывай результат в wf.vars.<name>, если пользователь явно не попросил сохранить его в LowCode-переменную." in generator_prompt
    assert "Все LowCode-переменные лежат в wf.vars." in generator_prompt
    assert "Переменные, которые схема получает при запуске из variables, лежат в wf.initVariables." in generator_prompt
    assert "использовать JsonPath;" in generator_prompt
    assert "Задача:\nGet the last email from the list." in generator_prompt
    assert 'Контекст:\n{"wf": {"vars": {"emails": ["user1@example.com", "user2@example.com"]}}}' in generator_prompt
    assert "SYSTEM:" not in generator_prompt
    assert "USER:" not in generator_prompt
    assert "Task archetype:" not in generator_prompt
    assert "Mode-specific rules:" not in generator_prompt
    assert "Дополнения prompter-агента:" in generator_prompt
    assert "Учитывай TaskSpec: нужно вернуть последний элемент массива, а не весь массив." in generator_prompt
    assert "Используй Lua-индексацию wf.vars.emails[#wf.vars.emails]." in generator_prompt
    assert "Prompter agent additions:" not in generator_prompt
    assert "Risk hints:" not in generator_prompt
    assert "План:" in generator_prompt
    assert [layer["stage"] for layer in debug["pipeline_layers"]] == [
        "planner",
        "prompter",
        "generator",
        "deterministic_validation",
        "semantic_critic",
    ]
    assert [call["phase"] for call in debug["agent_layer_calls"]] == ["planner", "prompter"]
    assert debug["model_calls"][0] == {
        "phase": "generation",
        "agent": "generator",
        "prompt": debug["prompt_package"]["prompt"],
        "raw_response": result["code"],
    }
    assert debug["model_calls"][1]["phase"] == "semantic_validation"
    assert debug["model_calls"][1]["agent"] == "semantic_critic"
    assert debug["model_calls"][1]["semantic_report"]["status"] == "pass"
    assert debug["validation_passes"][0]["format_report"]["status"] == "pass"
    assert debug["validation_passes"][0]["syntax_report"]["status"] == "pass"
    assert debug["validation_passes"][0]["rule_report"]["status"] == "pass"
    assert debug["validation_passes"][0]["semantic_report"]["status"] == "pass"


def test_generation_service_continues_truncated_generator_output_before_validation(
    monkeypatch,
    tmp_path,
) -> None:
    partial_candidate = '{"result":"lua{return wf.vars.em'
    tail_candidate = 'ails[#wf.vars.emails]}lua"}'
    full_candidate = partial_candidate + tail_candidate
    created_temp_paths: list[Path] = []
    validation_temp_exists: list[list[bool]] = []
    debug_temp_exists: list[list[bool]] = []

    def fake_named_temporary_file(
        mode: str = "w+b",
        *_args: object,
        encoding: str | None = None,
        delete: bool = True,
        prefix: str | None = None,
        suffix: str | None = None,
        **_kwargs: object,
    ):
        assert delete is False
        temp_path = tmp_path / f"{prefix or 'candidate_'}{len(created_temp_paths)}{suffix or '.tmp'}"
        created_temp_paths.append(temp_path)
        return temp_path.open(mode, encoding=encoding)

    monkeypatch.setattr(generation_module.tempfile, "NamedTemporaryFile", fake_named_temporary_file)
    real_run_validation_pipeline = generation_module.run_validation_pipeline

    def recording_run_validation_pipeline(candidate: str, **kwargs: object):
        validation_temp_exists.append([temp_path.exists() for temp_path in created_temp_paths])
        return real_run_validation_pipeline(candidate, **kwargs)

    monkeypatch.setattr(generation_module, "run_validation_pipeline", recording_run_validation_pipeline)
    model_adapter = TruncatedGeneratorContinuationModelAdapter(
        [
            {
                "response": partial_candidate,
                "eval_count": 256,
                "num_predict": 256,
            },
            {
                "response": tail_candidate,
                "eval_count": 24,
                "num_predict": 256,
            },
        ]
    )
    service = GenerationService(model_adapter=model_adapter)
    real_build_debug_payload = service._build_debug_payload

    def recording_build_debug_payload(*args: object, **kwargs: object):
        debug_temp_exists.append([temp_path.exists() for temp_path in created_temp_paths])
        return real_build_debug_payload(*args, **kwargs)

    monkeypatch.setattr(service, "_build_debug_payload", recording_build_debug_payload)

    result = service.generate(
        task_text="Get the last email from the list.",
        provided_context='{"wf":{"vars":{"emails":["user1@example.com","user2@example.com"]}}}',
        archetype="simple_extraction",
        output_mode="raw_lua",
        input_roots=["wf.vars.emails"],
        risk_tags=["array_indexing", "empty_array"],
        debug=True,
    )

    assert result["code"] == full_candidate
    assert result["validation_status"] == "passed"
    assert len(model_adapter.prompts) == 2
    assert partial_candidate in model_adapter.prompts[1]
    assert "лимит вывода" in model_adapter.prompts[1]
    assert validation_temp_exists[-1] == [True]
    assert debug_temp_exists[-1] == [True]
    assert all(not temp_path.exists() for temp_path in created_temp_paths)
    debug = result["debug"]
    assert debug is not None
    assert debug["model_calls"][0]["raw_response"] == full_candidate
    assert debug["model_calls"][0]["truncation_guard"]["continuation_count"] == 1
    assert debug["validation_passes"][0]["candidate"] == full_candidate


def test_generation_service_creates_temp_file_for_short_generator_output_until_final_status(
    monkeypatch,
    tmp_path,
) -> None:
    candidate = '{"result":"lua{return wf.vars.emails[#wf.vars.emails]}lua"}'
    created_temp_paths: list[Path] = []
    debug_temp_exists: list[list[bool]] = []

    def fake_named_temporary_file(
        mode: str = "w+b",
        *_args: object,
        encoding: str | None = None,
        delete: bool = True,
        prefix: str | None = None,
        suffix: str | None = None,
        **_kwargs: object,
    ):
        assert delete is False
        temp_path = tmp_path / f"{prefix or 'candidate_'}{len(created_temp_paths)}{suffix or '.tmp'}"
        created_temp_paths.append(temp_path)
        return temp_path.open(mode, encoding=encoding)

    monkeypatch.setattr(generation_module.tempfile, "NamedTemporaryFile", fake_named_temporary_file)
    model_adapter = TruncatedGeneratorContinuationModelAdapter(
        [
            {
                "response": candidate,
                "eval_count": 24,
                "num_predict": 256,
            },
        ]
    )
    service = GenerationService(model_adapter=model_adapter)
    real_build_debug_payload = service._build_debug_payload

    def recording_build_debug_payload(*args: object, **kwargs: object):
        debug_temp_exists.append([temp_path.exists() for temp_path in created_temp_paths])
        return real_build_debug_payload(*args, **kwargs)

    monkeypatch.setattr(service, "_build_debug_payload", recording_build_debug_payload)

    result = service.generate(
        task_text="Get the last email from the list.",
        provided_context='{"wf":{"vars":{"emails":["user1@example.com","user2@example.com"]}}}',
        archetype="simple_extraction",
        output_mode="raw_lua",
        input_roots=["wf.vars.emails"],
        risk_tags=["array_indexing", "empty_array"],
        debug=True,
    )

    assert result["code"] == candidate
    assert result["validation_status"] == "passed"
    assert len(created_temp_paths) == 1
    assert debug_temp_exists[-1] == [True]
    assert all(not temp_path.exists() for temp_path in created_temp_paths)


def test_generation_service_stops_after_bounded_invalid_lowcode_json_repairs() -> None:
    invalid_candidate = "\n".join(
        [
            "```json",
            "{",
            '  "raw_lua": {',
            '    "value": "',
            "      local emails = wf.vars.emails",
            "      if emails and #emails > 0 then",
            "        return emails[#emails]",
            "      else",
            "        return nil",
            "      end",
            '    "',
            "  }",
            "}",
            "```",
        ]
    )
    model_adapter = RussianPrompterPatchModelAdapter([invalid_candidate] * 2)
    service = GenerationService(model_adapter=model_adapter)

    result = service.generate(
        task_text="Из полученного json списка email получи последний.",
        provided_context=json.dumps(
            {
                "wf": {
                    "vars": {
                        "emails": [
                            "user1@example.com",
                            "user2@example.com",
                            "user3@example.com",
                        ]
                    }
                }
            }
        ),
        archetype="simple_extraction",
        output_mode="raw_lua",
        input_roots=["wf.vars.emails"],
        risk_tags=["array_indexing", "empty_array"],
        debug=True,
    )

    assert result["validation_status"] == "failed"
    assert result["stop_reason"] == "repair_exhausted"
    assert result["trace"][:5] == [
        "request_received",
        "planner",
        "prompter",
        "generation",
        "deterministic_validation",
    ]
    assert result["trace"].count("repair_prompter") == 0
    assert result["trace"].count("repair_generation") == 1
    assert "assisted_repair_summarizer" in result["trace"]
    assert result["trace"][-1] == "response_ready"
    assert result["repair_count"] == 1
    assert result["code"].startswith("```json")
    assert result["validator_report"]["status"] == "fail"
    assert result["critic_report"]["action"] == "repair"
    assert result["critic_report"]["failure_class"] == "markdown_fence"
    assert "repair_prompt" in result["critic_report"]
    assisted_repair_request = result["assisted_repair_request"]
    assert isinstance(assisted_repair_request, dict)
    assert assisted_repair_request["failure_classes"] == ["markdown_fence"]
    assert assisted_repair_request["latest_candidate"] == result["code"]
    assert "markdown" in str(assisted_repair_request["summary"]).lower()
    options = assisted_repair_request["options"]
    assert options[0]["id"] == "return_plain_output"
    assert options[0]["label"] == "Убрать markdown"
    assert "json object" in str(options[0]["effect"]).lower()
    assert "lowcode_json" in str(options[0]["effect"]).lower()
    assert options[1:] == [
        {
            "id": "simplify_result",
            "label": "Упростить результат",
            "effect": "Сохранить цель пользователя, но выбрать более простую форму результата и убрать лишнюю структуру.",
        },
        {
            "id": "custom",
            "label": "Свой вариант",
            "effect": "Пользователь вводит свою инструкцию для следующей широкой итерации.",
        },
    ]
    assert len(result["validator_report"]["iterations"]) == 2
    first_iteration = result["validator_report"]["iterations"][0]
    assert first_iteration["format_report"]["status"] == "fail"
    assert first_iteration["format_report"]["findings"][0]["failure_class"] == "markdown_fence"
    assert first_iteration["syntax_report"]["status"] == "skipped"
    debug = result["debug"]
    assert debug is not None
    assert [call["agent"] for call in model_adapter.agent_calls] == [
        "planner",
        "prompter",
        "assisted_repair_summarizer",
    ]
    assert debug["agent_layer_calls"][-1]["phase"] == "assisted_repair_summarizer"
    assert debug["agent_layer_calls"][-1]["assisted_repair_request"]["source"] == "deterministic_fallback"
    assert debug["validation_passes"][0]["candidate"] == result["code"]
    assert debug["validation_passes"][0]["format_report"]["findings"][0]["failure_class"] == "markdown_fence"


def test_generation_service_uses_agent_for_assisted_repair_summary_after_repair_exhausted() -> None:
    invalid_candidate = "\n".join(
        [
            "```json",
            "{",
            '  "raw_lua": {',
            '    "value": "',
            "      local emails = wf.vars.emails",
            "      if emails and #emails > 0 then",
            "        return emails[#emails]",
            "      else",
            "        return nil",
            "      end",
            '    "',
            "  }",
            "}",
            "```",
        ]
    )
    model_adapter = AgenticAssistedRepairSummarizerModelAdapter([invalid_candidate] * 2)
    service = GenerationService(model_adapter=model_adapter)

    result = service.generate(
        task_text="Из полученного json списка email получи последний.",
        provided_context=json.dumps(
            {
                "wf": {
                    "vars": {
                        "emails": [
                            "user1@example.com",
                            "user2@example.com",
                            "user3@example.com",
                        ]
                    }
                }
            }
        ),
        archetype="simple_extraction",
        output_mode="raw_lua",
        input_roots=["wf.vars.emails"],
        risk_tags=["array_indexing", "empty_array"],
        debug=True,
    )

    assert result["validation_status"] == "failed"
    assert result["stop_reason"] == "repair_exhausted"
    assert result["trace"][-2:] == ["assisted_repair_summarizer", "response_ready"]
    assert result["assisted_repair_request"] == {
        "summary": "Код всё ещё приходит с markdown-ограждениями вместо чистого raw_lua.",
        "failure_classes": ["markdown_fence"],
        "options": [
            {
                "id": "return_plain_output",
                "label": "Убрать markdown",
                "effect": "Вернуть только raw_lua без markdown и пояснений.",
            },
            {
                "id": "simplify_result",
                "label": "Упростить результат",
                "effect": "Сохранить цель пользователя, но выбрать более простую форму результата.",
            },
            {
                "id": "custom",
                "label": "Свой вариант",
                "effect": "Пользователь вводит свою инструкцию для следующей итерации.",
            },
        ],
        "latest_candidate": result["code"],
    }
    assert [call["agent"] for call in model_adapter.agent_calls] == [
        "planner",
        "prompter",
        "assisted_repair_summarizer",
    ]
    summarizer_prompt = model_adapter.agent_calls[2]["legacy_prompt"]
    assert "repair history summary" in summarizer_prompt
    assert "validation failures" in summarizer_prompt
    debug = result["debug"]
    assert debug is not None
    assert [call["phase"] for call in debug["agent_layer_calls"]] == [
        "planner",
        "prompter",
        "assisted_repair_summarizer",
    ]


def test_generation_service_repairs_invalid_lowcode_json_contract_directly_with_generator() -> None:
    invalid_candidate = "\n".join(
        [
            "```json",
            "{",
            '  "raw_lua": {',
            '    "value": "return wf.vars.emails[#wf.vars.emails]"',
            "  }",
            "}",
            "```",
        ]
    )
    repaired_candidate = '{"result":"lua{return wf.vars.emails[#wf.vars.emails]}lua"}'
    model_adapter = RussianPrompterPatchModelAdapter([invalid_candidate, repaired_candidate])
    service = GenerationService(model_adapter=model_adapter)

    result = service.generate(
        task_text="Из полученного json списка email получи последний.",
        provided_context=json.dumps(
            {
                "wf": {
                    "vars": {
                        "emails": [
                            "user1@example.com",
                            "user2@example.com",
                            "user3@example.com",
                        ]
                    }
                }
            }
        ),
        archetype="simple_extraction",
        output_mode="raw_lua",
        input_roots=["wf.vars.emails"],
        risk_tags=["array_indexing", "empty_array"],
        debug=True,
    )

    assert result["code"] == repaired_candidate
    assert result["validation_status"] == "repaired"
    assert result["stop_reason"] == "passed"
    assert result["repair_count"] == 1
    assert result["validator_report"]["status"] == "pass"
    assert [iteration["format_report"]["status"] for iteration in result["validator_report"]["iterations"]] == [
        "fail",
        "pass",
    ]
    assert result["critic_report"]["action"] == "finalize"
    assert "repair_prompter" not in result["trace"]
    assert "repair_generation" in result["trace"]
    assert [call["agent"] for call in model_adapter.agent_calls] == ["planner", "prompter", "semantic_critic"]

    debug = result["debug"]
    assert debug is not None
    assert [call["phase"] for call in debug["model_calls"]] == ["generation", "repair_generation", "semantic_validation"]
    assert [call["phase"] for call in debug["agent_layer_calls"]] == ["planner", "prompter"]
    assert "Текущий невалидный candidate:" in debug["model_calls"][1]["prompt"]
    assert "Инструкция critic:" in debug["model_calls"][1]["prompt"]
    assert debug["validation_passes"][0]["format_report"]["findings"][0]["failure_class"] == "markdown_fence"
    assert debug["validation_passes"][1]["format_report"]["status"] == "pass"


def test_generation_service_auto_normalizes_fenced_raw_lua_before_repair_budget() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                "```lua\nreturn wf.vars.emails[#wf.vars.emails]\n```",
                SEMANTIC_PASS_RESPONSE,
            ]
        )
    )

    result = service.generate(
        task_text="Get the last email from the list.",
        provided_context=json.dumps(
            {
                "wf": {
                    "vars": {
                        "emails": [
                            "user1@example.com",
                            "user2@example.com",
                        ]
                    }
                }
            }
        ),
        archetype="simple_extraction",
        output_mode="raw_lua",
        input_roots=["wf.vars.emails"],
        risk_tags=["array_indexing", "empty_array"],
        debug=True,
    )

    assert result["code"] == "return wf.vars.emails[#wf.vars.emails]"
    assert result["validation_status"] == "passed"
    assert result["stop_reason"] == "passed"
    assert result["repair_count"] == 0
    assert result["trace"] == [
        "request_received",
        "generation",
        "format_validation",
        "rule_validation",
        "runtime_validation",
        "semantic_validation",
        "finalize",
    ]
    debug = result["debug"]
    assert debug is not None
    assert debug["validation_passes"][0]["candidate"] == "return wf.vars.emails[#wf.vars.emails]"
    assert debug["validation_passes"][0]["format_report"]["status"] == "pass"


def test_generation_service_runs_runtime_validation_before_semantic_for_simple_extraction() -> None:
    model_adapter = ScriptedModelAdapter(
        [
            "return wf.vars.emails[#wf.vars.emails]",
            SEMANTIC_PASS_RESPONSE,
        ]
    )
    service = LegacyQualityLoopService(
        model_adapter=model_adapter
    )

    result = service.generate(
        task_text="Get the last email from the list.",
        provided_context=json.dumps(
            {
                "wf": {
                    "vars": {
                        "emails": [
                            "user1@example.com",
                            "user2@example.com",
                        ]
                    }
                }
            }
        ),
        archetype="simple_extraction",
        output_mode="raw_lua",
        input_roots=["wf.vars.emails"],
        risk_tags=["array_indexing", "empty_array"],
        debug=True,
    )

    assert result["trace"] == [
        "request_received",
        "generation",
        "format_validation",
        "rule_validation",
        "runtime_validation",
        "semantic_validation",
        "finalize",
    ]
    debug = result["debug"]
    assert debug is not None
    assert debug["prompt_package"]["task_spec"]["operation"] == "last_array_item"
    assert debug["prompt_package"]["task_spec"]["expected_shape"] == "scalar_or_nil"
    assert debug["prompt_package"]["planner_result"]["agent"] == "planner"
    assert debug["prompt_package"]["prompt_builder_result"]["agent"] == "prompter"
    assert [layer["stage"] for layer in debug["pipeline_layers"]] == [
        "input_normalization",
        "planner",
        "prompter",
        "generator",
    ]
    assert debug["validation_passes"][0]["runtime_report"]["status"] == "pass"
    assert debug["model_calls"][1]["phase"] == "semantic_validation"
    assert [call["agent"] for call in model_adapter.agent_calls] == [
        "planner",
        "prompter",
        "generator",
        "semantic_critic",
    ]
    assert [message["role"] for message in model_adapter.agent_calls[0]["messages"]] == ["system", "user"]


def test_generation_service_runs_planner_first_without_cli_semantic_overrides() -> None:
    model_adapter = PlannerOwnedMetadataModelAdapter(
        [
            "return wf.vars.emails[#wf.vars.emails]",
            SEMANTIC_PASS_RESPONSE,
        ]
    )
    service = LegacyQualityLoopService(model_adapter=model_adapter)

    result = service.generate(
        task_text="Из полученного списка email получи последний.",
        provided_context=json.dumps(
            {
                "wf": {
                    "vars": {
                        "emails": [
                            "user1@example.com",
                            "user2@example.com",
                        ]
                    }
                }
            }
        ),
        debug=True,
    )

    assert result["validation_status"] == "passed"
    assert result["output_mode"] == "raw_lua"
    assert result["archetype"] == "simple_extraction"
    assert result["trace"] == [
        "request_received",
        "generation",
        "format_validation",
        "rule_validation",
        "runtime_validation",
        "semantic_validation",
        "finalize",
    ]
    debug = result["debug"]
    assert debug is not None
    assert debug["prompt_package"]["archetype"] == "simple_extraction"
    assert debug["prompt_package"]["output_mode"] == "raw_lua"
    assert debug["prompt_package"]["risk_tags"] == ["array_indexing", "empty_array"]
    assert debug["prompt_package"]["planner_result"]["task_spec"]["archetype"] == "simple_extraction"
    assert [call["agent"] for call in model_adapter.agent_calls] == [
        "planner",
        "prompter",
        "generator",
        "semantic_critic",
    ]


def test_generation_service_runtime_blocks_wrong_candidate_after_truncated_planner_json() -> None:
    model_adapter = TruncatedPlannerModelAdapter(
        [
            "return (#wf.vars.emails>0 and wf.vars.emails or nil)",
            "return wf.vars.emails[#wf.vars.emails]",
            SEMANTIC_PASS_RESPONSE,
        ]
    )
    service = LegacyQualityLoopService(model_adapter=model_adapter)

    result = service.generate(
        task_text="Из полученного списка email получи последний.",
        provided_context=json.dumps(
            {
                "wf": {
                    "vars": {
                        "emails": [
                            "user1@example.com",
                            "user2@example.com",
                            "user3@example.com",
                        ]
                    }
                }
            }
        ),
        archetype="simple_extraction",
        output_mode="raw_lua",
        input_roots=["wf.vars.emails"],
        risk_tags=["array_indexing", "empty_array"],
        debug=True,
    )

    assert result["validation_status"] == "repaired"
    assert result["code"] == "return wf.vars.emails[#wf.vars.emails]"
    debug = result["debug"]
    assert debug is not None
    assert debug["prompt_package"]["planner_result"]["source"] == "agent_partial"
    assert debug["validation_passes"][0]["runtime_report"]["status"] == "fail"
    assert debug["validation_passes"][0]["semantic_report"]["status"] == "skipped"
    assert debug["validation_passes"][1]["runtime_report"]["status"] == "pass"
    assert debug["validation_passes"][1]["semantic_report"]["status"] == "pass"


def test_generation_service_repair_loop_blocks_whole_array_when_planner_falls_back_to_unresolved() -> None:
    model_adapter = EmptyPlannerPrompterModelAdapter(
        [
            "",
            "local emails = wf.vars.emails or {}\nlocal last_email = nil\nif #emails > 0 then\n  last_email = emails\nend\nreturn last_email",
            "return wf.vars.emails[#wf.vars.emails]",
            SEMANTIC_PASS_RESPONSE,
        ]
    )
    service = LegacyQualityLoopService(model_adapter=model_adapter)

    result = service.generate(
        task_text="Из полученного списка email получи последний.",
        provided_context=json.dumps(
            {
                "wf": {
                    "vars": {
                        "emails": [
                            "user1@example.com",
                            "user2@example.com",
                            "user3@example.com",
                        ]
                    }
                }
            }
        ),
        debug=True,
    )

    assert result["validation_status"] == "repaired"
    assert result["code"] == "return wf.vars.emails[#wf.vars.emails]"
    debug = result["debug"]
    assert debug is not None
    assert debug["prompt_package"]["planner_result"]["source"] == "deterministic_fallback"
    assert debug["validation_passes"][1]["runtime_report"]["status"] == "fail"
    assert debug["validation_passes"][1]["runtime_report"]["findings"][0]["failure_class"] == "runtime_behavior_mismatch"
    assert debug["validation_passes"][1]["semantic_report"]["status"] == "skipped"
    assert debug["validation_passes"][2]["runtime_report"]["status"] == "pass"
    assert debug["validation_passes"][2]["validation_bundle"]["task_spec"]["archetype"] == "simple_extraction"
    assert debug["validation_passes"][2]["validation_bundle"]["task_spec"]["operation"] == "last_array_item"


def test_generation_service_runtime_validates_array_item_operation_with_transformation_archetype() -> None:
    model_adapter = TransformationArrayItemPlannerModelAdapter(
        [
            "return wf.vars.emails[1]",
            "return wf.vars.emails[#wf.vars.emails]",
        ]
    )
    service = LegacyQualityLoopService(model_adapter=model_adapter)

    result = service.generate(
        task_text="Из полученного списка email получи последний.",
        provided_context=json.dumps(
            {
                "wf": {
                    "vars": {
                        "emails": [
                            "user1@example.com",
                            "user2@example.com",
                            "user3@example.com",
                        ]
                    }
                }
            }
        ),
        debug=True,
    )

    assert result["validation_status"] == "repaired"
    assert result["code"] == "return wf.vars.emails[#wf.vars.emails]"
    debug = result["debug"]
    assert debug is not None
    assert debug["prompt_package"]["task_spec"]["archetype"] == "transformation"
    assert debug["prompt_package"]["task_spec"]["operation"] == "last_array_item"
    assert debug["validation_passes"][0]["principle_report"]["status"] == "pass"
    assert debug["validation_passes"][0]["runtime_report"]["status"] == "fail"
    assert debug["validation_passes"][0]["runtime_report"]["findings"][0]["failure_class"] == "runtime_behavior_mismatch"


def test_generation_service_applies_compact_planner_and_prompter_protocol() -> None:
    model_adapter = CompactAgentProtocolModelAdapter(
        [
            "return wf.vars.emails[#wf.vars.emails]",
            SEMANTIC_PASS_RESPONSE,
        ]
    )
    service = LegacyQualityLoopService(model_adapter=model_adapter)

    result = service.generate(
        task_text="Из полученного списка email получи последний.",
        provided_context=json.dumps({"wf": {"vars": {"emails": ["user1@example.com", "user2@example.com"]}}}),
        archetype="simple_extraction",
        output_mode="raw_lua",
        input_roots=["wf.vars.emails"],
        risk_tags=["array_indexing", "empty_array"],
        debug=True,
    )

    assert result["validation_status"] == "passed"
    assert result["code"] == "return wf.vars.emails[#wf.vars.emails]"
    debug = result["debug"]
    assert debug["prompt_package"]["planner_result"]["source"] == "agent"
    assert debug["prompt_package"]["prompt_builder_result"]["source"] == "agent_patch"
    generator_system = debug["model_calls"][0]["messages"][0]["content"]
    generator_user = debug["model_calls"][0]["messages"][1]["content"]
    assert "Return the last array item, not the whole array." in generator_system
    assert "Use wf.vars.emails[#wf.vars.emails]." in generator_user
    assert len(debug["agent_layer_calls"][0]["raw_response"]) < 220
    assert len(debug["agent_layer_calls"][1]["raw_response"]) < 140


def test_generation_service_runtime_backstop_blocks_unresolved_last_array_item_shape() -> None:
    model_adapter = EmptyPlannerPatchPrompterModelAdapter(
        [
            "if #wf.vars.emails == 0 then return nil end\nreturn wf.vars.emails",
            "return wf.vars.emails[#wf.vars.emails]",
            SEMANTIC_PASS_RESPONSE,
        ]
    )
    service = LegacyQualityLoopService(model_adapter=model_adapter)

    result = service.generate(
        task_text="Из полученного списка email получи последний.",
        provided_context=json.dumps(
            {
                "wf": {
                    "vars": {
                        "emails": [
                            "user1@example.com",
                            "user2@example.com",
                            "user3@example.com",
                        ]
                    }
                }
            }
        ),
        archetype="simple_extraction",
        output_mode="raw_lua",
        input_roots=["wf.vars.emails"],
        risk_tags=["array_indexing", "empty_array"],
        debug=True,
    )

    assert result["validation_status"] == "repaired"
    assert result["code"] == "return wf.vars.emails[#wf.vars.emails]"
    debug = result["debug"]
    assert debug["prompt_package"]["planner_result"]["source"] == "deterministic_fallback"
    assert debug["prompt_package"]["task_spec"]["operation"] == "unresolved"
    assert debug["validation_passes"][0]["principle_report"]["status"] == "fail"
    assert debug["validation_passes"][0]["principle_report"]["findings"][0]["failure_class"] == "array_item_returns_whole_array"
    assert debug["validation_passes"][0]["runtime_report"]["status"] == "skipped"
    assert debug["validation_passes"][0]["runtime_report"]["skipped_reason"] == "prerequisite_validation_failed"
    assert debug["validation_passes"][0]["semantic_report"]["status"] == "skipped"
    assert debug["validation_passes"][0]["critic_report"]["action"] == "repair"
    assert debug["validation_passes"][1]["runtime_report"]["status"] == "pass"
    assert debug["validation_passes"][1]["semantic_report"]["status"] == "pass"


def test_generation_service_applies_valid_planner_and_prompter_agent_outputs() -> None:
    model_adapter = AgenticPromptModelAdapter(
        [
            "return wf.vars.emails[#wf.vars.emails]",
            SEMANTIC_PASS_RESPONSE,
        ]
    )
    service = LegacyQualityLoopService(model_adapter=model_adapter)

    result = service.generate(
        task_text="Get the last email from the list.",
        provided_context=json.dumps({"wf": {"vars": {"emails": ["user1@example.com", "user2@example.com"]}}}),
        archetype="simple_extraction",
        output_mode="raw_lua",
        input_roots=["wf.vars.emails"],
        risk_tags=["array_indexing", "empty_array"],
        debug=True,
    )

    debug = result["debug"]
    assert result["validation_status"] == "passed"
    assert debug is not None
    assert debug["prompt_package"]["planner_result"]["source"] == "agent"
    assert debug["prompt_package"]["prompt_builder_result"]["source"] == "agent"
    assert debug["model_calls"][0]["agent"] == "generator"
    assert debug["model_calls"][0]["messages"][0]["content"] == "TaskSpec\nAGENT PROMPTER SYSTEM"
    assert [call["phase"] for call in debug["agent_layer_calls"]] == ["planner", "prompter"]


def test_generation_service_runs_runtime_and_semantic_when_static_validator_has_infra_skip(monkeypatch) -> None:
    real_run_validation_pipeline = repair_loop.run_validation_pipeline

    def static_infra_skip_pipeline(*args, **kwargs):
        normalized, format_report, syntax_report, _static_report, principle_report, _rule_report = (
            real_run_validation_pipeline(*args, **kwargs)
        )
        static_report = ValidatorReport(
            validator="static_validator",
            status="skipped",
            skipped_reason="validator_execution_failed",
            metadata={"tool": "luacheck", "message": "broken luacheck launcher"},
        )
        rule_report = ValidatorReport(
            validator="rule_validator",
            status="skipped",
            skipped_reason="validator_execution_failed",
        )
        return normalized, format_report, syntax_report, static_report, principle_report, rule_report

    monkeypatch.setattr(repair_loop, "run_validation_pipeline", static_infra_skip_pipeline)
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                "return wf.vars.emails[#wf.vars.emails]",
                SEMANTIC_PASS_RESPONSE,
            ]
        )
    )

    result = service.generate(
        task_text="Get the last email from the list.",
        provided_context=json.dumps(
            {
                "wf": {
                    "vars": {
                        "emails": [
                            "user1@example.com",
                            "user2@example.com",
                        ]
                    }
                }
            }
        ),
        archetype="simple_extraction",
        output_mode="raw_lua",
        input_roots=["wf.vars.emails"],
        risk_tags=["array_indexing", "empty_array"],
        debug=True,
    )

    assert result["validation_status"] == "passed"
    assert result["trace"] == [
        "request_received",
        "generation",
        "format_validation",
        "rule_validation",
        "runtime_validation",
        "semantic_validation",
        "finalize",
    ]
    first_iteration = result["validator_report"]["iterations"][0]
    assert first_iteration["static_report"]["status"] == "skipped"
    assert first_iteration["static_report"]["skipped_reason"] == "validator_execution_failed"
    assert first_iteration["rule_report"]["status"] == "skipped"
    assert first_iteration["runtime_report"]["status"] == "pass"
    assert first_iteration["semantic_report"]["status"] == "pass"


def test_generation_service_repairs_runtime_behavior_mismatch_before_semantic() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                "return wf.vars.emails[1]",
                "return wf.vars.emails[#wf.vars.emails]",
                SEMANTIC_PASS_RESPONSE,
            ]
        )
    )

    result = service.generate(
        task_text="Get the last email from the list.",
        provided_context=json.dumps(
            {
                "wf": {
                    "vars": {
                        "emails": [
                            "user1@example.com",
                            "user2@example.com",
                        ]
                    }
                }
            }
        ),
        archetype="simple_extraction",
        output_mode="raw_lua",
        input_roots=["wf.vars.emails"],
        risk_tags=["array_indexing", "empty_array"],
        debug=True,
    )

    assert result["validation_status"] == "repaired"
    assert result["stop_reason"] == "passed"
    assert result["trace"] == [
        "request_received",
        "generation",
        "format_validation",
        "rule_validation",
        "runtime_validation",
        "critic_step",
        "repair_generation",
        "format_validation",
        "rule_validation",
        "runtime_validation",
        "semantic_validation",
        "finalize",
    ]
    debug = result["debug"]
    assert debug is not None
    assert debug["validation_passes"][0]["runtime_report"]["status"] == "fail"
    assert debug["validation_passes"][0]["runtime_report"]["findings"][0]["failure_class"] == "runtime_behavior_mismatch"
    assert debug["validation_passes"][0]["semantic_report"]["status"] == "skipped"
    assert debug["validation_passes"][1]["runtime_report"]["status"] == "pass"
    assert debug["model_calls"][1]["phase"] == "repair_generation"
    assert "Validation summary:" in debug["model_calls"][1]["prompt"]
    assert "TaskSpec compact:" in debug["model_calls"][1]["prompt"]
    assert "ValidationBundle facts:" not in debug["model_calls"][1]["prompt"]
    assert "Original prompt:" not in debug["model_calls"][1]["prompt"]
    assert '"behavioral_fingerprint"' in debug["model_calls"][1]["prompt"]
    assert '"failed_fixture"' in debug["model_calls"][1]["prompt"]
    assert '"shape":"scalar_or_nil"' in debug["model_calls"][1]["prompt"]
    assert debug["model_calls"][2]["phase"] == "semantic_validation"


def test_generation_service_repairs_markdown_fenced_raw_lua_candidate() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                "```lua\nreturn wf.vars.emails[#wf.vars.emails]\n```",
                SEMANTIC_PASS_RESPONSE,
            ]
        )
    )

    result = service.generate(
        task_text="Get the last email from the list.",
        provided_context=json.dumps(
            {
                "wf": {
                    "vars": {
                        "emails": [
                            "user1@example.com",
                            "user2@example.com",
                        ]
                    }
                }
            }
        ),
        archetype="simple_extraction",
        output_mode="raw_lua",
        input_roots=["wf.vars.emails"],
        risk_tags=["array_indexing", "empty_array"],
    )

    assert result["code"] == "return wf.vars.emails[#wf.vars.emails]"
    assert result["validation_status"] == "passed"
    assert result["repair_count"] == 0
    assert result["clarification_count"] == 0
    assert result["output_mode"] == "raw_lua"
    assert result["archetype"] == "simple_extraction"
    assert result["trace"] == [
        "request_received",
        "generation",
        "format_validation",
        "rule_validation",
        "runtime_validation",
        "semantic_validation",
        "finalize",
    ]
    assert result["validator_report"]["status"] == "pass"
    assert result["validator_report"]["iterations"][0]["format_report"]["status"] == "pass"
    assert result["validator_report"]["iterations"][0]["syntax_report"]["status"] == "pass"
    assert result["validator_report"]["iterations"][0]["principle_report"]["status"] == "pass"
    assert result["validator_report"]["iterations"][0]["rule_report"]["status"] == "pass"
    assert result["critic_report"] is None


def test_generation_service_normalizes_fenced_json_wrapper_on_repair_iteration() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                '```json\n{"lastEmail":"lua{return wf.vars.emails[#wf.vars.emails]}lua"}\n```',
                '```json\n{"lastEmail":"lua{return wf.vars.emails[#wf.vars.emails]}lua"}\n```',
                SEMANTIC_PASS_RESPONSE,
            ]
        )
    )

    result = service.generate(
        task_text="Get the last email from the list.",
        provided_context=json.dumps(
            {
                "wf": {
                    "vars": {
                        "emails": [
                            "user1@example.com",
                            "user2@example.com",
                        ]
                    }
                }
            }
        ),
        archetype="simple_extraction",
        output_mode="json_wrapper",
        input_roots=["wf.vars.emails"],
        risk_tags=["array_indexing", "empty_array", "json_wrapper"],
        debug=True,
    )

    assert result["code"] == '{"lastEmail":"lua{return wf.vars.emails[#wf.vars.emails]}lua"}'
    assert result["validation_status"] == "passed"
    assert result["trace"] == [
        "request_received",
        "generation",
        "format_validation",
        "rule_validation",
        "runtime_validation",
        "semantic_validation",
        "finalize",
    ]
    assert result["critic_report"] is None
    debug = result["debug"]
    assert debug is not None
    assert debug["model_calls"][1]["phase"] == "semantic_validation"
    assert debug["validation_passes"][0]["normalized_candidate"] == result["code"]


def test_generation_service_normalizes_fenced_patch_mode_on_repair_iteration() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                '```json\n{"num":"lua{return tonumber(\'5\')}lua","squared":"lua{local n = tonumber(\'5\')\\nreturn n * n}lua"}\n```',
                '```json\n{"num":"lua{return tonumber(\'5\')}lua","squared":"lua{local n = tonumber(\'5\')\\nreturn n * n}lua"}\n```',
                SEMANTIC_PASS_RESPONSE,
            ]
        )
    )

    result = service.generate(
        task_text="Add a variable with the square of the number.",
        provided_context=None,
        archetype="transformation",
        output_mode="patch_mode",
        input_roots=[],
        risk_tags=["patch_payload", "numeric_transform", "no_full_rewrite"],
        debug=True,
    )

    assert (
        result["code"]
        == '{"num":"lua{return tonumber(\'5\')}lua","squared":"lua{local n = tonumber(\'5\')\\nreturn n * n}lua"}'
    )
    assert result["validation_status"] == "passed"
    assert result["trace"] == [
        "request_received",
        "generation",
        "format_validation",
        "rule_validation",
        "semantic_validation",
        "finalize",
    ]
    assert result["critic_report"] is None
    debug = result["debug"]
    assert debug is not None
    assert debug["model_calls"][1]["phase"] == "semantic_validation"
    assert debug["validation_passes"][0]["normalized_candidate"] == result["code"]


def test_generation_service_repairs_invalid_json_json_wrapper_with_tool() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                '{\n  "result": {\n    "last_email": lua{wf.vars.emails[#wf.vars.emails] or ""}lua\n  }\n}',
                SEMANTIC_PASS_RESPONSE,
            ]
        )
    )

    result = service.generate(
        task_text="Get the last email from the list.",
        provided_context=json.dumps(
            {
                "wf": {
                    "vars": {
                        "emails": [
                            "user1@example.com",
                            "user2@example.com",
                        ]
                    }
                }
            }
        ),
        archetype="simple_extraction",
        output_mode="json_wrapper",
        input_roots=["wf.vars.emails"],
        risk_tags=["array_indexing", "empty_array", "json_wrapper"],
        debug=True,
    )

    assert result["code"] == '{"result":{"last_email":"lua{wf.vars.emails[#wf.vars.emails] or \\"\\"}lua"}}'
    assert result["validation_status"] == "repaired"
    assert result["trace"] == [
        "request_received",
        "generation",
        "format_validation",
        "critic_step",
        "repair_generation",
        "format_validation",
        "rule_validation",
        "runtime_validation",
        "semantic_validation",
        "finalize",
    ]
    assert result["repair_count"] == 1
    assert result["critic_report"] == {
        "action": "repair",
        "failure_class": "invalid_json",
        "message": "Repair the candidate for failure class invalid_json without changing the user goal.",
        "repair_prompt": "Исправь текущий кандидат по замечанию валидатора. Сохрани тот же режим вывода, цель пользователя и верни только исправленный результат.",
    }
    debug = result["debug"]
    assert debug is not None
    assert debug["validation_passes"][0]["format_report"]["findings"][0]["failure_class"] == "invalid_json"
    assert debug["validation_passes"][1]["normalized_candidate"] == result["code"]
    assert debug["model_calls"][1]["repair_source"] == "deterministic_tool"


def test_generation_service_repairs_invalid_json_patch_mode_with_tool() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                "{\n  lua = {\n    value = (wf.vars.number or 0) * (wf.vars.number or 0)\n  }\n}",
                SEMANTIC_PASS_RESPONSE,
            ]
        )
    )

    result = service.generate(
        task_text="Add a variable with the square of the number.",
        provided_context=None,
        archetype="transformation",
        output_mode="patch_mode",
        input_roots=[],
        risk_tags=["patch_payload", "numeric_transform", "no_full_rewrite"],
        debug=True,
    )

    assert result["code"] == '{"lua":{"value":"lua{return (wf.vars.number or 0) * (wf.vars.number or 0)}lua"}}'
    assert result["validation_status"] == "repaired"
    assert result["trace"] == [
        "request_received",
        "generation",
        "format_validation",
        "critic_step",
        "repair_generation",
        "format_validation",
        "rule_validation",
        "semantic_validation",
        "finalize",
    ]
    assert result["repair_count"] == 1
    assert result["critic_report"] == {
        "action": "repair",
        "failure_class": "invalid_json",
        "message": "Repair the candidate for failure class invalid_json without changing the user goal.",
        "repair_prompt": "Исправь текущий кандидат по замечанию валидатора. Сохрани тот же режим вывода, цель пользователя и верни только исправленный результат.",
    }
    debug = result["debug"]
    assert debug is not None
    assert debug["validation_passes"][0]["format_report"]["findings"][0]["failure_class"] == "invalid_json"
    assert debug["validation_passes"][1]["normalized_candidate"] == result["code"]
    assert debug["model_calls"][1]["repair_source"] == "deterministic_tool"


def test_generation_service_repairs_string_only_patch_object_with_tool() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                '{\n  "lua": {\n    "my_square_variable = tonumber(wf.vars.number) ^ 2;"\n  }\n}',
                SEMANTIC_PASS_RESPONSE,
            ]
        )
    )

    result = service.generate(
        task_text="Add a variable with the square of the number.",
        provided_context=None,
        archetype="transformation",
        output_mode="patch_mode",
        input_roots=[],
        risk_tags=["patch_payload", "numeric_transform", "no_full_rewrite"],
        debug=True,
    )

    assert result["code"] == '{"lua":{"value":"lua{my_square_variable = tonumber(wf.vars.number) ^ 2;}lua"}}'
    assert result["validation_status"] == "repaired"
    assert result["trace"] == [
        "request_received",
        "generation",
        "format_validation",
        "critic_step",
        "repair_generation",
        "format_validation",
        "rule_validation",
        "semantic_validation",
        "finalize",
    ]
    assert result["repair_count"] == 1
    debug = result["debug"]
    assert debug is not None
    assert debug["validation_passes"][0]["format_report"]["findings"][0]["failure_class"] == "invalid_json"
    assert debug["validation_passes"][1]["normalized_candidate"] == result["code"]
    assert debug["model_calls"][1]["repair_source"] == "deterministic_tool"


def test_generation_service_repairs_fragment_only_patch_object_with_tool() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                '{\n  "wf.vars.squareOfNumber": {\n    "lua{"\n    "..=local num = wf.vars.number\\n"\n    "..=local squared = num * num\\n"\n    "..=return {squareOfNumber = squared}\\n"\n    "}"\n  }\n}',
                SEMANTIC_PASS_RESPONSE,
            ]
        )
    )

    result = service.generate(
        task_text="Add a variable with the square of the number.",
        provided_context=None,
        archetype="transformation",
        output_mode="patch_mode",
        input_roots=[],
        risk_tags=["patch_payload", "numeric_transform", "no_full_rewrite"],
        debug=True,
    )

    assert (
        result["code"]
        == '{"squareOfNumber":{"value":"lua{local num = wf.vars.number\\nlocal squared = num * num\\nreturn {squareOfNumber = squared}\\n}lua"}}'
    )
    assert result["validation_status"] == "repaired"
    assert result["trace"] == [
        "request_received",
        "generation",
        "format_validation",
        "critic_step",
        "repair_generation",
        "format_validation",
        "rule_validation",
        "semantic_validation",
        "finalize",
    ]
    assert result["repair_count"] == 1
    debug = result["debug"]
    assert debug is not None
    assert debug["validation_passes"][0]["format_report"]["findings"][0]["failure_class"] == "invalid_json"
    assert debug["validation_passes"][1]["normalized_candidate"] == result["code"]
    assert debug["model_calls"][1]["repair_source"] == "deterministic_tool"


def test_generation_service_exposes_debug_audit_trail_for_repair_flow() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                "```lua\nreturn wf.vars.emails[#wf.vars.emails]\n```",
                SEMANTIC_PASS_RESPONSE,
            ]
        )
    )

    result = service.generate(
        task_text="Get the last email from the list.",
        provided_context=json.dumps(
            {
                "wf": {
                    "vars": {
                        "emails": [
                            "user1@example.com",
                            "user2@example.com",
                        ]
                    }
                }
            }
        ),
        archetype="simple_extraction",
        output_mode="raw_lua",
        input_roots=["wf.vars.emails"],
        risk_tags=["array_indexing", "empty_array"],
        debug=True,
    )

    debug = result["debug"]
    assert debug is not None
    assert debug["prompt_package"]["archetype"] == "simple_extraction"
    assert debug["prompt_package"]["output_mode"] == "raw_lua"
    assert debug["model_calls"][0]["phase"] == "generation"
    assert debug["model_calls"][0]["raw_response"] == "```lua\nreturn wf.vars.emails[#wf.vars.emails]\n```"
    assert debug["model_calls"][1]["phase"] == "semantic_validation"
    assert debug["validation_passes"][0]["rule_report"]["status"] == "pass"
    assert debug["validation_passes"][0]["semantic_report"]["status"] == "pass"


def test_generation_service_repairs_semantically_wrong_but_formally_valid_candidate() -> None:
    model_adapter = ScriptedModelAdapter(
        [
            "return wf.vars.emails[1]",
            "return wf.vars.emails[#wf.vars.emails]",
            '{"status":"pass","message":"The candidate now returns the last email."}',
        ]
    )
    service = LegacyQualityLoopService(
        model_adapter=model_adapter
    )

    result = service.generate(
        task_text="Get the last email from the list.",
        provided_context=json.dumps(
            {
                "wf": {
                    "vars": {
                        "emails": [
                            "user1@example.com",
                            "user2@example.com",
                        ]
                    }
                }
            }
        ),
        archetype="simple_extraction",
        output_mode="raw_lua",
        input_roots=["wf.vars.emails"],
        risk_tags=["array_indexing", "empty_array"],
        debug=True,
    )

    assert result["code"] == "return wf.vars.emails[#wf.vars.emails]"
    assert result["validation_status"] == "repaired"
    assert result["trace"] == [
        "request_received",
        "generation",
        "format_validation",
        "rule_validation",
        "runtime_validation",
        "critic_step",
        "repair_generation",
        "format_validation",
        "rule_validation",
        "runtime_validation",
        "semantic_validation",
        "finalize",
    ]
    assert result["critic_report"] == {
        "action": "repair",
        "failure_class": "runtime_behavior_mismatch",
        "message": "Исправь кандидат так, чтобы его runtime-поведение соответствовало задаче.",
        "repair_prompt": (
            "Текущий кандидат не соответствует operation=last_array_item. "
            "Нужно вернуть последний элемент массива из wf.vars.emails, либо nil для пустого массива. "
            "Ожидаемая форма результата: scalar_or_nil. "
            "Сломанный runtime fixture: primary. "
            "Ожидалось: 'user2@example.com'; фактически получено: 'user1@example.com'. "
            "Не возвращай весь входной массив, если expected_shape требует scalar_or_nil. "
            "Верни только исправленный результат без объяснений."
        ),
    }
    first_iteration = result["validator_report"]["iterations"][0]
    second_iteration = result["validator_report"]["iterations"][1]
    assert first_iteration["rule_report"]["status"] == "pass"
    assert first_iteration["runtime_report"]["status"] == "fail"
    assert first_iteration["runtime_report"]["findings"][0]["failure_class"] == "runtime_behavior_mismatch"
    assert first_iteration["semantic_report"]["status"] == "skipped"
    assert second_iteration["semantic_report"]["status"] == "pass"
    debug = result["debug"]
    assert debug is not None
    assert [call["phase"] for call in debug["model_calls"]] == [
        "generation",
        "repair_generation",
        "semantic_validation",
    ]
    assert debug["model_calls"][1]["raw_response"] == "return wf.vars.emails[#wf.vars.emails]"
    assert debug["validation_passes"][1]["runtime_report"]["status"] == "pass"
    assert [call["agent"] for call in model_adapter.agent_calls] == [
        "planner",
        "prompter",
        "generator",
        "prompter",
        "generator",
        "semantic_critic",
    ]
    assert "Return only short additions for the next generator prompt." in model_adapter.agent_calls[3]["messages"][0]["content"]
    assert "Use only the compact repair facts as the source of truth." in model_adapter.agent_calls[4]["messages"][0]["content"]


def test_generation_service_repairs_whole_array_alias_for_last_array_item_before_runtime() -> None:
    model_adapter = CompactAgentProtocolModelAdapter(
        [
            "local emails = wf.vars.emails\nif emails and #emails > 0 then\n\treturn emails\nelse\n\treturn nil\nend",
            "return wf.vars.emails[#wf.vars.emails]",
            SEMANTIC_PASS_RESPONSE,
        ]
    )
    service = LegacyQualityLoopService(model_adapter=model_adapter)

    result = service.generate(
        task_text="Из полученного списка email получи последний.",
        provided_context=json.dumps(
            {
                "wf": {
                    "vars": {
                        "emails": [
                            "user1@example.com",
                            "user2@example.com",
                            "user3@example.com",
                        ]
                    }
                }
            }
        ),
        archetype="simple_extraction",
        output_mode="raw_lua",
        input_roots=["wf.vars.emails"],
        risk_tags=["array_indexing", "empty_array"],
        debug=True,
    )

    assert result["code"] == "return wf.vars.emails[#wf.vars.emails]"
    assert result["validation_status"] == "repaired"
    assert result["trace"] == [
        "request_received",
        "generation",
        "format_validation",
        "rule_validation",
        "critic_step",
        "repair_generation",
        "format_validation",
        "rule_validation",
        "runtime_validation",
        "semantic_validation",
        "finalize",
    ]
    first_iteration = result["validator_report"]["iterations"][0]
    second_iteration = result["validator_report"]["iterations"][1]
    assert first_iteration["principle_report"]["status"] == "fail"
    assert first_iteration["principle_report"]["findings"][0]["failure_class"] == "array_item_returns_whole_array"
    assert first_iteration["runtime_report"]["status"] == "skipped"
    assert first_iteration["runtime_report"]["skipped_reason"] == "prerequisite_validation_failed"
    assert second_iteration["runtime_report"]["status"] == "pass"
    assert second_iteration["semantic_report"]["status"] == "pass"


def test_generation_service_preserves_think_block_and_validates_visible_raw_lua_response() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                "<think>\nNeed to pick the last email.\n</think>\nreturn wf.vars.emails[#wf.vars.emails]\n<|endoftext|><|im_start|>user",
                SEMANTIC_PASS_RESPONSE,
            ]
        )
    )

    result = service.generate(
        task_text="Get the last email from the list.",
        provided_context=json.dumps(
            {
                "wf": {
                    "vars": {
                        "emails": [
                            "user1@example.com",
                            "user2@example.com",
                        ]
                    }
                }
            }
        ),
        archetype="simple_extraction",
        output_mode="raw_lua",
        input_roots=["wf.vars.emails"],
        risk_tags=["array_indexing", "empty_array"],
        debug=True,
    )

    assert result["code"] == "return wf.vars.emails[#wf.vars.emails]"
    assert result["validation_status"] == "passed"
    debug = result["debug"]
    assert debug is not None
    assert debug["model_calls"][0]["raw_response"].startswith("<think>")
    assert debug["model_calls"][0]["response_parts"] == {
        "visible_response": "return wf.vars.emails[#wf.vars.emails]",
        "reasoning_blocks": ["Need to pick the last email."],
        "leading_control_tokens": [],
        "trailing_control_tokens": ["<|endoftext|>", "<|im_start|>"],
        "trailing_auxiliary_text": "<|endoftext|><|im_start|>user",
    }
    assert debug["validation_passes"][0]["candidate"] == "return wf.vars.emails[#wf.vars.emails]"
    assert debug["validation_passes"][0]["format_report"]["status"] == "pass"


def test_generation_service_preserves_think_block_and_validates_visible_json_response() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                "<think>\nNeed to return the last email in a wrapped JSON field.\n</think>\n"
                '{"lastEmail":"lua{return wf.vars.emails[#wf.vars.emails]}lua"}',
                SEMANTIC_PASS_RESPONSE,
            ]
        )
    )

    result = service.generate(
        task_text="Get the last email from the list.",
        provided_context=json.dumps(
            {
                "wf": {
                    "vars": {
                        "emails": [
                            "user1@example.com",
                            "user2@example.com",
                        ]
                    }
                }
            }
        ),
        archetype="simple_extraction",
        output_mode="json_wrapper",
        input_roots=["wf.vars.emails"],
        risk_tags=["array_indexing", "empty_array", "json_wrapper"],
        debug=True,
    )

    assert result["code"] == '{"lastEmail":"lua{return wf.vars.emails[#wf.vars.emails]}lua"}'
    assert result["validation_status"] == "passed"
    debug = result["debug"]
    assert debug is not None
    assert debug["model_calls"][0]["response_parts"]["reasoning_blocks"] == [
        "Need to return the last email in a wrapped JSON field."
    ]
    assert (
        debug["model_calls"][0]["response_parts"]["visible_response"]
        == '{"lastEmail":"lua{return wf.vars.emails[#wf.vars.emails]}lua"}'
    )
    assert debug["validation_passes"][0]["candidate"] == result["code"]
    assert debug["validation_passes"][0]["rule_report"]["status"] == "pass"


def test_generation_service_normalizes_fenced_raw_lua_on_repair_iteration() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                "return wf.data.emails[#wf.data.emails]",
                "```lua\nreturn wf.vars.emails[#wf.vars.emails]\n```",
                SEMANTIC_PASS_RESPONSE,
            ]
        )
    )

    result = service.generate(
        task_text="Get the last email from the list.",
        provided_context=json.dumps(
            {
                "wf": {
                    "vars": {
                        "emails": [
                            "user1@example.com",
                            "user2@example.com",
                        ]
                    }
                }
            }
        ),
        archetype="simple_extraction",
        output_mode="raw_lua",
        input_roots=["wf.vars.emails"],
        risk_tags=["array_indexing", "empty_array"],
        debug=True,
    )

    assert result["code"] == "return wf.vars.emails[#wf.vars.emails]"
    assert result["validation_status"] == "repaired"
    assert result["trace"] == [
        "request_received",
        "generation",
        "format_validation",
        "rule_validation",
        "critic_step",
        "repair_generation",
        "format_validation",
        "rule_validation",
        "runtime_validation",
        "semantic_validation",
        "finalize",
    ]
    assert result["repair_count"] == 1
    assert result["critic_report"] == {
        "action": "repair",
        "failure_class": "invented_data_root",
        "message": "Repair the candidate for failure class invented_data_root without changing the user goal.",
        "repair_prompt": "Исправь текущий кандидат по замечанию валидатора. Сохрани тот же режим вывода, цель пользователя и верни только исправленный результат.",
    }
    debug = result["debug"]
    assert debug is not None
    assert debug["model_calls"][1]["phase"] == "repair_generation"
    assert debug["model_calls"][1]["raw_response"].startswith("```lua")
    assert debug["model_calls"][2]["phase"] == "semantic_validation"
    assert debug["validation_passes"][0]["rule_report"]["findings"][0]["failure_class"] == "invented_data_root"
    assert debug["validation_passes"][1]["format_report"]["status"] == "pass"
    assert debug["validation_passes"][1]["normalized_candidate"] == result["code"]


def test_generation_service_repairs_missing_array_allocator_with_tool() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                "\n".join(
                    [
                        "local filteredData = {}",
                        "for _, item in ipairs(wf.vars.parsedCsv) do",
                        '    if (item.Discount ~= nil and item.Discount ~= "") or (item.Markdown ~= nil and item.Markdown ~= "") then',
                        "        table.insert(filteredData, item)",
                        "    end",
                        "end",
                        "",
                        "return filteredData",
                    ]
                ),
                SEMANTIC_PASS_RESPONSE,
                SEMANTIC_PASS_RESPONSE,
            ]
        )
    )

    result = service.generate(
        task_text="Filter items that have either Discount or Markdown set.",
        provided_context=json.dumps(
            {
                "wf": {
                    "vars": {
                        "parsedCsv": [
                            {"SKU": "A001", "Discount": "10%", "Markdown": ""},
                            {"SKU": "A002", "Discount": "", "Markdown": "5%"},
                            {"SKU": "A003", "Discount": None, "Markdown": None},
                            {"SKU": "A004", "Discount": "", "Markdown": ""},
                        ]
                    }
                }
            }
        ),
        archetype="filtering",
        output_mode="raw_lua",
        input_roots=["wf.vars.parsedCsv"],
        risk_tags=["array_allocation", "empty_value_filtering", "nil_handling"],
        debug=True,
    )

    assert result["code"] == "\n".join(
        [
            "local filteredData = _utils.array.new()",
            "for _, item in ipairs(wf.vars.parsedCsv) do",
            '    if (item.Discount ~= nil and item.Discount ~= "") or (item.Markdown ~= nil and item.Markdown ~= "") then',
            "        table.insert(filteredData, item)",
            "    end",
            "end",
            "",
            "return filteredData",
        ]
    )
    assert result["validation_status"] == "repaired"
    assert result["trace"] == [
        "request_received",
        "generation",
        "format_validation",
        "rule_validation",
        "critic_step",
        "repair_generation",
        "format_validation",
        "rule_validation",
        "semantic_validation",
        "finalize",
    ]
    assert result["repair_count"] == 1
    assert result["critic_report"] == {
        "action": "repair",
        "failure_class": "missing_array_allocator",
        "message": "Добавь недостающий доменный элемент, не меняя требуемую форму результата.",
        "repair_prompt": "Исправь кандидат, добавив недостающую доменную логику, сохранив требуемый режим вывода и цель пользователя.",
    }
    debug = result["debug"]
    assert debug is not None
    assert debug["validation_passes"][0]["rule_report"]["findings"][0]["failure_class"] == "missing_array_allocator"
    assert debug["validation_passes"][1]["normalized_candidate"] == result["code"]
    assert debug["model_calls"][1]["phase"] == "repair_generation"
    assert debug["model_calls"][1]["raw_response"] == result["code"]
    assert debug["model_calls"][2]["phase"] == "semantic_validation"


def test_generation_service_returns_clarification_for_ambiguous_roots() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                "Which data root should I use: wf.vars.emails or wf.initVariables.recallTime?"
            ]
        )
    )

    result = service.generate(
        task_text="Convert the value and return the result.",
        provided_context=json.dumps(
            {
                "wf": {
                    "vars": {"emails": ["user@example.com"]},
                    "initVariables": {"recallTime": "2023-10-15T15:30:00+00:00"},
                }
            }
        ),
        archetype="datetime_conversion",
        output_mode="raw_lua",
        risk_tags=["init_variables", "timezone_offset"],
    )

    assert result["code"] == "Which data root should I use: wf.vars.emails or wf.initVariables.recallTime?"
    assert result["validation_status"] == "clarification_requested"
    assert result["repair_count"] == 0
    assert result["clarification_count"] == 1
    assert result["output_mode"] == "clarification"
    assert result["trace"] == [
        "request_received",
        "generation",
        "format_validation",
        "clarification",
    ]
    assert result["validator_report"]["status"] == "pass"


def test_generation_service_asks_for_feedback_after_repeated_invalid_shape_after_repair() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                "Here is the repaired code:\n```lua\nreturn wf.vars.emails[#wf.vars.emails]\n```",
                "Here is the repaired code:\n```lua\nreturn wf.vars.emails[#wf.vars.emails]\n```",
            ]
        )
    )

    result = service.generate(
        task_text="Get the last email from the list.",
        provided_context=json.dumps(
            {
                "wf": {
                    "vars": {
                        "emails": [
                            "user1@example.com",
                            "user2@example.com",
                        ]
                    }
                }
            }
        ),
        archetype="simple_extraction",
        output_mode="raw_lua",
        input_roots=["wf.vars.emails"],
        risk_tags=["array_indexing", "empty_array"],
    )

    assert result["validation_status"] == "clarification_requested"
    assert result["stop_reason"] == "clarification_requested"
    assert result["repair_count"] == 1
    assert result["clarification_count"] == 1
    assert result["critic_report"] == {
        "action": "clarification",
        "failure_class": "repair_oscillation",
        "message": "Цикл исправления начал зацикливаться на уже встречавшихся вариантах или типах ошибок.",
        "clarification_question": "Цикл исправления повторяет ту же ошибку. Что нужно изменить перед следующей попыткой?",
    }
    assert result["validator_report"]["status"] == "fail"


def test_generation_service_asks_for_feedback_after_four_non_oscillating_repairs() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                "return wf.vars.emails[1]",
                "return wf.vars.emails",
                "return nil",
                'return "wrong"',
                "return 42",
            ]
        )
    )

    result = service.generate(
        task_text="Get the last email from the list.",
        provided_context=json.dumps(
            {
                "wf": {
                    "vars": {
                        "emails": [
                            "user1@example.com",
                            "user2@example.com",
                        ]
                    }
                }
            }
        ),
        archetype="simple_extraction",
        output_mode="raw_lua",
        input_roots=["wf.vars.emails"],
        risk_tags=["array_indexing", "empty_array"],
    )

    assert result["validation_status"] == "clarification_requested"
    assert result["stop_reason"] == "clarification_requested"
    assert result["repair_count"] == 4
    assert result["clarification_count"] == 1
    assert result["critic_report"] == {
        "action": "clarification",
        "failure_class": "runtime_behavior_mismatch",
        "message": "Лимит исправлений исчерпан или после последнего исправления снова повторилась та же ошибка.",
        "clarification_question": "Return the last element from wf.vars.emails and nil when the array is empty.",
    }


def test_generation_service_prefers_semantic_intent_for_clear_field_conflict() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                '\n'.join(
                    [
                        'local filteredEntry = wf.vars.restBody[1]',
                        'for _, value in pairs(filteredEntry) do',
                        '  value = nil',
                        'end',
                        'return filteredEntry',
                    ]
                ),
                (
                    '{"status":"fail","failure_class":"semantic_mismatch",'
                    '"message":"The task expects preserving untouched fields while clearing only the target values.",'
                    '"repairable":true,"ambiguous":false,'
                    '"suggestion":"Set ID, ENTITY_ID, and CALL to nil directly and keep all other fields untouched."}'
                ),
                '\n'.join(
                    [
                        'local filteredEntry = wf.vars.restBody[1]',
                        'filteredEntry["ID"] = nil',
                        'filteredEntry["ENTITY_ID"] = nil',
                        'filteredEntry["CALL"] = nil',
                        'return filteredEntry',
                    ]
                ),
                SEMANTIC_PASS_RESPONSE,
            ]
        )
    )

    result = service.generate(
        task_text="Очисти значения полей ID, ENTITY_ID и CALL в первом элементе restBody, остальные поля не трогай.",
        provided_context=json.dumps(
            {
                "wf": {
                    "vars": {
                        "restBody": [
                            {
                                "ID": "123",
                                "ENTITY_ID": "456",
                                "CALL": "789",
                                "NAME": "Alice",
                            }
                        ]
                    }
                }
            }
        ),
        archetype="transformation",
        output_mode="raw_lua",
        input_roots=["wf.vars.restBody"],
        risk_tags=["field_whitelist"],
        debug=True,
    )

    assert result["validation_status"] == "repaired"
    assert result["repair_count"] == 1
    assert result["trace"] == [
        "request_received",
        "generation",
        "format_validation",
        "rule_validation",
        "semantic_validation",
        "critic_step",
        "repair_generation",
        "format_validation",
        "rule_validation",
        "semantic_validation",
        "finalize",
    ]
    assert result["critic_report"] == {
        "action": "repair",
        "failure_class": "semantic_mismatch",
        "message": "Для задач с явными операциями над полями предпочитай семантическое намерение, а не шаблонное правило.",
        "repair_prompt": "Set ID, ENTITY_ID, and CALL to nil directly and keep all other fields untouched.",
    }
    first_iteration = result["validator_report"]["iterations"][0]
    assert first_iteration["principle_report"]["status"] == "fail"
    assert first_iteration["principle_report"]["findings"][0]["failure_class"] == "missing_field_whitelist_pattern"
    assert first_iteration["semantic_report"]["status"] == "fail"
    assert first_iteration["semantic_report"]["findings"][0]["failure_class"] == "semantic_mismatch"


def test_generation_service_returns_validator_conflict_for_unresolved_pattern_vs_semantic_disagreement() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                '\n'.join(
                    [
                        'local filteredEntry = wf.vars.restBody[1]',
                        'for _, value in pairs(filteredEntry) do',
                        '  value = nil',
                        'end',
                        'return filteredEntry',
                    ]
                ),
                (
                    '{"status":"fail","failure_class":"semantic_mismatch",'
                    '"message":"The candidate does not implement the requested field operation.",'
                    '"repairable":true,"ambiguous":false,'
                    '"suggestion":"Use an explicit key-preservation strategy for the requested fields."}'
                ),
            ]
        )
    )

    result = service.generate(
        task_text="Обработай поля ID, ENTITY_ID и CALL в первом элементе restBody.",
        provided_context=json.dumps(
            {
                "wf": {
                    "vars": {
                        "restBody": [
                            {
                                "ID": "123",
                                "ENTITY_ID": "456",
                                "CALL": "789",
                                "NAME": "Alice",
                            }
                        ]
                    }
                }
            }
        ),
        archetype="transformation",
        output_mode="raw_lua",
        input_roots=["wf.vars.restBody"],
        risk_tags=["field_whitelist"],
        debug=True,
    )

    assert result["validation_status"] == "validator_conflict"
    assert result["repair_count"] == 0
    assert result["critic_report"] == {
        "action": "finalize",
        "failure_class": "validator_conflict",
        "message": "Семантический и шаблонный валидаторы расходятся в направлении исправления.",
    }


def test_generation_service_accepts_direct_named_field_clearing_when_semantics_pass() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                '\n'.join(
                    [
                        'local result = wf.vars.RESTbody.result',
                        'for _, filteredEntry in pairs(result) do',
                        '  filteredEntry["ID"] = nil',
                        '  filteredEntry["ENTITY_ID"] = nil',
                        '  filteredEntry["CALL"] = nil',
                        'end',
                        '',
                        'return result',
                    ]
                ),
                SEMANTIC_PASS_RESPONSE,
            ]
        )
    )

    result = service.generate(
        task_text="Очисти значения полей ID, ENTITY_ID и CALL в result, остальные поля не трогай.",
        provided_context=json.dumps(
            {
                "wf": {
                    "vars": {
                        "RESTbody": {
                            "result": [
                                {
                                    "ID": "123",
                                    "ENTITY_ID": "456",
                                    "CALL": "789",
                                    "NAME": "Alice",
                                }
                            ]
                        }
                    }
                }
            }
        ),
        archetype="transformation",
        output_mode="raw_lua",
        input_roots=["wf.vars.RESTbody.result"],
        risk_tags=["field_whitelist"],
        debug=True,
    )

    assert result["validation_status"] == "passed"
    assert result["trace"] == [
        "request_received",
        "generation",
        "format_validation",
        "rule_validation",
        "semantic_validation",
        "finalize",
    ]
    first_iteration = result["validator_report"]["iterations"][0]
    assert first_iteration["principle_report"]["status"] == "pass"
    assert first_iteration["semantic_report"]["status"] == "pass"


def test_generation_service_asks_for_feedback_when_candidate_returns_to_previous_shape() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                "return wf.data.emails[1]",
                "return wf.vars.emails[1]",
                "return wf.data.emails[1]",
            ]
        )
    )

    result = service.generate(
        task_text="Get the last email from the list.",
        provided_context=json.dumps(
            {
                "wf": {
                    "vars": {
                        "emails": [
                            "user1@example.com",
                            "user2@example.com",
                        ]
                    }
                }
            }
        ),
        archetype="simple_extraction",
        output_mode="raw_lua",
        input_roots=["wf.vars.emails"],
        risk_tags=["array_indexing", "empty_array"],
    )

    assert result["validation_status"] == "clarification_requested"
    assert result["stop_reason"] == "clarification_requested"
    assert result["repair_count"] == 2
    assert result["clarification_count"] == 1
    assert result["critic_report"] == {
        "action": "clarification",
        "failure_class": "repair_oscillation",
        "message": "Цикл исправления начал зацикливаться на уже встречавшихся вариантах или типах ошибок.",
        "clarification_question": "Цикл исправления повторяет ту же ошибку. Что нужно изменить перед следующей попыткой?",
    }


def test_generation_service_asks_for_feedback_when_runtime_behavior_repeats() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                "return wf.vars.emails[1]",
                "\n".join(
                    [
                        "local emails = wf.vars.emails",
                        "return emails[1]",
                    ]
                ),
            ]
        )
    )

    result = service.generate(
        task_text="Get the last email from the list.",
        provided_context=json.dumps(
            {
                "wf": {
                    "vars": {
                        "emails": [
                            "user1@example.com",
                            "user2@example.com",
                        ]
                    }
                }
            }
        ),
        archetype="simple_extraction",
        output_mode="raw_lua",
        input_roots=["wf.vars.emails"],
        risk_tags=["array_indexing", "empty_array"],
    )

    assert result["validation_status"] == "clarification_requested"
    assert result["stop_reason"] == "clarification_requested"
    assert result["repair_count"] == 1
    assert result["clarification_count"] == 1
    assert result["critic_report"] == {
        "action": "clarification",
        "failure_class": "repair_oscillation",
        "message": "Цикл исправления начал зацикливаться на уже встречавшихся вариантах или типах ошибок.",
        "clarification_question": "Цикл исправления повторяет ту же ошибку. Что нужно изменить перед следующей попыткой?",
    }


def test_generation_service_asks_for_feedback_after_three_repair_attempts_with_repeated_shape() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                "return wf.vars.emails[1]",
                "{}",
                "",
                "",
            ]
        )
    )

    result = service.generate(
        task_text="Get the last email from the list.",
        provided_context=json.dumps(
            {
                "wf": {
                    "vars": {
                        "emails": [
                            "user1@example.com",
                            "user2@example.com",
                        ]
                    }
                }
            }
        ),
        archetype="simple_extraction",
        output_mode="raw_lua",
        input_roots=["wf.vars.emails"],
        risk_tags=["array_indexing", "empty_array"],
    )

    assert result["validation_status"] == "clarification_requested"
    assert result["stop_reason"] == "clarification_requested"
    assert result["repair_count"] == 3
    assert result["clarification_count"] == 1
    assert result["code"] == "Цикл исправления повторяет ту же ошибку. Что нужно изменить перед следующей попыткой?"
    assert result["final_candidate_source"] == "clarification_question"
    assert result["critic_report_iteration_index"] == 3
    assert result["critic_report"] == {
        "action": "clarification",
        "failure_class": "repair_oscillation",
        "message": "Цикл исправления начал зацикливаться на уже встречавшихся вариантах или типах ошибок.",
        "clarification_question": "Цикл исправления повторяет ту же ошибку. Что нужно изменить перед следующей попыткой?",
    }


def test_detect_repair_oscillation_marks_repeated_invalid_shape_signature() -> None:
    assert _detect_repair_oscillation(
        current_fingerprint="candidate-b",
        current_behavioral_fingerprint=None,
        current_invalid_shape_signature="invalid_json:response:Candidate is not valid JSON.",
        current_disallowed_root_signature=None,
        current_failure_class=None,
        prior_fingerprints=["candidate-a"],
        behavioral_history=[],
        invalid_shape_history=["invalid_json:response:Candidate is not valid JSON."],
        disallowed_root_history=[],
        failure_history=[],
    )


def test_detect_repair_oscillation_marks_repeated_disallowed_root_signature() -> None:
    assert _detect_repair_oscillation(
        current_fingerprint="candidate-b",
        current_behavioral_fingerprint=None,
        current_invalid_shape_signature=None,
        current_disallowed_root_signature=(
            "disallowed_data_root:response:Candidate references wf.data.emails instead of wf.vars.emails."
        ),
        current_failure_class=None,
        prior_fingerprints=["candidate-a"],
        behavioral_history=[],
        invalid_shape_history=[],
        disallowed_root_history=[
            "disallowed_data_root:response:Candidate references wf.data.emails instead of wf.vars.emails."
        ],
        failure_history=[],
    )


def test_generation_service_accepts_domain_datetime_helper_for_unix_conversion() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                "return parse_iso8601_to_epoch(wf.initVariables.remindAt)",
                (
                    '{"status":"fail","failure_class":"semantic_mismatch","message":"Missing explicit timezone parsing.",'
                    '"repairable":true,"ambiguous":false,"suggestion":"Parse offset fields manually."}'
                ),
            ]
        )
    )

    result = service.generate(
        task_text="Преобразуй remindAt из initVariables в unix-время.",
        provided_context=json.dumps({"wf": {"initVariables": {"remindAt": "2024-02-01T10:00:00+03:00"}}}),
        archetype="datetime_conversion",
        output_mode="raw_lua",
        input_roots=["wf.initVariables.remindAt"],
        risk_tags=["datetime_conversion", "init_variables", "timezone_offset"],
        debug=True,
    )

    assert result["validation_status"] == "passed"
    first_iteration = result["validator_report"]["iterations"][0]
    assert first_iteration["static_report"]["status"] == "pass"
    assert first_iteration["principle_report"]["status"] == "pass"
    assert first_iteration["semantic_report"]["status"] == "pass"


def test_generation_service_accepts_count_until_first_success_inclusive_false_positive() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                "\n".join(
                    [
                        "local count = 0",
                        "for _, attempt in ipairs(wf.vars.attempts) do",
                        "  count = count + 1",
                        "  if attempt.success then",
                        "    break",
                        "  end",
                        "end",
                        "return count",
                    ]
                ),
                (
                    '{"status":"fail","failure_class":"semantic_mismatch","message":"Off by one.",'
                    '"repairable":true,"ambiguous":false,"suggestion":"Include the successful attempt."}'
                ),
            ]
        )
    )

    result = service.generate(
        task_text="Посчитай количество попыток до первого успешного результата включительно.",
        provided_context=json.dumps({"wf": {"vars": {"attempts": [{"success": False}, {"success": True}]}}}),
        archetype="transformation",
        output_mode="raw_lua",
        input_roots=["wf.vars.attempts"],
        risk_tags=["numeric_transform"],
        debug=True,
    )

    assert result["validation_status"] == "passed"
    assert result["validator_report"]["iterations"][0]["semantic_report"]["status"] == "pass"


def test_generation_service_accepts_always_array_normalization_false_positive() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                "\n".join(
                    [
                        "local tags = wf.vars.tags",
                        "if tags == nil then",
                        "  return {}",
                        "end",
                        'if type(tags) ~= "table" then',
                        "  return {tags}",
                        "end",
                        "for key, _ in pairs(tags) do",
                        '  if type(key) ~= "number" then',
                        "    return {tags}",
                        "  end",
                        "end",
                        "return tags",
                    ]
                ),
                (
                    '{"status":"fail","failure_class":"semantic_mismatch","message":"Does not normalize non-array tables.",'
                    '"repairable":true,"ambiguous":false,"suggestion":"Wrap non-array tables."}'
                ),
            ]
        )
    )

    result = service.generate(
        task_text="Если tags не является массивом, оберни значение в массив.",
        provided_context=json.dumps({"wf": {"vars": {"tags": {"primary": "vip"}}}}),
        archetype="normalization",
        output_mode="raw_lua",
        input_roots=["wf.vars.tags"],
        risk_tags=["array_semantics", "type_normalization"],
        debug=True,
    )

    assert result["validation_status"] == "passed"
    assert result["validator_report"]["iterations"][0]["semantic_report"]["status"] == "pass"


def test_generation_service_accepts_error_code_array_projection_false_positive() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                "\n".join(
                    [
                        "local result = _utils.array.new()",
                        "for _, error in ipairs(wf.vars.errors) do",
                        '  if error.code ~= nil and error.code ~= "" then',
                        "    table.insert(result, error.code)",
                        "  end",
                        "end",
                        "return result",
                    ]
                ),
                (
                    '{"status":"fail","failure_class":"semantic_mismatch","message":"Returns full error objects.",'
                    '"repairable":true,"ambiguous":false,"suggestion":"Insert only error.code."}'
                ),
            ]
        )
    )

    result = service.generate(
        task_text="Из массива errors собери массив code только для записей, у которых code не пустой.",
        provided_context=json.dumps({"wf": {"vars": {"errors": [{"code": "E1"}, {"code": ""}]}}}),
        archetype="filtering",
        output_mode="raw_lua",
        input_roots=["wf.vars.errors"],
        risk_tags=["array_allocation", "nil_handling", "empty_value_filtering"],
        debug=True,
    )

    assert result["validation_status"] == "passed"
    assert result["validator_report"]["iterations"][0]["semantic_report"]["status"] == "pass"


def test_generation_service_keeps_error_object_array_projection_failure() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                "\n".join(
                    [
                        "local result = _utils.array.new()",
                        "for _, error in ipairs(wf.vars.errors) do",
                        '  if error.code ~= nil and error.code ~= "" then',
                        "    table.insert(result, error)",
                        "  end",
                        "end",
                        "return result",
                    ]
                ),
                (
                    '{"status":"fail","failure_class":"semantic_mismatch","message":"Returns full error objects.",'
                    '"repairable":false,"ambiguous":false,"suggestion":"Insert only error.code."}'
                ),
            ]
        )
    )

    result = service.generate(
        task_text="Из массива errors собери массив code только для записей, у которых code не пустой.",
        provided_context=json.dumps({"wf": {"vars": {"errors": [{"code": "E1"}]}}}),
        archetype="filtering",
        output_mode="raw_lua",
        input_roots=["wf.vars.errors"],
        risk_tags=["array_allocation", "nil_handling", "empty_value_filtering"],
        debug=True,
    )

    assert result["validation_status"] == "clarification_requested"
    assert result["validator_report"]["iterations"][0]["semantic_report"]["status"] == "fail"


def test_generation_service_accepts_file_meta_direct_projection_false_positive() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                "\n".join(
                    [
                        "return {",
                        "  name = wf.vars.fileMeta.name,",
                        "  extension = wf.vars.fileMeta.extension,",
                        "  size = wf.vars.fileMeta.size",
                        "}",
                    ]
                ),
                (
                    '{"status":"fail","failure_class":"semantic_mismatch","message":"Must stay under fileMeta.",'
                    '"repairable":true,"ambiguous":false,"suggestion":"Return direct fields."}'
                ),
            ]
        )
    )

    result = service.generate(
        task_text="Из объекта fileMeta оставь только name, extension и size.",
        provided_context=json.dumps({"wf": {"vars": {"fileMeta": {"name": "a.png", "extension": "png", "size": 10}}}}),
        archetype="transformation",
        output_mode="raw_lua",
        input_roots=["wf.vars.fileMeta"],
        risk_tags=[],
        debug=True,
    )

    assert result["validation_status"] == "passed"
    assert result["validator_report"]["iterations"][0]["semantic_report"]["status"] == "pass"


def test_generation_service_accepts_manager_name_fallback_false_positive() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                'return wf.vars.team.manager and wf.vars.team.manager.name or "no-manager"',
                (
                    '{"status":"fail","failure_class":"semantic_mismatch","message":"Missing name returns nil.",'
                    '"repairable":true,"ambiguous":false,"suggestion":"Use fallback."}'
                ),
            ]
        )
    )

    result = service.generate(
        task_text='Верни manager.name, а если manager отсутствует — строку "no-manager".',
        provided_context=json.dumps({"wf": {"vars": {"team": {"manager": {"name": "Ann"}}}}}),
        archetype="simple_extraction",
        output_mode="raw_lua",
        input_roots=["wf.vars.team.manager"],
        risk_tags=["nil_handling", "empty_value_filtering"],
        debug=True,
    )

    assert result["validation_status"] == "passed"
    assert result["validator_report"]["iterations"][0]["semantic_report"]["status"] == "pass"


def test_generation_service_accepts_date_ru_iso_reorder_false_positive() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                "\n".join(
                    [
                        "local dateRu = wf.vars.dateRu",
                        "local day = string.sub(dateRu, 1, 2)",
                        "local month = string.sub(dateRu, 4, 5)",
                        "local year = string.sub(dateRu, 7, 10)",
                        'return string.format("%s-%s-%s", year, month, day)',
                    ]
                ),
                (
                    '{"status":"fail","failure_class":"semantic_mismatch","message":"Fields day and month are swapped.",'
                    '"repairable":true,"ambiguous":false,"suggestion":"Use year, month, day."}'
                ),
            ]
        )
    )

    result = service.generate(
        task_text='Преобразуй dateRu из формата "DD.MM.YYYY" в "YYYY-MM-DD".',
        provided_context=json.dumps({"wf": {"vars": {"dateRu": "31.12.2024"}}}),
        archetype="transformation",
        output_mode="raw_lua",
        input_roots=["wf.vars.dateRu"],
        risk_tags=["substring_bounds"],
        debug=True,
    )

    assert result["validation_status"] == "passed"
    assert result["validator_report"]["iterations"][0]["semantic_report"]["status"] == "pass"


def test_generation_service_accepts_nil_tags_empty_array_false_positive() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                "\n".join(
                    [
                        "local result = _utils.array.new()",
                        "if wf.vars.tags ~= nil then",
                        "  for _, tag in ipairs(wf.vars.tags) do",
                        "    table.insert(result, tag)",
                        "  end",
                        "end",
                        "return result",
                    ]
                ),
                (
                    '{"status":"fail","failure_class":"semantic_mismatch","message":"Unnecessary empty array branch.",'
                    '"repairable":true,"ambiguous":false,"suggestion":"Return empty array for nil tags."}'
                ),
            ]
        )
    )

    result = service.generate(
        task_text="Верни массив tags, а если переменная tags равна nil — верни пустой массив.",
        provided_context=json.dumps({"wf": {"vars": {"tags": None}}}),
        archetype="filtering",
        output_mode="raw_lua",
        input_roots=["wf.vars.tags"],
        risk_tags=["array_allocation", "nil_handling"],
        debug=True,
    )

    assert result["validation_status"] == "passed"
    assert result["validator_report"]["iterations"][0]["semantic_report"]["status"] == "pass"


def test_generation_service_accepts_strict_type_count_false_positive() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                "\n".join(
                    [
                        "local count = 0",
                        "for _, row in ipairs(wf.vars.rows) do",
                        '  if type(row.fieldA) == "string" and type(row.fieldB) == "number" then',
                        "    count = count + 1",
                        "  end",
                        "end",
                        "return count",
                    ]
                ),
                (
                    '{"status":"fail","failure_class":"semantic_mismatch","message":"Should accept numeric strings.",'
                    '"repairable":true,"ambiguous":false,"suggestion":"Use tonumber(fieldB)."}'
                ),
            ]
        )
    )

    result = service.generate(
        task_text="Посчитай количество строк, где fieldA является строкой, а fieldB является числом.",
        provided_context=json.dumps({"wf": {"vars": {"rows": [{"fieldA": "ok", "fieldB": 1}]}}}),
        archetype="transformation",
        output_mode="raw_lua",
        input_roots=["wf.vars.rows"],
        risk_tags=["numeric_transform"],
        debug=True,
    )

    assert result["validation_status"] == "passed"
    assert result["validator_report"]["iterations"][0]["semantic_report"]["status"] == "pass"


def test_generation_service_accepts_iso_date_string_compare_false_positive() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                "\n".join(
                    [
                        "local invoices = wf.vars.invoices",
                        "local currentDate = wf.initVariables.currentDate",
                        "for _, invoice in ipairs(invoices) do",
                        "  if invoice.dueDate and invoice.dueDate < currentDate then",
                        "    return invoice",
                        "  end",
                        "end",
                        "return nil",
                    ]
                ),
                (
                    '{"status":"fail","failure_class":"semantic_mismatch","message":"String comparison used for date comparison.",'
                    '"repairable":true,"ambiguous":false,"suggestion":"Parse dates as timestamps."}'
                ),
            ]
        )
    )

    result = service.generate(
        task_text="Найди первый просроченный invoice, у которого dueDate меньше currentDate.",
        provided_context=json.dumps(
            {
                "wf": {
                    "vars": {"invoices": [{"dueDate": "2026-04-01"}]},
                    "initVariables": {"currentDate": "2026-04-05"},
                }
            }
        ),
        archetype="transformation",
        output_mode="raw_lua",
        input_roots=["wf.initVariables.currentDate", "wf.vars.invoices"],
        risk_tags=["nil_handling", "init_variables"],
        debug=True,
    )

    assert result["validation_status"] == "passed"
    assert result["validator_report"]["iterations"][0]["semantic_report"]["status"] == "pass"


def test_generation_service_accepts_dd_mm_yyyy_iso_conversion_false_positive() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                "\n".join(
                    [
                        "local date = wf.vars.deliveryDate",
                        'local day, month, year = string.match(date, "(%d%d)%.(%d%d)%.(%d%d%d%d)")',
                        'return string.format("%s-%s-%s", year, month, day)',
                    ]
                ),
                (
                    '{"status":"fail","failure_class":"semantic_mismatch","message":"Missing date range validation.",'
                    '"repairable":true,"ambiguous":false,"suggestion":"Validate month and day ranges."}'
                ),
            ]
        )
    )

    result = service.generate(
        task_text="Преобразуй дату из формата DD.MM.YYYY в YYYY-MM-DD.",
        provided_context=json.dumps({"wf": {"vars": {"deliveryDate": "15.04.2026"}}}),
        archetype="datetime_conversion",
        output_mode="raw_lua",
        input_roots=["wf.vars.deliveryDate"],
        risk_tags=[],
        debug=True,
    )

    assert result["validation_status"] == "passed"
    assert result["validator_report"]["iterations"][0]["semantic_report"]["status"] == "pass"


def test_generation_service_accepts_nested_tax_code_array_projection_false_positive() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                "\n".join(
                    [
                        "local result = _utils.array.new()",
                        "for _, invoice in ipairs(wf.vars.invoices) do",
                        "  for _, line in ipairs(invoice.lines) do",
                        "    table.insert(result, line.taxCode)",
                        "  end",
                        "end",
                        "return result",
                    ]
                ),
                (
                    '{"status":"fail","failure_class":"semantic_mismatch","message":"The result array is not initialized.",'
                    '"repairable":true,"ambiguous":false,"suggestion":"Initialize result before table.insert."}'
                ),
            ]
        )
    )

    result = service.generate(
        task_text="Из массива invoices собери единый массив taxCode из всех lines.",
        provided_context=json.dumps({"wf": {"vars": {"invoices": [{"lines": [{"taxCode": "T1"}]}]}}}),
        archetype="filtering",
        output_mode="raw_lua",
        input_roots=["wf.vars.invoices"],
        risk_tags=["array_allocation"],
        debug=True,
    )

    assert result["validation_status"] == "passed"
    assert result["validator_report"]["iterations"][0]["semantic_report"]["status"] == "pass"


def test_generation_service_accepts_in_place_array_field_enrichment_false_positive() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                "\n".join(
                    [
                        "for _, file in ipairs(wf.vars.files) do",
                        '  file.isImage = (file.extension == "png" or file.extension == "jpg")',
                        "end",
                        "return wf.vars.files",
                    ]
                ),
                (
                    '{"status":"fail","failure_class":"semantic_mismatch","message":"Must allocate a new result array.",'
                    '"repairable":true,"ambiguous":false,"suggestion":"Use _utils.array.new()."}'
                ),
            ]
        )
    )

    result = service.generate(
        task_text=(
            "Для каждого файла в files добавь boolean-поле isImage: true, если extension равно png или jpg, "
            "иначе false. Можно обновлять исходные объекты."
        ),
        provided_context=json.dumps({"wf": {"vars": {"files": [{"extension": "png"}, {"extension": "pdf"}]}}}),
        archetype="filtering",
        output_mode="raw_lua",
        input_roots=["wf.vars.files"],
        risk_tags=["array_allocation"],
        debug=True,
    )

    assert result["validation_status"] == "passed"
    first_iteration = result["validator_report"]["iterations"][0]
    assert first_iteration["principle_report"]["status"] == "pass"
    assert first_iteration["semantic_report"]["status"] == "pass"


def test_generation_service_accepts_alias_conditional_in_place_array_field_enrichment_false_positive() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                "\n".join(
                    [
                        "local files = wf.vars.files",
                        "for _, file in ipairs(files) do",
                        '  if file.extension == "png" or file.extension == "jpg" then',
                        "    file.isImage = true",
                        "  else",
                        "    file.isImage = false",
                        "  end",
                        "end",
                        "return files",
                    ]
                ),
                (
                    '{"status":"fail","failure_class":"semantic_mismatch","message":"Must allocate a new result array.",'
                    '"repairable":true,"ambiguous":false,"suggestion":"Use _utils.array.new()."}'
                ),
            ]
        )
    )

    result = service.generate(
        task_text=(
            "Для каждого файла в files добавь boolean-поле isImage: true, если extension равно png или jpg, "
            "иначе false. Можно обновлять исходные объекты."
        ),
        provided_context=json.dumps({"wf": {"vars": {"files": [{"extension": "png"}, {"extension": "pdf"}]}}}),
        archetype="filtering",
        output_mode="raw_lua",
        input_roots=["wf.vars.files"],
        risk_tags=["array_allocation"],
        debug=True,
    )

    assert result["validation_status"] == "passed"
    first_iteration = result["validator_report"]["iterations"][0]
    assert first_iteration["principle_report"]["status"] == "pass"
    assert first_iteration["semantic_report"]["status"] == "pass"


def test_generation_service_keeps_object_rebuild_field_loss_failure() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                "\n".join(
                    [
                        "local result = _utils.array.new()",
                        "for _, product in ipairs(wf.vars.products) do",
                        "  local normalizedSku = string.upper(product.sku)",
                        "  table.insert(result, {",
                        "    sku = product.sku,",
                        "    normalizedSku = normalizedSku",
                        "  })",
                        "end",
                        "return result",
                    ]
                ),
                (
                    '{"status":"fail","failure_class":"semantic_mismatch","message":"Drops existing product fields.",'
                    '"repairable":false,"ambiguous":false,"suggestion":"Preserve the original product object."}'
                ),
            ]
        )
    )

    result = service.generate(
        task_text="Для каждого продукта в products добавь поле normalizedSku в верхнем регистре.",
        provided_context=json.dumps({"wf": {"vars": {"products": [{"sku": "ab-1", "name": "A"}]}}}),
        archetype="filtering",
        output_mode="raw_lua",
        input_roots=["wf.vars.products"],
        risk_tags=["array_allocation"],
        debug=True,
    )

    assert result["validation_status"] == "clarification_requested"
    assert result["validator_report"]["iterations"][0]["semantic_report"]["status"] == "fail"


def test_generation_service_repairs_patch_mode_path_keys_with_tool() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                '{"wf.vars.squared":"lua{return wf.vars.number ^ 2}lua"}',
                SEMANTIC_PASS_RESPONSE,
                SEMANTIC_PASS_RESPONSE,
            ]
        )
    )

    result = service.generate(
        task_text="Добавь переменную с квадратом числа.",
        provided_context=json.dumps({"wf": {"vars": {"number": 5}}}),
        archetype="transformation",
        output_mode="patch_mode",
        input_roots=["wf.vars.number"],
        risk_tags=["patch_payload", "numeric_transform", "no_full_rewrite"],
        debug=True,
    )

    assert result["validation_status"] == "repaired"
    assert result["code"] == '{"squared":"lua{return wf.vars.number ^ 2}lua"}'
    assert any(call.get("repair_source") == "deterministic_tool" for call in result["debug"]["model_calls"])


def test_generation_service_repairs_nested_full_rewrite_patch_payload_with_tool() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                '{"wf":{"vars":{"squared":"lua{return wf.vars.number ^ 2}lua"}}}',
                SEMANTIC_PASS_RESPONSE,
                SEMANTIC_PASS_RESPONSE,
            ]
        )
    )

    result = service.generate(
        task_text="Добавь переменную с квадратом числа.",
        provided_context=json.dumps({"wf": {"vars": {"number": 5}}}),
        archetype="transformation",
        output_mode="patch_mode",
        input_roots=["wf.vars.number"],
        risk_tags=["patch_payload", "numeric_transform", "no_full_rewrite"],
        debug=True,
    )

    assert result["validation_status"] == "repaired"
    assert result["code"] == '{"squared":"lua{return wf.vars.number ^ 2}lua"}'
    assert any(call.get("repair_source") == "deterministic_tool" for call in result["debug"]["model_calls"])


def test_generation_service_exposes_layered_reports_before_critic() -> None:
    service = LegacyQualityLoopService(
        model_adapter=ScriptedModelAdapter(
            [
                "local iso_time = wf.initVariables.recallTime\nreturn parse_iso8601_to_epoch(iso_time)",
                SEMANTIC_PASS_RESPONSE,
                "\n".join(
                    [
                        "local iso_time = wf.initVariables.recallTime",
                        "local days_in_month = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31}",
                        "if not iso_time or not iso_time:match(\"^%d%d%d%d%-%d%d%-%d%dT\") then",
                        "  return nil",
                        "end",
                        "local year, month, day, hour, min, sec, offset_sign, offset_hour, offset_min =",
                        "  iso_time:match(\"(%d+)%-(%d+)%-(%d+)T(%d+):(%d+):(%d+)([+-])(%d+):(%d+)\")",
                        "year = tonumber(year)",
                        "month = tonumber(month)",
                        "day = tonumber(day)",
                        "hour = tonumber(hour)",
                        "min = tonumber(min)",
                        "sec = tonumber(sec)",
                        "offset_hour = tonumber(offset_hour)",
                        "offset_min = tonumber(offset_min)",
                        "local function is_leap_year(y)",
                        "  return (y % 4 == 0 and y % 100 ~= 0) or (y % 400 == 0)",
                        "end",
                        "local function days_since_epoch(y, m, d)",
                        "  local days = 0",
                        "  for current_year = 1970, y - 1 do",
                        "    days = days + (is_leap_year(current_year) and 366 or 365)",
                        "  end",
                        "  for current_month = 1, m - 1 do",
                        "    days = days + days_in_month[current_month]",
                        "    if current_month == 2 and is_leap_year(y) then",
                        "      days = days + 1",
                        "    end",
                        "  end",
                        "  return days + (d - 1)",
                        "end",
                        "local total_seconds = days_since_epoch(year, month, day) * 86400 + hour * 3600 + min * 60 + sec",
                        "local offset_seconds = offset_hour * 3600 + offset_min * 60",
                        "if offset_sign == '-' then",
                        "  offset_seconds = -offset_seconds",
                        "end",
                        "return total_seconds - offset_seconds",
                    ]
                ),
                SEMANTIC_PASS_RESPONSE,
            ]
        )
    )

    result = service.generate(
        task_text="Convert recallTime into unix format.",
        provided_context=json.dumps(
            {
                "wf": {
                    "initVariables": {
                        "recallTime": "2023-10-15T15:30:00+00:00",
                    }
                }
            }
        ),
        archetype="datetime_conversion",
        output_mode="raw_lua",
        input_roots=["wf.initVariables.recallTime"],
        risk_tags=["datetime_conversion", "init_variables", "timezone_offset"],
        debug=True,
    )

    first_iteration = result["validator_report"]["iterations"][0]

    assert result["validation_status"] == "passed"
    assert first_iteration["format_report"]["status"] == "pass"
    assert first_iteration["syntax_report"]["status"] == "pass"
    assert first_iteration["principle_report"]["status"] == "pass"
    assert first_iteration["semantic_report"]["status"] == "pass"
    debug = result["debug"]
    assert debug is not None
    assert [call["phase"] for call in debug["model_calls"]] == [
        "generation",
        "semantic_validation",
    ]
