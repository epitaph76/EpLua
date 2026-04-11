import json

from services.generation import GenerationService


class ScriptedModelAdapter:
    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self.prompts: list[str] = []

    def generate_from_prompt(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if not self._responses:
            raise AssertionError("No scripted responses left for the model adapter.")
        return self._responses.pop(0)


def test_generation_service_repairs_markdown_fenced_raw_lua_candidate() -> None:
    service = GenerationService(
        model_adapter=ScriptedModelAdapter(
            [
                "```lua\nreturn wf.vars.emails[#wf.vars.emails]\n```",
                "return wf.vars.emails[#wf.vars.emails]",
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
    assert result["validation_status"] == "repaired"
    assert result["repair_count"] == 1
    assert result["clarification_count"] == 0
    assert result["output_mode"] == "raw_lua"
    assert result["archetype"] == "simple_extraction"
    assert result["trace"] == [
        "request_received",
        "generation",
        "format_validation",
        "critic_step",
        "repair_generation",
        "format_validation",
        "rule_validation",
        "finalize",
    ]
    assert result["validator_report"]["status"] == "pass"
    assert result["validator_report"]["iterations"][0]["format_report"]["status"] == "fail"
    assert result["validator_report"]["iterations"][0]["format_report"]["findings"] == [
        {
            "validator": "format_validator",
            "failure_class": "markdown_fence",
            "message": "raw_lua output must not include markdown fences.",
            "location": "response",
            "repairable": True,
            "ambiguous": False,
            "suggestion": "Return only Lua code without markdown fences or surrounding prose.",
        }
    ]
    assert result["validator_report"]["iterations"][1]["rule_report"]["status"] == "pass"
    assert result["critic_report"] == {
        "action": "repair",
        "failure_class": "markdown_fence",
        "message": "Remove markdown fences and keep the output in raw_lua mode.",
        "repair_prompt": (
            "Return only raw Lua code. Remove markdown fences and any surrounding explanation "
            "without changing the user goal."
        ),
    }


def test_generation_service_normalizes_fenced_json_wrapper_on_repair_iteration() -> None:
    service = GenerationService(
        model_adapter=ScriptedModelAdapter(
            [
                '```json\n{"lastEmail":"lua{return wf.vars.emails[#wf.vars.emails]}lua"}\n```',
                '```json\n{"lastEmail":"lua{return wf.vars.emails[#wf.vars.emails]}lua"}\n```',
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
    assert result["validation_status"] == "repaired"
    assert result["trace"] == [
        "request_received",
        "generation",
        "format_validation",
        "critic_step",
        "repair_generation",
        "format_validation",
        "rule_validation",
        "finalize",
    ]
    assert result["critic_report"] == {
        "action": "repair",
        "failure_class": "markdown_fence",
        "message": "Repair the candidate for failure class markdown_fence without changing the user goal.",
        "repair_prompt": "Repair the current candidate using the validator finding. Keep the same output mode, preserve the user goal, and return only the repaired result.",
    }
    debug = result["debug"]
    assert debug is not None
    assert debug["model_calls"][1]["raw_response"].startswith("```json")
    assert debug["validation_passes"][1]["normalized_candidate"] == result["code"]


def test_generation_service_normalizes_fenced_patch_mode_on_repair_iteration() -> None:
    service = GenerationService(
        model_adapter=ScriptedModelAdapter(
            [
                '```json\n{"num":"lua{return tonumber(\'5\')}lua","squared":"lua{local n = tonumber(\'5\')\\nreturn n * n}lua"}\n```',
                '```json\n{"num":"lua{return tonumber(\'5\')}lua","squared":"lua{local n = tonumber(\'5\')\\nreturn n * n}lua"}\n```',
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
    assert result["validation_status"] == "repaired"
    assert result["trace"] == [
        "request_received",
        "generation",
        "format_validation",
        "critic_step",
        "repair_generation",
        "format_validation",
        "rule_validation",
        "finalize",
    ]
    assert result["critic_report"] == {
        "action": "repair",
        "failure_class": "markdown_fence",
        "message": "Repair the candidate for failure class markdown_fence without changing the user goal.",
        "repair_prompt": "Repair the current candidate using the validator finding. Keep the same output mode, preserve the user goal, and return only the repaired result.",
    }
    debug = result["debug"]
    assert debug is not None
    assert debug["model_calls"][1]["raw_response"].startswith("```json")
    assert debug["validation_passes"][1]["normalized_candidate"] == result["code"]


def test_generation_service_repairs_invalid_json_json_wrapper_with_tool() -> None:
    service = GenerationService(
        model_adapter=ScriptedModelAdapter(
            [
                '{\n  "result": {\n    "last_email": lua{wf.vars.emails[#wf.vars.emails] or ""}lua\n  }\n}',
                '{\n  "result": {\n    "last_email": lua{wf.vars.emails[#wf.vars.emails] or ""}lua\n  }\n}',
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
        "finalize",
    ]
    assert result["repair_count"] == 1
    assert result["critic_report"] == {
        "action": "repair",
        "failure_class": "invalid_json",
        "message": "Repair the candidate for failure class invalid_json without changing the user goal.",
        "repair_prompt": "Repair the current candidate using the validator finding. Keep the same output mode, preserve the user goal, and return only the repaired result.",
    }
    debug = result["debug"]
    assert debug is not None
    assert debug["validation_passes"][0]["format_report"]["findings"][0]["failure_class"] == "invalid_json"
    assert debug["validation_passes"][1]["normalized_candidate"] == result["code"]
    assert debug["model_calls"][1]["repair_source"] == "deterministic_tool"


def test_generation_service_repairs_invalid_json_patch_mode_with_tool() -> None:
    service = GenerationService(
        model_adapter=ScriptedModelAdapter(
            [
                "{\n  lua = {\n    value = (wf.vars.number or 0) * (wf.vars.number or 0)\n  }\n}",
                "{\n  lua = {\n    value = (wf.vars.number or 0) * (wf.vars.number or 0)\n  }\n}",
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
        "finalize",
    ]
    assert result["repair_count"] == 1
    assert result["critic_report"] == {
        "action": "repair",
        "failure_class": "invalid_json",
        "message": "Repair the candidate for failure class invalid_json without changing the user goal.",
        "repair_prompt": "Repair the current candidate using the validator finding. Keep the same output mode, preserve the user goal, and return only the repaired result.",
    }
    debug = result["debug"]
    assert debug is not None
    assert debug["validation_passes"][0]["format_report"]["findings"][0]["failure_class"] == "invalid_json"
    assert debug["validation_passes"][1]["normalized_candidate"] == result["code"]
    assert debug["model_calls"][1]["repair_source"] == "deterministic_tool"


def test_generation_service_repairs_string_only_patch_object_with_tool() -> None:
    service = GenerationService(
        model_adapter=ScriptedModelAdapter(
            [
                '{\n  "lua": {\n    "my_square_variable = tonumber(wf.vars.number) ^ 2;"\n  }\n}',
                '{\n  "lua": {\n    "my_square_variable = tonumber(wf.vars.number) ^ 2;"\n  }\n}',
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
        "finalize",
    ]
    assert result["repair_count"] == 1
    debug = result["debug"]
    assert debug is not None
    assert debug["validation_passes"][0]["format_report"]["findings"][0]["failure_class"] == "invalid_json"
    assert debug["validation_passes"][1]["normalized_candidate"] == result["code"]
    assert debug["model_calls"][1]["repair_source"] == "deterministic_tool"


def test_generation_service_repairs_fragment_only_patch_object_with_tool() -> None:
    service = GenerationService(
        model_adapter=ScriptedModelAdapter(
            [
                '{\n  "wf.vars.squareOfNumber": {\n    "lua{"\n    "..=local num = wf.vars.number\\n"\n    "..=local squared = num * num\\n"\n    "..=return {squareOfNumber = squared}\\n"\n    "}"\n  }\n}',
                '{\n  "wf.vars.squareOfNumber": {\n    "lua{"\n    "..=local num = wf.vars.number\\n"\n    "..=local squared = num * num\\n"\n    "..=return {squareOfNumber = squared}\\n"\n    "}"\n  }\n}',
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
        == '{"wf.vars.squareOfNumber":{"value":"lua{local num = wf.vars.number\\\\nlocal squared = num * num\\\\nreturn {squareOfNumber = squared}\\\\n}lua"}}'
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
        "finalize",
    ]
    assert result["repair_count"] == 1
    debug = result["debug"]
    assert debug is not None
    assert debug["validation_passes"][0]["format_report"]["findings"][0]["failure_class"] == "invalid_json"
    assert debug["validation_passes"][1]["normalized_candidate"] == result["code"]
    assert debug["model_calls"][1]["repair_source"] == "deterministic_tool"


def test_generation_service_exposes_debug_audit_trail_for_repair_flow() -> None:
    service = GenerationService(
        model_adapter=ScriptedModelAdapter(
            [
                "```lua\nreturn wf.vars.emails[#wf.vars.emails]\n```",
                "return wf.vars.emails[#wf.vars.emails]",
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
    assert debug["model_calls"][1]["phase"] == "repair_generation"
    assert "Repair task:" in debug["model_calls"][1]["prompt"]
    assert debug["validation_passes"][0]["critic_report"]["action"] == "repair"
    assert debug["validation_passes"][1]["rule_report"]["status"] == "pass"


def test_generation_service_normalizes_fenced_raw_lua_on_repair_iteration() -> None:
    service = GenerationService(
        model_adapter=ScriptedModelAdapter(
            [
                "return wf.data.emails[#wf.data.emails]",
                "```lua\nreturn wf.vars.emails[#wf.vars.emails]\n```",
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
        "finalize",
    ]
    assert result["repair_count"] == 1
    assert result["critic_report"] == {
        "action": "repair",
        "failure_class": "invented_data_root",
        "message": "Repair the candidate for failure class invented_data_root without changing the user goal.",
        "repair_prompt": "Repair the current candidate using the validator finding. Keep the same output mode, preserve the user goal, and return only the repaired result.",
    }
    debug = result["debug"]
    assert debug is not None
    assert debug["model_calls"][1]["phase"] == "repair_generation"
    assert debug["model_calls"][1]["raw_response"].startswith("```lua")
    assert debug["validation_passes"][0]["rule_report"]["findings"][0]["failure_class"] == "invented_data_root"
    assert debug["validation_passes"][1]["format_report"]["status"] == "pass"
    assert debug["validation_passes"][1]["normalized_candidate"] == result["code"]


def test_generation_service_repairs_missing_array_allocator_with_tool() -> None:
    service = GenerationService(
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
                )
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
        "finalize",
    ]
    assert result["repair_count"] == 1
    assert result["critic_report"] == {
        "action": "repair",
        "failure_class": "missing_array_allocator",
        "message": "Add the missing domain-specific element without changing the requested result shape.",
        "repair_prompt": "Repair the candidate by adding the missing domain-specific logic while preserving the requested output mode and user goal.",
    }
    debug = result["debug"]
    assert debug is not None
    assert debug["validation_passes"][0]["rule_report"]["findings"][0]["failure_class"] == "missing_array_allocator"
    assert debug["validation_passes"][1]["normalized_candidate"] == result["code"]
    assert debug["model_calls"][1]["phase"] == "repair_generation"
    assert debug["model_calls"][1]["raw_response"] == result["code"]


def test_generation_service_returns_clarification_for_ambiguous_roots() -> None:
    service = GenerationService(
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


def test_generation_service_stops_after_repeated_failure_class_after_repair() -> None:
    service = GenerationService(
        model_adapter=ScriptedModelAdapter(
            [
                "```lua\nreturn wf.vars.emails[#wf.vars.emails]\n```",
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

    assert result["validation_status"] == "bounded_failure"
    assert result["repair_count"] == 1
    assert result["clarification_count"] == 0
    assert result["critic_report"] == {
        "action": "finalize",
        "failure_class": "markdown_fence",
        "message": "Repair budget exhausted or the same failure repeated after the latest repair.",
    }
    assert result["validator_report"]["status"] == "fail"
