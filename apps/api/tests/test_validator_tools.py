import sys
from pathlib import Path
from subprocess import CompletedProcess

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

from packages.orchestrator.task_spec import TaskSpec  # noqa: E402
from packages.validators import core  # noqa: E402


def test_validate_syntax_uses_stylua_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    monkeypatch.setattr(core, "_resolve_tool_binary", lambda env_var, default: "stylua")

    def fake_run(command: list[str], **_: object) -> CompletedProcess[str]:
        commands.append(command)
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(core.subprocess, "run", fake_run)

    report = core.validate_syntax("return wf.vars.value", output_mode=core.RAW_LUA)

    assert report.status == "pass"
    assert commands
    assert commands[0][0] == "stylua"
    assert "--check" in commands[0]


def test_lowcode_json_format_requires_lua_wrapped_string_leaves() -> None:
    report = core.validate_format('{"result":"lua{return wf.vars.emails[#wf.vars.emails]}lua"}', core.LOWCODE_JSON)

    assert report.status == "pass"
    assert report.normalized_candidate == '{"result":"lua{return wf.vars.emails[#wf.vars.emails]}lua"}'

    non_string_report = core.validate_format('{"result":123}', core.LOWCODE_JSON)
    assert non_string_report.status == "fail"
    assert non_string_report.findings[0].failure_class == "non_string_lua_value"

    unwrapped_report = core.validate_format('{"raw_lua":{"value":"return wf.vars.emails[#wf.vars.emails]"}}', core.LOWCODE_JSON)
    assert unwrapped_report.status == "fail"
    assert unwrapped_report.findings[0].failure_class == "invalid_wrapper"


def test_lowcode_json_rejects_lua_error_calls() -> None:
    report = core.validate_static(
        '{"result":"lua{if not wf.vars.value then error(\\"missing\\") end\\nreturn wf.vars.value}lua"}',
        output_mode=core.LOWCODE_JSON,
        allowed_data_roots=("wf.vars.value",),
        forbidden_patterns=(),
    )

    assert report.status == "fail"
    assert report.findings[0].failure_class == "runtime_error_call"
    assert report.findings[0].suggestion == "Return nil, false, or an empty string instead of throwing an error."


def test_validate_syntax_surfaces_stylua_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core, "_resolve_tool_binary", lambda env_var, default: "stylua")

    def fake_run(command: list[str], **_: object) -> CompletedProcess[str]:
        return CompletedProcess(command, 1, "", "error: expected 'end' near <eof>")

    monkeypatch.setattr(core.subprocess, "run", fake_run)

    report = core.validate_syntax("if wf.vars.value then\n  return wf.vars.value", output_mode=core.RAW_LUA)

    assert report.status == "fail"
    assert report.findings[0].failure_class == "stylua_check_failed"
    assert "expected 'end' near <eof>" in report.findings[0].message


def test_validate_syntax_falls_back_when_stylua_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(core, "_resolve_tool_binary", lambda env_var, default: None)

    report = core.validate_syntax("if wf.vars.value then\n  return wf.vars.value", output_mode=core.RAW_LUA)

    assert report.status == "fail"
    assert report.findings[0].failure_class == "unbalanced_blocks"


def test_validate_static_uses_luacheck_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    def fake_resolve_tool(env_var: str, default: str) -> str | None:
        if env_var == "LUACHECK_BIN":
            return "luacheck"
        return None

    monkeypatch.setattr(core, "_resolve_tool_binary", fake_resolve_tool)

    def fake_run(command: list[str], **_: object) -> CompletedProcess[str]:
        commands.append(command)
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(core.subprocess, "run", fake_run)

    report = core.validate_static(
        "return wf.vars.value",
        output_mode=core.RAW_LUA,
        allowed_data_roots=("wf.vars.value",),
        forbidden_patterns=(),
    )

    assert report.status == "pass"
    assert commands
    assert commands[0][0] == "luacheck"
    assert "--config" in commands[0]
    assert commands[0][commands[0].index("--config") + 1].endswith(".luacheckrc")


def test_luacheck_file_binary_runs_directly_instead_of_through_lua(tmp_path: Path) -> None:
    luacheck_binary = tmp_path / "luacheck"
    luacheck_binary.write_text("#!/bin/sh\n", encoding="utf-8")

    command = core._build_luacheck_command(str(luacheck_binary), tmp_path / "candidate.lua")

    assert command[0] == str(luacheck_binary)
    assert "lua5.4" not in command[0]
    assert str(luacheck_binary) in command


def test_validate_static_surfaces_luacheck_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_resolve_tool(env_var: str, default: str) -> str | None:
        if env_var == "LUACHECK_BIN":
            return "luacheck"
        return None

    monkeypatch.setattr(core, "_resolve_tool_binary", fake_resolve_tool)

    def fake_run(command: list[str], **_: object) -> CompletedProcess[str]:
        return CompletedProcess(command, 1, "tmp.lua:1:11: accessing undefined variable missing_value", "")

    monkeypatch.setattr(core.subprocess, "run", fake_run)

    report = core.validate_static(
        "return missing_value",
        output_mode=core.RAW_LUA,
        allowed_data_roots=(),
        forbidden_patterns=(),
    )

    assert report.status == "fail"
    assert report.findings[0].failure_class == "luacheck_failed"
    assert "undefined variable missing_value" in report.findings[0].message


def test_validate_static_marks_broken_luacheck_launcher_as_infrastructure_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_resolve_tool(env_var: str, default: str) -> str | None:
        if env_var == "LUACHECK_BIN":
            return "luacheck"
        return None

    monkeypatch.setattr(core, "_resolve_tool_binary", fake_resolve_tool)

    def fake_run(command: list[str], **_: object) -> CompletedProcess[str]:
        return CompletedProcess(
            command,
            1,
            "",
            "/usr/bin/lua5.4: /usr/local/bin/luacheck:3: unexpected symbol near '-'",
        )

    monkeypatch.setattr(core.subprocess, "run", fake_run)

    report = core.validate_static(
        "return wf.vars.value",
        output_mode=core.RAW_LUA,
        allowed_data_roots=("wf.vars.value",),
        forbidden_patterns=(),
    )

    assert report.status == "skipped"
    assert report.skipped_reason == "validator_execution_failed"
    assert report.metadata is not None
    assert report.metadata["tool"] == "luacheck"


def test_validate_runtime_behavior_catches_wrong_last_array_item_candidate() -> None:
    report = core.validate_runtime_behavior(
        "return wf.vars.emails",
        output_mode=core.RAW_LUA,
        execution_context={
            "wf": {
                "vars": {
                    "emails": [
                        "user1@example.com",
                        "user2@example.com",
                        "user3@example.com",
                    ]
                }
            }
        },
        task_spec=TaskSpec(
            task_text="Get the last email from the list.",
            language="en",
            archetype="simple_extraction",
            operation="last_array_item",
            output_mode=core.RAW_LUA,
            input_roots=("wf.vars.emails",),
            expected_shape="scalar_or_nil",
            risk_tags=("array_indexing", "empty_array"),
            edge_cases=("single_item", "empty_array"),
            clarification_required=False,
            clarification_question=None,
        ),
    )

    assert report.status == "fail"
    assert report.findings[0].failure_class == "runtime_behavior_mismatch"
    assert "expected 'user3@example.com'" in report.findings[0].message


def test_validate_principles_rejects_last_array_item_returning_array_alias() -> None:
    task_spec = TaskSpec(
        task_text="Get the last email from the list.",
        language="en",
        archetype="simple_extraction",
        operation="last_array_item",
        output_mode=core.RAW_LUA,
        input_roots=("wf.vars.emails",),
        expected_shape="scalar_or_nil",
        risk_tags=("array_indexing", "empty_array"),
        edge_cases=("single_item", "empty_array"),
        clarification_required=False,
        clarification_question=None,
    )

    report = core.validate_principles(
        "local emails = wf.vars.emails\nif emails and #emails > 0 then\n\treturn emails\nend\nreturn nil",
        output_mode=core.RAW_LUA,
        risk_tags=("array_indexing", "empty_array"),
        archetype="simple_extraction",
        task_spec=task_spec,
    )

    assert report.status == "fail"
    assert report.findings[0].failure_class == "array_item_returns_whole_array"

    correct_report = core.validate_principles(
        "local emails = wf.vars.emails\nif emails and #emails > 0 then\n\treturn emails[#emails]\nend\nreturn nil",
        output_mode=core.RAW_LUA,
        risk_tags=("array_indexing", "empty_array"),
        archetype="simple_extraction",
        task_spec=task_spec,
    )

    assert correct_report.status == "pass"


def test_array_item_validators_follow_operation_when_planner_keeps_transformation_archetype() -> None:
    task_spec = TaskSpec(
        task_text="Из полученного списка email получи последний.",
        language="ru",
        archetype="transformation",
        operation="last_array_item",
        output_mode=core.RAW_LUA,
        input_roots=("wf.vars.emails",),
        expected_shape="scalar_or_nil",
        risk_tags=("array_indexing",),
        edge_cases=("single_item", "empty_array"),
        clarification_required=False,
        clarification_question=None,
    )
    candidate = 'local emails = wf.vars.emails\nif emails and #emails > 0 then\n    return emails\nend\nreturn nil'
    execution_context = {
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

    principle_report = core.validate_principles(
        candidate,
        output_mode=core.RAW_LUA,
        risk_tags=("array_indexing",),
        archetype="transformation",
        task_spec=task_spec,
    )
    runtime_report = core.validate_runtime_behavior(
        candidate,
        output_mode=core.RAW_LUA,
        execution_context=execution_context,
        task_spec=task_spec,
    )

    assert principle_report.status == "fail"
    assert principle_report.findings[0].failure_class == "array_item_returns_whole_array"
    assert runtime_report.status == "fail"
    assert runtime_report.findings[0].failure_class == "runtime_behavior_mismatch"
