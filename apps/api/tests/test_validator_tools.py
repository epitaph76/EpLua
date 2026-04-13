import sys
from pathlib import Path
from subprocess import CompletedProcess

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.append(str(REPO_ROOT))

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
