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


SEMANTIC_PASS_RESPONSE = '{"status":"pass","message":"Semantic validation passed."}'


def test_generation_service_auto_normalizes_fenced_raw_lua_before_repair_budget() -> None:
    service = GenerationService(
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
    assert result["repair_count"] == 0
    assert result["trace"] == [
        "request_received",
        "generation",
        "format_validation",
        "rule_validation",
        "semantic_validation",
        "finalize",
    ]
    debug = result["debug"]
    assert debug is not None
    assert debug["validation_passes"][0]["candidate"] == "return wf.vars.emails[#wf.vars.emails]"
    assert debug["validation_passes"][0]["format_report"]["status"] == "pass"


def test_generation_service_repairs_markdown_fenced_raw_lua_candidate() -> None:
    service = GenerationService(
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
    service = GenerationService(
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
        "semantic_validation",
        "finalize",
    ]
    assert result["critic_report"] is None
    debug = result["debug"]
    assert debug is not None
    assert debug["model_calls"][1]["phase"] == "semantic_validation"
    assert debug["validation_passes"][0]["normalized_candidate"] == result["code"]


def test_generation_service_normalizes_fenced_patch_mode_on_repair_iteration() -> None:
    service = GenerationService(
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
    service = GenerationService(
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
        "semantic_validation",
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
    service = GenerationService(
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
    service = GenerationService(
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
    service = GenerationService(
        model_adapter=ScriptedModelAdapter(
            [
                "return wf.vars.emails[1]",
                (
                    '{"status":"fail","failure_class":"semantic_mismatch","message":"The task asks for the last email, '
                    'but the candidate returns the first item.","repairable":true,"ambiguous":false,'
                    '"suggestion":"Return the last element of wf.vars.emails instead of the first one."}'
                ),
                "return wf.vars.emails[#wf.vars.emails]",
                '{"status":"pass","message":"The candidate now returns the last email."}',
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
        "message": "Repair the candidate to match the task semantics without changing the user goal.",
        "repair_prompt": "Return the last element of wf.vars.emails instead of the first one.",
    }
    first_iteration = result["validator_report"]["iterations"][0]
    second_iteration = result["validator_report"]["iterations"][1]
    assert first_iteration["rule_report"]["status"] == "pass"
    assert first_iteration["semantic_report"]["status"] == "fail"
    assert first_iteration["semantic_report"]["findings"][0]["failure_class"] == "semantic_mismatch"
    assert second_iteration["semantic_report"]["status"] == "pass"
    debug = result["debug"]
    assert debug is not None
    assert [call["phase"] for call in debug["model_calls"]] == [
        "generation",
        "semantic_validation",
        "repair_generation",
        "semantic_validation",
    ]


def test_generation_service_preserves_think_block_and_validates_visible_raw_lua_response() -> None:
    service = GenerationService(
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
    service = GenerationService(
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
    service = GenerationService(
        model_adapter=ScriptedModelAdapter(
            [
                "return wf.data.emails[#wf.data.emails]",
                SEMANTIC_PASS_RESPONSE,
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
        "semantic_validation",
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
        "failure_class": "invented_data_root",
        "message": "Repair the candidate for failure class invented_data_root without changing the user goal.",
        "repair_prompt": "Repair the current candidate using the validator finding. Keep the same output mode, preserve the user goal, and return only the repaired result.",
    }
    debug = result["debug"]
    assert debug is not None
    assert debug["model_calls"][1]["phase"] == "semantic_validation"
    assert debug["model_calls"][2]["phase"] == "repair_generation"
    assert debug["model_calls"][2]["raw_response"].startswith("```lua")
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
        "semantic_validation",
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
        "message": "Add the missing domain-specific element without changing the requested result shape.",
        "repair_prompt": "Repair the candidate by adding the missing domain-specific logic while preserving the requested output mode and user goal.",
    }
    debug = result["debug"]
    assert debug is not None
    assert debug["validation_passes"][0]["rule_report"]["findings"][0]["failure_class"] == "missing_array_allocator"
    assert debug["validation_passes"][1]["normalized_candidate"] == result["code"]
    assert debug["model_calls"][1]["phase"] == "semantic_validation"
    assert debug["model_calls"][2]["phase"] == "repair_generation"
    assert debug["model_calls"][2]["raw_response"] == result["code"]


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

    assert result["validation_status"] == "bounded_failure"
    assert result["repair_count"] == 1
    assert result["clarification_count"] == 0
    assert result["critic_report"] == {
        "action": "finalize",
        "failure_class": "repair_oscillation",
        "message": "Repair loop started oscillating between previously seen candidates or failure patterns.",
    }
    assert result["validator_report"]["status"] == "fail"


def test_generation_service_prefers_semantic_intent_for_clear_field_conflict() -> None:
    service = GenerationService(
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
        "message": "Prefer semantic intent over the pattern-based rule for explicit field-operation tasks.",
        "repair_prompt": "Set ID, ENTITY_ID, and CALL to nil directly and keep all other fields untouched.",
    }
    first_iteration = result["validator_report"]["iterations"][0]
    assert first_iteration["principle_report"]["status"] == "fail"
    assert first_iteration["principle_report"]["findings"][0]["failure_class"] == "missing_field_whitelist_pattern"
    assert first_iteration["semantic_report"]["status"] == "fail"
    assert first_iteration["semantic_report"]["findings"][0]["failure_class"] == "semantic_mismatch"


def test_generation_service_returns_validator_conflict_for_unresolved_pattern_vs_semantic_disagreement() -> None:
    service = GenerationService(
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
        "message": "Semantic and pattern-based validators disagree on the repair direction.",
    }


def test_generation_service_accepts_direct_named_field_clearing_when_semantics_pass() -> None:
    service = GenerationService(
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


def test_generation_service_marks_repair_oscillation_when_candidate_returns_to_previous_shape() -> None:
    service = GenerationService(
        model_adapter=ScriptedModelAdapter(
            [
                "return wf.data.emails[1]",
                SEMANTIC_PASS_RESPONSE,
                "return wf.vars.emails[1]",
                (
                    '{"status":"fail","failure_class":"semantic_mismatch","message":"The task asks for the last email, '
                    'but the candidate returns the first item.","repairable":true,"ambiguous":false,'
                    '"suggestion":"Return the last element of wf.vars.emails instead of the first one."}'
                ),
                "return wf.data.emails[1]",
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

    assert result["validation_status"] == "bounded_failure"
    assert result["repair_count"] == 2
    assert result["critic_report"] == {
        "action": "finalize",
        "failure_class": "repair_oscillation",
        "message": "Repair loop started oscillating between previously seen candidates or failure patterns.",
    }


def test_generation_service_keeps_best_candidate_after_three_repairs() -> None:
    service = GenerationService(
        model_adapter=ScriptedModelAdapter(
            [
                "return wf.vars.emails[1]",
                (
                    '{"status":"fail","failure_class":"semantic_mismatch","message":"The task asks for the last email, '
                    'but the candidate returns the first item.","repairable":true,"ambiguous":false,'
                    '"suggestion":"Return the last element of wf.vars.emails instead of the first one."}'
                ),
                "Here is the repaired code:\nreturn wf.vars.emails[#wf.vars.emails]",
                "{}",
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

    assert result["validation_status"] == "bounded_failure"
    assert result["repair_count"] == 3
    assert result["code"] == "return wf.vars.emails[1]"
    assert result["final_candidate_source"] == "best_candidate"
    assert result["final_candidate_iteration_index"] == 0
    assert result["critic_report_iteration_index"] == 3
    assert result["critic_report"] == {
        "action": "finalize",
        "failure_class": "empty_output",
        "message": "Repair budget exhausted or the same failure repeated after the latest repair.",
    }


def test_generation_service_accepts_domain_datetime_helper_for_unix_conversion() -> None:
    service = GenerationService(
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
    service = GenerationService(
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
    service = GenerationService(
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
    service = GenerationService(
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
    service = GenerationService(
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
    service = GenerationService(
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
    service = GenerationService(
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
    service = GenerationService(
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
    service = GenerationService(
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


def test_generation_service_repairs_patch_mode_path_keys_with_tool() -> None:
    service = GenerationService(
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
    assert result["debug"]["model_calls"][2]["repair_source"] == "deterministic_tool"


def test_generation_service_repairs_nested_full_rewrite_patch_payload_with_tool() -> None:
    service = GenerationService(
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
    assert result["debug"]["model_calls"][2]["repair_source"] == "deterministic_tool"


def test_generation_service_exposes_layered_reports_before_critic() -> None:
    service = GenerationService(
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
