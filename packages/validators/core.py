from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

from packages.shared.quality import ValidationFinding, ValidatorReport

RAW_LUA = "raw_lua"
JSON_WRAPPER = "json_wrapper"
PATCH_MODE = "patch_mode"
CLARIFICATION = "clarification"

_WF_ROOT_PATTERN = re.compile(r"wf\.(?:vars|initVariables)\.[A-Za-z0-9_\.]+")
_JSONPATH_PATTERN = re.compile(r"[$@]\.[A-Za-z0-9_.*]+")
_BLOCK_START_PATTERN = re.compile(r"^\s*(?:local\s+function|function|if\b.*then$|for\b.*do$|while\b.*do$)")
_BLOCK_END_PATTERN = re.compile(r"^\s*end\s*$")
_DIRECT_FIELD_ASSIGNMENT_PATTERN = re.compile(
    r'(?:\[\s*"(?P<bracket>[A-Za-z_][A-Za-z0-9_]*)"\s*\]|\.(?P<dot>[A-Za-z_][A-Za-z0-9_]*))\s*='
)
_VALIDATOR_TOOL_TIMEOUT_SECONDS = 15
_REPO_ROOT = Path(__file__).resolve().parents[2]
_LOCAL_TOOL_PATHS = {
    "stylua": _REPO_ROOT / "tools" / "stylua" / "stylua.exe",
    "luacheck": _REPO_ROOT / "tools" / "lua_modules" / "bin" / "luacheck",
    "lua": _REPO_ROOT / "tools" / "lua" / "bin" / "lua.exe",
}
_LOCAL_LUACHECK_CONFIG = _REPO_ROOT / ".luacheckrc"
_LOCAL_STYLUA_CONFIG = _REPO_ROOT / ".stylua.toml"
_LOCAL_LUA_SHARE = _REPO_ROOT / "tools" / "lua_modules" / "share" / "lua" / "5.4"
_LOCAL_LUA_LIB = _REPO_ROOT / "tools" / "lua_modules" / "lib" / "lua" / "5.4"
_LOCAL_LUA_BIN_DIR = _REPO_ROOT / "tools" / "lua" / "bin"
_LOCAL_MINGW_BIN_DIR = _REPO_ROOT / "tools" / "mingw" / "mingw64" / "bin"


def run_validation_pipeline(
    candidate: str,
    *,
    output_mode: str,
    allowed_data_roots: tuple[str, ...],
    forbidden_patterns: tuple[str, ...],
    risk_tags: tuple[str, ...],
    archetype: str,
) -> tuple[str | None, ValidatorReport, ValidatorReport, ValidatorReport, ValidatorReport, ValidatorReport]:
    format_report = validate_format(candidate, output_mode)
    normalized_candidate = format_report.normalized_candidate

    if format_report.status != "pass":
        skipped_syntax = _skipped_report("syntax_validator", "format_validation_failed")
        skipped_static = _skipped_report("static_validator", "format_validation_failed")
        skipped_principle = _skipped_report("principle_validator", "format_validation_failed")
        return normalized_candidate, format_report, skipped_syntax, skipped_static, skipped_principle, ValidatorReport(
            validator="rule_validator",
            status="skipped",
            skipped_reason="format_validation_failed",
        )

    normalized = normalized_candidate or candidate.strip()
    syntax_report = validate_syntax(normalized, output_mode=output_mode)
    static_report = validate_static(
        normalized,
        output_mode=output_mode,
        allowed_data_roots=allowed_data_roots,
        forbidden_patterns=forbidden_patterns,
    )
    principle_report = validate_principles(
        normalized,
        output_mode=output_mode,
        risk_tags=risk_tags,
        archetype=archetype,
    )
    rule_report = _merge_reports("rule_validator", syntax_report, static_report, principle_report)
    return normalized_candidate, format_report, syntax_report, static_report, principle_report, rule_report


def validate_format(candidate: str, output_mode: str) -> ValidatorReport:
    stripped = candidate.strip()

    if output_mode == RAW_LUA:
        if "```" in stripped:
            return _fail_report(
                "format_validator",
                ValidationFinding(
                    validator="format_validator",
                    failure_class="markdown_fence",
                    message="raw_lua output must not include markdown fences.",
                    location="response",
                    repairable=True,
                    suggestion="Return only Lua code without markdown fences or surrounding prose.",
                ),
            )
        if stripped.startswith("{"):
            return _fail_report(
                "format_validator",
                ValidationFinding(
                    validator="format_validator",
                    failure_class="json_in_raw_lua",
                    message="raw_lua output must not be a JSON object.",
                    location="response",
                    repairable=True,
                    suggestion="Return only Lua code in raw_lua mode.",
                ),
            )

        non_empty_lines = [line.strip() for line in stripped.splitlines() if line.strip()]
        if not non_empty_lines:
            return _fail_report(
                "format_validator",
                ValidationFinding(
                    validator="format_validator",
                    failure_class="empty_output",
                    message="raw_lua output must contain Lua code.",
                    location="response",
                    repairable=True,
                    suggestion="Return a non-empty Lua snippet.",
                ),
            )
        if not _looks_like_lua(non_empty_lines[0]):
            return _fail_report(
                "format_validator",
                ValidationFinding(
                    validator="format_validator",
                    failure_class="leading_prose",
                    message="raw_lua output must not include explanatory prose before the code.",
                    location="response",
                    repairable=True,
                    suggestion="Return only Lua code with no leading explanation.",
                ),
            )
        return ValidatorReport(
            validator="format_validator",
            status="pass",
            normalized_candidate=stripped,
        )

    if output_mode in {JSON_WRAPPER, PATCH_MODE}:
        if "```" in stripped:
            return _fail_report(
                "format_validator",
                ValidationFinding(
                    validator="format_validator",
                    failure_class="markdown_fence",
                    message=f"{output_mode} output must not include markdown fences.",
                    location="response",
                    repairable=True,
                    suggestion=f"Return a plain JSON object for {output_mode} mode.",
                ),
            )
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            return _fail_report(
                "format_validator",
                ValidationFinding(
                    validator="format_validator",
                    failure_class="invalid_json",
                    message=f"{output_mode} output must be valid JSON.",
                    location="response",
                    repairable=True,
                    suggestion="Return a valid JSON object without extra prose.",
                ),
            )
        if not isinstance(payload, dict):
            return _fail_report(
                "format_validator",
                ValidationFinding(
                    validator="format_validator",
                    failure_class="json_object_required",
                    message=f"{output_mode} output must be a JSON object.",
                    location="response",
                    repairable=True,
                    suggestion="Return a JSON object instead of an array or scalar.",
                ),
            )
        invalid_wrappers = [
            location
            for location, value in _iter_string_leaves(payload)
            if not (value.startswith("lua{") and value.endswith("}lua"))
        ]
        if invalid_wrappers:
            return _fail_report(
                "format_validator",
                ValidationFinding(
                    validator="format_validator",
                    failure_class="invalid_wrapper",
                    message=f"{output_mode} string values must use lua{{...}}lua wrappers.",
                    location=invalid_wrappers[0],
                    repairable=True,
                    suggestion="Wrap every generated code string in lua{...}lua.",
                ),
            )
        normalized = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return ValidatorReport(
            validator="format_validator",
            status="pass",
            normalized_candidate=normalized,
        )

    if output_mode == CLARIFICATION:
        if stripped.startswith("{") or "```" in stripped:
            return _fail_report(
                "format_validator",
                ValidationFinding(
                    validator="format_validator",
                    failure_class="invalid_clarification_format",
                    message="clarification output must be plain text.",
                    location="response",
                    repairable=True,
                    suggestion="Return one plain clarification question without JSON or markdown.",
                ),
            )
        if "?" not in stripped:
            return _fail_report(
                "format_validator",
                ValidationFinding(
                    validator="format_validator",
                    failure_class="missing_question",
                    message="clarification output must contain a focused question.",
                    location="response",
                    repairable=True,
                    suggestion="Ask one short clarification question.",
                ),
            )
        return ValidatorReport(
            validator="format_validator",
            status="pass",
            normalized_candidate=stripped,
        )

    return _fail_report(
        "format_validator",
        ValidationFinding(
            validator="format_validator",
            failure_class="unknown_output_mode",
            message=f"Unsupported output mode: {output_mode}.",
            location="response",
            repairable=False,
            suggestion=None,
        ),
    )


def validate_rules(
    candidate: str,
    *,
    output_mode: str,
    allowed_data_roots: tuple[str, ...],
    forbidden_patterns: tuple[str, ...],
    risk_tags: tuple[str, ...],
    archetype: str,
) -> ValidatorReport:
    if output_mode == CLARIFICATION:
        return ValidatorReport(validator="rule_validator", status="pass")

    syntax_report = validate_syntax(candidate, output_mode=output_mode)
    static_report = validate_static(
        candidate,
        output_mode=output_mode,
        allowed_data_roots=allowed_data_roots,
        forbidden_patterns=forbidden_patterns,
    )
    principle_report = validate_principles(
        candidate,
        output_mode=output_mode,
        risk_tags=risk_tags,
        archetype=archetype,
    )
    return _merge_reports("rule_validator", syntax_report, static_report, principle_report)


def validate_syntax(candidate: str, *, output_mode: str) -> ValidatorReport:
    if output_mode == CLARIFICATION:
        return ValidatorReport(validator="syntax_validator", status="pass")

    lua_segments = _extract_lua_segments(candidate, output_mode)
    stylua_report = _run_stylua_check(lua_segments)
    if stylua_report is not None:
        return stylua_report

    findings = _validate_lua_syntax(lua_segments)
    if findings:
        return ValidatorReport(validator="syntax_validator", status="fail", findings=tuple(findings))
    return ValidatorReport(validator="syntax_validator", status="pass")


def validate_static(
    candidate: str,
    *,
    output_mode: str,
    allowed_data_roots: tuple[str, ...],
    forbidden_patterns: tuple[str, ...],
) -> ValidatorReport:
    if output_mode == CLARIFICATION:
        return ValidatorReport(validator="static_validator", status="pass")

    lua_segments = _extract_lua_segments(candidate, output_mode)
    findings: list[ValidationFinding] = []
    findings.extend(_validate_paths(lua_segments, allowed_data_roots))
    findings.extend(_validate_forbidden_patterns(candidate, lua_segments, forbidden_patterns))

    if not findings and output_mode == RAW_LUA:
        luacheck_report = _run_luacheck(lua_segments)
        if luacheck_report is not None:
            return luacheck_report

    if findings:
        return ValidatorReport(validator="static_validator", status="fail", findings=tuple(findings))
    return ValidatorReport(validator="static_validator", status="pass")


def validate_principles(
    candidate: str,
    *,
    output_mode: str,
    risk_tags: tuple[str, ...],
    archetype: str,
) -> ValidatorReport:
    if output_mode == CLARIFICATION:
        return ValidatorReport(validator="principle_validator", status="pass")

    lua_segments = _extract_lua_segments(candidate, output_mode)
    findings = _validate_archetype_specific(lua_segments, candidate, risk_tags, archetype, output_mode)
    if findings:
        return ValidatorReport(validator="principle_validator", status="fail", findings=tuple(findings))
    return ValidatorReport(validator="principle_validator", status="pass")


def _validate_paths(lua_segments: list[tuple[str, str]], allowed_data_roots: tuple[str, ...]) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    seen_roots: list[str] = []

    for location, segment in lua_segments:
        for root in _WF_ROOT_PATTERN.findall(segment):
            if root not in seen_roots:
                seen_roots.append(root)

        if not allowed_data_roots:
            continue

        for root in _WF_ROOT_PATTERN.findall(segment):
            if _path_is_allowed(root, allowed_data_roots):
                continue
            findings.append(
                ValidationFinding(
                    validator="path_validator",
                    failure_class="disallowed_data_root",
                    message=f"Candidate uses a data root outside the allowed scope: {root}.",
                    location=location,
                    repairable=True,
                    ambiguous=False,
                    suggestion=f"Use only the allowed data roots: {', '.join(allowed_data_roots)}.",
                )
            )
            return findings

    families = {_root_family(root) for root in seen_roots if _root_family(root)}
    allowed_families = {_root_family(root) for root in allowed_data_roots if _root_family(root)}
    if len(families) > 1 and len(allowed_families) <= 1:
        findings.append(
            ValidationFinding(
                validator="path_validator",
                failure_class="mixed_root_families",
                message="Candidate mixes wf.vars.* and wf.initVariables.* without a safe basis.",
                location="response",
                repairable=False,
                ambiguous=True,
                suggestion="Which data root should be used: wf.vars.* or wf.initVariables.*?",
            )
        )
    return findings


def _validate_forbidden_patterns(
    candidate: str,
    lua_segments: list[tuple[str, str]],
    forbidden_patterns: tuple[str, ...],
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []

    if _JSONPATH_PATTERN.search(candidate):
        findings.append(
            ValidationFinding(
                validator="forbidden_patterns_validator",
                failure_class="jsonpath_usage",
                message="JsonPath-like access is forbidden; use direct wf.* access instead.",
                location="response",
                repairable=True,
                suggestion="Replace JsonPath expressions with direct wf.* paths.",
            )
        )
        return findings

    forbidden_hits = {
        "JsonPath": "jsonpath_usage",
        "wf.data.": "invented_data_root",
        "```": "markdown_fence",
    }
    for token, failure_class in forbidden_hits.items():
        if token in candidate:
            findings.append(
                ValidationFinding(
                    validator="forbidden_patterns_validator",
                    failure_class=failure_class,
                    message=f"Forbidden pattern detected: {token}.",
                    location="response",
                    repairable=True,
                    suggestion="Remove the forbidden pattern and keep the requested output mode intact.",
                )
            )
            return findings

    for location, segment in lua_segments:
        for pattern in forbidden_patterns:
            if pattern in segment:
                findings.append(
                    ValidationFinding(
                        validator="forbidden_patterns_validator",
                        failure_class="forbidden_pattern",
                        message=f"Forbidden domain pattern detected: {pattern}.",
                        location=location,
                        repairable=True,
                        suggestion="Remove the forbidden pattern and keep direct LocalScript-compatible access.",
                    )
                )
                return findings
    return findings


def _validate_lua_syntax(lua_segments: list[tuple[str, str]]) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for location, segment in lua_segments:
        if segment.count("(") != segment.count(")"):
            findings.append(
                ValidationFinding(
                    validator="lua_syntax_validator",
                    failure_class="unbalanced_parentheses",
                    message="Lua candidate has unbalanced parentheses.",
                    location=location,
                    repairable=True,
                    suggestion="Balance the Lua expression parentheses.",
                )
            )
            return findings

        block_balance = 0
        for line in segment.splitlines():
            if _BLOCK_START_PATTERN.match(line):
                block_balance += 1
            if _BLOCK_END_PATTERN.match(line):
                block_balance -= 1
            if block_balance < 0:
                findings.append(
                    ValidationFinding(
                        validator="lua_syntax_validator",
                        failure_class="unexpected_end",
                        message="Lua candidate contains an unmatched end statement.",
                        location=location,
                        repairable=True,
                        suggestion="Remove the extra end statement or complete the corresponding block.",
                    )
                )
                return findings

        if block_balance != 0:
            findings.append(
                ValidationFinding(
                    validator="lua_syntax_validator",
                    failure_class="unbalanced_blocks",
                    message="Lua candidate has unbalanced block delimiters.",
                    location=location,
                    repairable=True,
                    suggestion="Close every function/if/for/while block with a matching end.",
                )
            )
            return findings
    return findings


def _run_stylua_check(lua_segments: list[tuple[str, str]]) -> ValidatorReport | None:
    stylua_binary = _resolve_tool_binary("STYLUA_BIN", "stylua")
    if stylua_binary is None:
        return None

    for index, (location, segment) in enumerate(lua_segments, start=1):
        with tempfile.TemporaryDirectory(prefix="luamts-validator-") as temp_dir:
            file_path = Path(temp_dir) / f"snippet_{index}.lua"
            file_path.write_text(_prepare_lua_segment_for_tool(segment), encoding="utf-8")
            command = _build_stylua_command(stylua_binary, file_path)
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=_VALIDATOR_TOOL_TIMEOUT_SECONDS,
                )
            except subprocess.TimeoutExpired:
                return ValidatorReport(
                    validator="syntax_validator",
                    status="fail",
                    findings=(
                        ValidationFinding(
                            validator="stylua_validator",
                            failure_class="stylua_check_failed_timeout",
                            message="Stylua check failed: tool timed out while checking the Lua snippet.",
                            location=location,
                            repairable=False,
                            suggestion="Fix the Lua syntax issue and retry the stylua check.",
                        ),
                    ),
                )
            except OSError as exc:
                return ValidatorReport(
                    validator="syntax_validator",
                    status="fail",
                    findings=(
                        ValidationFinding(
                            validator="stylua_validator",
                            failure_class="stylua_check_failed_execution_error",
                            message=f"Stylua check failed: unable to execute the tool ({exc}).",
                            location=location,
                            repairable=False,
                            suggestion="Make sure stylua is installed and reachable by the validator.",
                        ),
                    ),
                )

            if completed.returncode == 0:
                continue

            details = _compact_tool_output(completed.stderr or completed.stdout)
            if details.startswith("Diff in "):
                continue

            message = "Stylua check failed" if not details else f"Stylua check failed: {details}"
            return ValidatorReport(
                validator="syntax_validator",
                status="fail",
                findings=(
                    ValidationFinding(
                        validator="stylua_validator",
                        failure_class="stylua_check_failed",
                        message=message,
                        location=location,
                        repairable=True,
                        suggestion="Fix the Lua syntax or formatting issue reported by stylua.",
                    ),
                ),
            )

    return ValidatorReport(validator="syntax_validator", status="pass")


def _run_luacheck(lua_segments: list[tuple[str, str]]) -> ValidatorReport | None:
    luacheck_binary = _resolve_tool_binary("LUACHECK_BIN", "luacheck")
    if luacheck_binary is None:
        return None

    for index, (location, segment) in enumerate(lua_segments, start=1):
        with tempfile.TemporaryDirectory(prefix="luamts-validator-") as temp_dir:
            file_path = Path(temp_dir) / f"snippet_{index}.lua"
            file_path.write_text(_prepare_lua_segment_for_tool(segment), encoding="utf-8")
            command = _build_luacheck_command(luacheck_binary, file_path)
            environment = _build_luacheck_environment(luacheck_binary)

            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=_VALIDATOR_TOOL_TIMEOUT_SECONDS,
                    env=environment,
                )
            except subprocess.TimeoutExpired:
                return ValidatorReport(
                    validator="static_validator",
                    status="fail",
                    findings=(
                        ValidationFinding(
                            validator="luacheck_validator",
                            failure_class="luacheck_failed_timeout",
                            message="Luacheck reported a static analysis issue: tool timed out while checking the Lua snippet.",
                            location=location,
                            repairable=False,
                            suggestion="Fix the Lua issue and retry the luacheck pass.",
                        ),
                    ),
                )
            except OSError as exc:
                return ValidatorReport(
                    validator="static_validator",
                    status="fail",
                    findings=(
                        ValidationFinding(
                            validator="luacheck_validator",
                            failure_class="luacheck_failed_execution_error",
                            message=f"Luacheck reported a static analysis issue: unable to execute the tool ({exc}).",
                            location=location,
                            repairable=False,
                            suggestion="Make sure luacheck is installed and reachable by the validator.",
                        ),
                    ),
                )

            if completed.returncode == 0:
                continue

            details = _compact_tool_output(completed.stderr or completed.stdout)
            if not _luacheck_output_requires_failure(details):
                continue

            message = "Luacheck reported a static analysis issue" if not details else f"Luacheck reported a static analysis issue: {details}"
            return ValidatorReport(
                validator="static_validator",
                status="fail",
                findings=(
                    ValidationFinding(
                        validator="luacheck_validator",
                        failure_class="luacheck_failed",
                        message=message,
                        location=location,
                        repairable=True,
                        suggestion="Fix the Lua issue reported by luacheck while keeping the requested behavior.",
                    ),
                ),
            )

    return ValidatorReport(validator="static_validator", status="pass")


def _resolve_tool_binary(env_var: str, default: str) -> str | None:
    configured_path = os.environ.get(env_var)
    if configured_path:
        return configured_path
    local_path = _LOCAL_TOOL_PATHS.get(default)
    if local_path is not None and local_path.exists():
        return str(local_path)
    return shutil.which(default)


def _run_external_lua_tool(
    lua_segments: list[tuple[str, str]],
    *,
    tool_binary: str,
    command_builder: Callable[[str, Path], list[str]],
    env_builder: Callable[[str], dict[str, str] | None] | None,
    validator: str,
    failure_class: str,
    message_prefix: str,
    suggestion: str,
) -> list[ValidationFinding]:
    for index, (location, segment) in enumerate(lua_segments, start=1):
        with tempfile.TemporaryDirectory(prefix="luamts-validator-") as temp_dir:
            file_path = Path(temp_dir) / f"snippet_{index}.lua"
            file_path.write_text(_prepare_lua_segment_for_tool(segment), encoding="utf-8")
            command = command_builder(tool_binary, file_path)
            environment = env_builder(tool_binary) if env_builder is not None else None
            try:
                completed = subprocess.run(
                    command,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=_VALIDATOR_TOOL_TIMEOUT_SECONDS,
                    env=environment,
                )
            except subprocess.TimeoutExpired:
                return [
                    ValidationFinding(
                        validator=validator,
                        failure_class=f"{failure_class}_timeout",
                        message=f"{message_prefix}: tool timed out while checking the Lua snippet.",
                        location=location,
                        repairable=False,
                        suggestion=suggestion,
                    )
                ]
            except OSError as exc:
                return [
                    ValidationFinding(
                        validator=validator,
                        failure_class=f"{failure_class}_execution_error",
                        message=f"{message_prefix}: unable to execute the tool ({exc}).",
                        location=location,
                        repairable=False,
                        suggestion=suggestion,
                    )
                ]

            if completed.returncode == 0:
                continue

            details = _compact_tool_output(completed.stderr or completed.stdout)
            message = message_prefix if not details else f"{message_prefix}: {details}"
            return [
                ValidationFinding(
                    validator=validator,
                    failure_class=failure_class,
                    message=message,
                    location=location,
                    repairable=True,
                    suggestion=suggestion,
                )
            ]

    return []


def _build_stylua_command(tool_binary: str, file_path: Path) -> list[str]:
    command = [tool_binary, "--check"]
    if _LOCAL_STYLUA_CONFIG.exists():
        command.extend(["--config-path", str(_LOCAL_STYLUA_CONFIG)])
    command.append(str(file_path))
    return command


def _build_luacheck_command(tool_binary: str, file_path: Path) -> list[str]:
    if Path(tool_binary).is_file():
        lua_binary = _resolve_tool_binary("LUA_BIN", "lua")
        if lua_binary is None:
            return [tool_binary, "--codes", "--no-color", str(file_path)]

        command = [lua_binary, tool_binary, "--codes", "--no-color"]
        if _LOCAL_LUACHECK_CONFIG.exists():
            command.extend(["--config", str(_LOCAL_LUACHECK_CONFIG)])
        command.append(str(file_path))
        return command

    command = [tool_binary, "--codes", "--no-color"]
    if _LOCAL_LUACHECK_CONFIG.exists():
        command.extend(["--config", str(_LOCAL_LUACHECK_CONFIG)])
    else:
        command.extend(["--globals", "wf", "_utils"])
    command.append(str(file_path))
    return command


def _compact_tool_output(output: str) -> str:
    return " ".join(output.strip().split())


def _build_luacheck_environment(tool_binary: str) -> dict[str, str] | None:
    if not Path(tool_binary).is_file():
        return None

    environment = os.environ.copy()
    environment["LUA_PATH"] = (
        f"{_LOCAL_LUA_SHARE}\\?.lua;{_LOCAL_LUA_SHARE}\\?\\init.lua;"
        + environment.get("LUA_PATH", ";;")
    )
    environment["LUA_CPATH"] = f"{_LOCAL_LUA_LIB}\\?.dll;" + environment.get("LUA_CPATH", ";;")

    path_parts = [
        str(_LOCAL_MINGW_BIN_DIR),
        str(_LOCAL_LUA_BIN_DIR),
        environment.get("PATH", ""),
    ]
    environment["PATH"] = ";".join(part for part in path_parts if part)
    return environment


def _luacheck_output_requires_failure(output: str) -> bool:
    if not output:
        return True

    if re.search(r"\(E\d{3}\)", output):
        return True

    if "(W113)" in output:
        return True

    return not bool(re.search(r"\(W\d{3}\)", output))


def _prepare_lua_segment_for_tool(segment: str) -> str:
    normalized_segment = _localize_top_level_assignments_for_tool(segment)
    non_empty_lines = [line for line in segment.splitlines() if line.strip()]
    if not non_empty_lines:
        return normalized_segment

    first_line = non_empty_lines[0].strip()
    if _looks_like_lua(first_line):
        return normalized_segment

    return f"return {normalized_segment}"


def _localize_top_level_assignments_for_tool(segment: str) -> str:
    declared_names: set[str] = set()
    normalized_lines: list[str] = []

    for line in segment.splitlines():
        stripped = line.strip()
        if stripped.startswith("local "):
            declared_names.update(_extract_local_names(stripped))
            normalized_lines.append(line)
            continue

        function_match = re.match(r"^function\s+(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*\(", line)
        if function_match is not None:
            declared_names.add(function_match.group("name"))
            normalized_lines.append(f"local {line}")
            continue

        match = re.match(r"^(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if match is None:
            normalized_lines.append(line)
            continue

        variable_name = match.group("name")
        if variable_name in declared_names:
            normalized_lines.append(line)
            continue

        declared_names.add(variable_name)
        normalized_lines.append(f"local {line}")

    return "\n".join(normalized_lines)


def _extract_local_names(line: str) -> list[str]:
    left_side = line[6:]
    names_part = left_side.split("=", 1)[0]
    return [name.strip() for name in names_part.split(",") if name.strip()]


def _validate_archetype_specific(
    lua_segments: list[tuple[str, str]],
    candidate: str,
    risk_tags: tuple[str, ...],
    archetype: str,
    output_mode: str,
) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    combined_segments = "\n".join(segment for _, segment in lua_segments)

    if "array_allocation" in risk_tags and "_utils.array.new()" not in combined_segments:
        findings.append(
            ValidationFinding(
                validator="archetype_validator",
                failure_class="missing_array_allocator",
                message="This task needs _utils.array.new() for a rebuilt array result.",
                location="response",
                repairable=True,
                suggestion="Use _utils.array.new() when constructing a new array result.",
            )
        )
        return findings

    if "field_whitelist" in risk_tags:
        if not _matches_field_preservation_pattern(combined_segments):
            findings.append(
                ValidationFinding(
                    validator="principle_validator",
                    failure_class="missing_field_whitelist_pattern",
                    message="Field-preservation tasks must either iterate over keys with explicit key checks or update the named fields directly while preserving untouched data.",
                    location="response",
                    repairable=True,
                    suggestion="Either use key iteration with explicit key ~= checks, or update the named fields directly without dropping unrelated data.",
                )
            )
            return findings

    if "field_value_clearing" in risk_tags:
        if not _has_direct_named_field_updates(combined_segments):
            findings.append(
                ValidationFinding(
                    validator="principle_validator",
                    failure_class="missing_named_field_update_pattern",
                    message="Field-clearing tasks must update the named fields directly while preserving unrelated data.",
                    location="response",
                    repairable=True,
                    suggestion="Assign nil or the cleared value directly to the named fields instead of reshaping the full object.",
                )
            )
            return findings

    if (
        "timezone_offset" in risk_tags
        and "parse_iso8601_to_epoch" not in combined_segments
        and not all(token in combined_segments for token in ("offset_sign", "offset_hour", "offset_min"))
    ):
        findings.append(
            ValidationFinding(
                validator="archetype_validator",
                failure_class="missing_timezone_offset_handling",
                message="Timezone conversion tasks must account for offset_sign, offset_hour, and offset_min.",
                location="response",
                repairable=True,
                suggestion="Parse and apply the timezone offset fields before returning unix time.",
            )
        )
        return findings

    if output_mode == PATCH_MODE and candidate.strip() == "{}":
        findings.append(
            ValidationFinding(
                validator="archetype_validator",
                failure_class="empty_patch_payload",
                message="patch_mode output must contain an additive payload.",
                location="response",
                repairable=True,
                suggestion="Return only the fields that need to be added or changed.",
            )
        )
        return findings

    if output_mode == PATCH_MODE:
        try:
            payload = json.loads(candidate)
        except json.JSONDecodeError:
            payload = None

        if isinstance(payload, dict):
            invalid_keys = [key for key in payload if key.startswith("wf.") or "." in key]
            if invalid_keys:
                findings.append(
                    ValidationFinding(
                        validator="principle_validator",
                        failure_class="patch_path_keys",
                        message="patch_mode must return additive field names, not wf.* paths as JSON keys.",
                        location=f"response.{invalid_keys[0]}",
                        repairable=True,
                        suggestion="Return only the local payload fields that should be added or changed.",
                    )
                )
                return findings

    if output_mode == PATCH_MODE and '"wf"' in candidate:
        findings.append(
            ValidationFinding(
                validator="archetype_validator",
                failure_class="full_rewrite_patch_payload",
                message="patch_mode must not rewrite the whole payload.",
                location="response",
                repairable=True,
                suggestion="Return only additive or updated fields instead of the full payload.",
            )
        )
        return findings

    if archetype == "filtering" and "empty_value_filtering" in risk_tags:
        if "_utils.array.new()" not in combined_segments:
            findings.append(
                ValidationFinding(
                    validator="archetype_validator",
                    failure_class="missing_filter_result_container",
                    message="Filtering tasks should allocate a dedicated result array.",
                    location="response",
                    repairable=True,
                    suggestion="Create a new result array with _utils.array.new() before filtering.",
                )
            )
            return findings

    return findings


def _extract_lua_segments(candidate: str, output_mode: str) -> list[tuple[str, str]]:
    if output_mode == RAW_LUA:
        return [("response", candidate)]
    if output_mode == CLARIFICATION:
        return []

    payload = json.loads(candidate)
    segments: list[tuple[str, str]] = []
    for location, value in _iter_string_leaves(payload):
        segments.append((location, value[4:-4]))
    return segments


def _iter_string_leaves(node: Any, location: str = "$") -> list[tuple[str, str]]:
    if isinstance(node, dict):
        leaves: list[tuple[str, str]] = []
        for key, value in node.items():
            leaves.extend(_iter_string_leaves(value, f"{location}.{key}"))
        return leaves
    if isinstance(node, list):
        leaves: list[tuple[str, str]] = []
        for index, value in enumerate(node):
            leaves.extend(_iter_string_leaves(value, f"{location}[{index}]"))
        return leaves
    if isinstance(node, str):
        return [(location, node)]
    return []


def _matches_field_preservation_pattern(candidate: str) -> bool:
    return _has_whitelist_key_iteration(candidate) or _has_direct_named_field_updates(candidate)


def _has_whitelist_key_iteration(candidate: str) -> bool:
    return "for key" in candidate and "key ~=" in candidate


def _has_direct_named_field_updates(candidate: str) -> bool:
    return bool(_extract_named_field_updates(candidate))


def _extract_named_field_updates(candidate: str) -> set[str]:
    fields: set[str] = set()
    for match in _DIRECT_FIELD_ASSIGNMENT_PATTERN.finditer(candidate):
        literal = match.group("bracket") or match.group("dot")
        if literal:
            fields.add(literal)
    return fields


def _path_is_allowed(root: str, allowed_data_roots: tuple[str, ...]) -> bool:
    return any(
        root == allowed
        or root.startswith(f"{allowed}.")
        or allowed.startswith(f"{root}.")
        for allowed in allowed_data_roots
    )


def _root_family(root: str) -> str | None:
    if root.startswith("wf.vars."):
        return "wf.vars"
    if root.startswith("wf.initVariables."):
        return "wf.initVariables"
    return None


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
        or stripped.startswith("--")
        or "=" in stripped
    )


def _fail_report(validator: str, finding: ValidationFinding) -> ValidatorReport:
    return ValidatorReport(
        validator=validator,
        status="fail",
        findings=(finding,),
    )


def _skipped_report(validator: str, reason: str) -> ValidatorReport:
    return ValidatorReport(
        validator=validator,
        status="skipped",
        skipped_reason=reason,
    )


def _merge_reports(validator: str, *reports: ValidatorReport) -> ValidatorReport:
    findings: list[ValidationFinding] = []
    for report in reports:
        findings.extend(report.findings)

    if findings:
        return ValidatorReport(
            validator=validator,
            status="fail",
            findings=tuple(findings),
        )

    if any(report.status == "skipped" for report in reports):
        skipped_reason = next((report.skipped_reason for report in reports if report.skipped_reason), None)
        return ValidatorReport(
            validator=validator,
            status="skipped",
            skipped_reason=skipped_reason,
        )

    return ValidatorReport(validator=validator, status="pass")
