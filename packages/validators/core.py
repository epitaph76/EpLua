from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable

from packages.orchestrator.task_spec import TaskSpec
from packages.shared.quality import ValidationFinding, ValidatorReport

RAW_LUA = "raw_lua"
JSON_WRAPPER = "json_wrapper"
PATCH_MODE = "patch_mode"
LOWCODE_JSON = "lowcode_json"
CLARIFICATION = "clarification"
_JSON_LUA_OUTPUT_MODES = {JSON_WRAPPER, PATCH_MODE, LOWCODE_JSON}
_ARRAY_ITEM_OPERATIONS = {"last_array_item", "first_array_item"}

_WF_ROOT_PATTERN = re.compile(r"wf\.(?:vars|initVariables)\.[A-Za-z0-9_\.]+")
_JSONPATH_PATTERN = re.compile(r"[$@]\.[A-Za-z0-9_.*]+")
_BLOCK_START_PATTERN = re.compile(r"^\s*(?:local\s+function|function|if\b.*then$|for\b.*do$|while\b.*do$)")
_BLOCK_END_PATTERN = re.compile(r"^\s*end\s*$")
_DIRECT_FIELD_ASSIGNMENT_PATTERN = re.compile(
    r'(?:\[\s*"(?P<bracket>[A-Za-z_][A-Za-z0-9_]*)"\s*\]|\.(?P<dot>[A-Za-z_][A-Za-z0-9_]*))\s*='
)
_LOCAL_ARRAY_ALIAS_PATTERN = re.compile(
    r"\blocal\s+(?P<alias>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<source>wf\.vars\.[A-Za-z_][A-Za-z0-9_]*)\b"
)
_IP_ARRAY_LOOP_PATTERN = re.compile(
    r"for\s+_,\s*(?P<item>[A-Za-z_][A-Za-z0-9_]*)\s+in\s+ipairs\(\s*(?P<source>wf\.vars\.[A-Za-z_][A-Za-z0-9_]*|[A-Za-z_][A-Za-z0-9_]*)\s*\)\s+do(?P<body>.*?)\bend\b",
    re.DOTALL,
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
    task_spec: TaskSpec | None = None,
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
        task_spec=task_spec,
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

    if output_mode in _JSON_LUA_OUTPUT_MODES:
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
        if output_mode == LOWCODE_JSON:
            non_string_leaves = [
                location
                for location, value in _iter_leaf_values(payload)
                if not isinstance(value, str)
            ]
            if non_string_leaves:
                return _fail_report(
                    "format_validator",
                    ValidationFinding(
                        validator="format_validator",
                        failure_class="non_string_lua_value",
                        message="LowCode JSON output leaves must be JsonString values in lua{...}lua format.",
                        location=non_string_leaves[0],
                        repairable=True,
                        suggestion="Return generated Lua as JSON string values wrapped with lua{...}lua.",
                    ),
                )
            if not _iter_string_leaves(payload):
                return _fail_report(
                    "format_validator",
                    ValidationFinding(
                        validator="format_validator",
                        failure_class="missing_lua_value",
                        message="LowCode JSON output must contain at least one lua{...}lua string value.",
                        location="response",
                        repairable=True,
                        suggestion="Return a JSON object with a generated lua{...}lua string value.",
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
    task_spec: TaskSpec | None = None,
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
        task_spec=task_spec,
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
    findings.extend(
        _validate_forbidden_patterns(
            candidate,
            lua_segments,
            forbidden_patterns,
            output_mode=output_mode,
        )
    )

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
    task_spec: TaskSpec | None = None,
) -> ValidatorReport:
    if output_mode == CLARIFICATION:
        return ValidatorReport(validator="principle_validator", status="pass")

    lua_segments = _extract_lua_segments(candidate, output_mode)
    findings = _validate_archetype_specific(lua_segments, candidate, risk_tags, archetype, output_mode)
    if not findings and task_spec is not None:
        findings = _validate_task_spec_shape(lua_segments, task_spec)
    if findings:
        return ValidatorReport(validator="principle_validator", status="fail", findings=tuple(findings))
    return ValidatorReport(validator="principle_validator", status="pass")


def validate_runtime_behavior(
    candidate: str,
    *,
    output_mode: str,
    execution_context: Any | None,
    task_spec: TaskSpec,
) -> ValidatorReport:
    if output_mode == CLARIFICATION:
        return _skipped_report("runtime_validator", "clarification_mode")

    if task_spec.clarification_required:
        return _skipped_report("runtime_validator", "clarification_required")

    if execution_context is None:
        return _skipped_report("runtime_validator", "missing_execution_context")

    if not _supports_array_item_validation(task_spec):
        return _skipped_report("runtime_validator", "unsupported_operation")

    lua_segments = _extract_lua_segments(candidate, output_mode)
    if len(lua_segments) != 1:
        return _skipped_report("runtime_validator", "unsupported_output_shape")

    fixtures = _build_simple_extraction_runtime_fixtures(execution_context, task_spec)
    if not fixtures:
        return _skipped_report("runtime_validator", "missing_runtime_fixture")

    _, lua_segment = lua_segments[0]
    runtime_results: list[dict[str, object]] = []
    for fixture_name, fixture_context, expected_value in fixtures:
        actual_value, runtime_error = _execute_runtime_candidate(lua_segment, fixture_context)
        runtime_results.append(
            {
                "fixture": fixture_name,
                "expected": _format_runtime_value(expected_value),
                "actual": _format_runtime_value(actual_value),
                "error": runtime_error,
            }
        )
        if runtime_error is not None:
            return ValidatorReport(
                validator="runtime_validator",
                status="fail",
                findings=(
                    ValidationFinding(
                        validator="runtime_validator",
                        failure_class="runtime_execution_failed",
                        message=f"Runtime validation failed on fixture '{fixture_name}': {runtime_error}",
                        location="response",
                        repairable=True,
                        suggestion=_runtime_repair_suggestion(task_spec),
                    ),
                ),
                metadata=_build_runtime_metadata(
                    task_spec=task_spec,
                    runtime_results=runtime_results,
                    failed_fixture=fixture_name,
                    actual_value=actual_value,
                    expected_value=expected_value,
                ),
            )

        if not _runtime_values_match(
            expected_value,
            actual_value,
            output_mode=output_mode,
            task_spec=task_spec,
        ):
            return ValidatorReport(
                validator="runtime_validator",
                status="fail",
                findings=(
                    ValidationFinding(
                        validator="runtime_validator",
                        failure_class="runtime_behavior_mismatch",
                        message=(
                            f"Runtime validation failed on fixture '{fixture_name}': expected "
                            f"{_format_runtime_value(expected_value)} but got {_format_runtime_value(actual_value)}."
                        ),
                        location="response",
                        repairable=True,
                        suggestion=_runtime_repair_suggestion(task_spec),
                    ),
                ),
                metadata=_build_runtime_metadata(
                    task_spec=task_spec,
                    runtime_results=runtime_results,
                    failed_fixture=fixture_name,
                    actual_value=actual_value,
                    expected_value=expected_value,
                ),
            )

    return ValidatorReport(
        validator="runtime_validator",
        status="pass",
        metadata=_build_runtime_metadata(
            task_spec=task_spec,
            runtime_results=runtime_results,
            failed_fixture=None,
            actual_value=None,
            expected_value=None,
        ),
    )


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
    *,
    output_mode: str,
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
        if re.search(r"\bprint\s*\(", segment):
            findings.append(
                ValidationFinding(
                    validator="forbidden_patterns_validator",
                    failure_class="debug_output",
                    message="LowCode Lua output must not call print or emit debug output.",
                    location=location,
                    repairable=True,
                    suggestion="Remove print/debug output and return only the requested value.",
                )
            )
            return findings

        if output_mode == LOWCODE_JSON and re.search(r"\berror\s*\(", segment):
            findings.append(
                ValidationFinding(
                    validator="forbidden_patterns_validator",
                    failure_class="runtime_error_call",
                    message="LowCode Lua output must not throw runtime errors with error().",
                    location=location,
                    repairable=True,
                    suggestion="Return nil, false, or an empty string instead of throwing an error.",
                )
            )
            return findings

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
                    status="skipped",
                    skipped_reason="validator_timeout",
                    metadata={
                        "tool": "luacheck",
                        "message": "Tool timed out while checking the Lua snippet.",
                        "command": command,
                    },
                )
            except OSError as exc:
                return ValidatorReport(
                    validator="static_validator",
                    status="skipped",
                    skipped_reason="validator_execution_failed",
                    metadata={
                        "tool": "luacheck",
                        "message": f"Unable to execute the tool ({exc}).",
                        "command": command,
                    },
                )

            if completed.returncode == 0:
                continue

            details = _compact_tool_output(completed.stderr or completed.stdout)
            if _looks_like_luacheck_infrastructure_failure(details):
                return ValidatorReport(
                    validator="static_validator",
                    status="skipped",
                    skipped_reason="validator_execution_failed",
                    metadata={
                        "tool": "luacheck",
                        "message": details,
                        "command": command,
                    },
                )
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


def _build_simple_extraction_runtime_fixtures(
    execution_context: Any,
    task_spec: TaskSpec,
) -> list[tuple[str, Any, object]]:
    if not task_spec.input_roots:
        return []

    root = task_spec.input_roots[0]
    primary_value = _resolve_context_path(execution_context, root)
    expected_primary = _expected_simple_extraction_result(task_spec.operation, primary_value)
    fixtures: list[tuple[str, Any, object]] = [("primary", execution_context, expected_primary)]

    if not isinstance(primary_value, list):
        return fixtures

    if "single_item" in task_spec.edge_cases and primary_value:
        single_item_value = [primary_value[0]]
        fixtures.append(
            (
                "single_item",
                _clone_context_with_replaced_root(execution_context, root, single_item_value),
                _expected_simple_extraction_result(task_spec.operation, single_item_value),
            )
        )

    if "empty_array" in task_spec.edge_cases:
        fixtures.append(
            (
                "empty_array",
                _clone_context_with_replaced_root(execution_context, root, []),
                _expected_simple_extraction_result(task_spec.operation, []),
            )
        )

    return fixtures


def _expected_simple_extraction_result(operation: str, value: Any) -> object:
    if operation == "last_array_item":
        return value[-1] if isinstance(value, list) and value else None
    if operation == "first_array_item":
        return value[0] if isinstance(value, list) and value else None
    return value


def _resolve_context_path(node: Any, root: str) -> Any:
    current = node
    for part in root.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
            continue
        return None
    return current


def _clone_context_with_replaced_root(execution_context: Any, root: str, replacement: Any) -> Any:
    cloned = json.loads(json.dumps(execution_context))
    parts = root.split(".")
    current = cloned
    for part in parts[:-1]:
        if not isinstance(current, dict):
            return cloned
        current = current.setdefault(part, {})
    if isinstance(current, dict):
        current[parts[-1]] = replacement
    return cloned


def _execute_runtime_candidate(candidate: str, execution_context: Any) -> tuple[object | None, str | None]:
    lua_binary = _resolve_tool_binary("LUA_BIN", "lua")
    if lua_binary is None:
        return None, "lua runtime is unavailable."

    runtime_script = _build_runtime_script(candidate, execution_context)
    with tempfile.TemporaryDirectory(prefix="luamts-runtime-validator-") as temp_dir:
        file_path = Path(temp_dir) / "runtime_check.lua"
        file_path.write_text(runtime_script, encoding="utf-8")

        try:
            completed = subprocess.run(
                [lua_binary, str(file_path)],
                capture_output=True,
                text=True,
                check=False,
                timeout=_VALIDATOR_TOOL_TIMEOUT_SECONDS,
                env=_build_lua_runtime_environment(lua_binary),
            )
        except subprocess.TimeoutExpired:
            return None, "lua runtime timed out."
        except OSError as exc:
            return None, f"unable to execute lua runtime ({exc})."

    if completed.returncode != 0:
        details = _compact_tool_output(completed.stderr or completed.stdout)
        return None, details or "lua runtime returned a non-zero exit code."

    return _decode_runtime_result(completed.stdout.strip()), None


def _build_runtime_script(candidate: str, execution_context: Any) -> str:
    wf_context = execution_context.get("wf", {}) if isinstance(execution_context, dict) else {}
    prepared_candidate = _prepare_lua_segment_for_runtime(candidate)
    candidate_lines = prepared_candidate.splitlines() or ["return nil"]
    indented_candidate = "\n".join(f"  {line}" for line in candidate_lines)
    return "\n".join(
        [
            f"local wf = {_to_lua_literal(wf_context)}",
            "",
            "local function __encode_runtime_value(value)",
            "  if value == nil then",
            "    return 'nil'",
            "  end",
            "  local value_type = type(value)",
            "  if value_type == 'string' then",
            "    return 'string:' .. string.format('%q', value)",
            "  end",
            "  if value_type == 'number' then",
            "    return 'number:' .. string.format('%.17g', value)",
            "  end",
            "  if value_type == 'boolean' then",
            "    return 'boolean:' .. tostring(value)",
            "  end",
            "  return 'type:' .. value_type",
            "end",
            "",
            "local function __candidate__()",
            indented_candidate,
            "end",
            "",
            "local ok, result = pcall(__candidate__)",
            "if not ok then",
            "  io.stderr:write(tostring(result))",
            "  os.exit(1)",
            "end",
            "io.write(__encode_runtime_value(result))",
        ]
    )


def _prepare_lua_segment_for_runtime(candidate: str) -> str:
    stripped = candidate.strip()
    if not stripped:
        return "return nil"

    non_empty_lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    if len(non_empty_lines) == 1 and not _looks_like_lua(non_empty_lines[0]):
        return f"return {non_empty_lines[0]}"

    return candidate


def _decode_runtime_result(output: str) -> object:
    if output == "nil":
        return None
    if output.startswith("string:"):
        return ast.literal_eval(output[len("string:") :])
    if output.startswith("number:"):
        raw_number = output[len("number:") :]
        return int(raw_number) if re.fullmatch(r"-?\d+", raw_number) else float(raw_number)
    if output.startswith("boolean:"):
        return output[len("boolean:") :] == "true"
    return output


def _to_lua_literal(node: Any) -> str:
    if node is None:
        return "nil"
    if isinstance(node, bool):
        return "true" if node else "false"
    if isinstance(node, (int, float)):
        return repr(node)
    if isinstance(node, str):
        return json.dumps(node, ensure_ascii=False)
    if isinstance(node, list):
        return "{%s}" % ", ".join(_to_lua_literal(item) for item in node)
    if isinstance(node, dict):
        items = [f"[{json.dumps(str(key), ensure_ascii=False)}] = {_to_lua_literal(value)}" for key, value in node.items()]
        return "{%s}" % ", ".join(items)
    return "nil"


def _build_lua_runtime_environment(lua_binary: str) -> dict[str, str] | None:
    if not Path(lua_binary).is_file():
        return None

    environment = os.environ.copy()
    path_parts = [
        str(_LOCAL_MINGW_BIN_DIR),
        str(_LOCAL_LUA_BIN_DIR),
        environment.get("PATH", ""),
    ]
    environment["PATH"] = ";".join(part for part in path_parts if part)
    return environment


def _format_runtime_value(value: object) -> str:
    return repr(value)


def _runtime_repair_suggestion(task_spec: TaskSpec) -> str:
    root = task_spec.input_roots[0] if task_spec.input_roots else "the target input root"
    if task_spec.operation == "last_array_item":
        return f"Return the last element from {root} and nil when the array is empty."
    if task_spec.operation == "first_array_item":
        return f"Return the first element from {root} and nil when the array is empty."
    return f"Return the value from {root} without changing the requested result shape."


def _build_runtime_metadata(
    *,
    task_spec: TaskSpec,
    runtime_results: list[dict[str, object]],
    failed_fixture: str | None,
    actual_value: object | None,
    expected_value: object | None,
) -> dict[str, object]:
    behavioral_fingerprint = "|".join(
        f"{item['fixture']}={item['actual']}" if item["error"] is None else f"{item['fixture']}=error:{item['error']}"
        for item in runtime_results
    )
    metadata: dict[str, object] = {
        "operation": task_spec.operation,
        "expected_shape": task_spec.expected_shape,
        "edge_cases": list(task_spec.edge_cases),
        "runtime_results": runtime_results,
        "behavioral_fingerprint": behavioral_fingerprint,
    }
    if failed_fixture is not None:
        metadata["failed_fixture"] = failed_fixture
        metadata["actual_value"] = _format_runtime_value(actual_value)
        metadata["expected_value"] = _format_runtime_value(expected_value)
    return metadata


def _runtime_values_match(
    expected_value: object,
    actual_value: object,
    *,
    output_mode: str,
    task_spec: TaskSpec,
) -> bool:
    if expected_value is None and output_mode == JSON_WRAPPER and task_spec.operation in {"last_array_item", "first_array_item"}:
        return actual_value in {None, ""}
    return actual_value == expected_value


def _supports_array_item_validation(task_spec: TaskSpec) -> bool:
    if task_spec.operation not in _ARRAY_ITEM_OPERATIONS:
        return False
    if not task_spec.input_roots:
        return False
    if task_spec.archetype == "simple_extraction":
        return True
    return task_spec.expected_shape == "scalar_or_nil" and "array_indexing" in task_spec.risk_tags


def _validate_task_spec_shape(lua_segments: list[tuple[str, str]], task_spec: TaskSpec) -> list[ValidationFinding]:
    if not _supports_array_item_validation(task_spec):
        return []

    combined_segments = "\n".join(segment for _, segment in lua_segments)
    source = task_spec.input_roots[0]
    aliases = {
        match.group("alias"): match.group("source")
        for match in _local_array_alias_pattern_for_source(source).finditer(combined_segments)
    }
    if not _candidate_returns_array_source(combined_segments, source, aliases):
        return []

    item_position = "last" if task_spec.operation == "last_array_item" else "first"
    return [
        ValidationFinding(
            validator="principle_validator",
            failure_class="array_item_returns_whole_array",
            message="Candidate returns the whole array instead of the requested array item.",
            location="response",
            repairable=True,
            suggestion=f"Return the {item_position} element from {source}, or nil when the array is empty.",
        )
    ]


def _build_stylua_command(tool_binary: str, file_path: Path) -> list[str]:
    command = [tool_binary, "--check"]
    if _LOCAL_STYLUA_CONFIG.exists():
        command.extend(["--config-path", str(_LOCAL_STYLUA_CONFIG)])
    command.append(str(file_path))
    return command


def _build_luacheck_command(tool_binary: str, file_path: Path) -> list[str]:
    if _luacheck_requires_lua_launcher(tool_binary):
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


def _luacheck_requires_lua_launcher(tool_binary: str) -> bool:
    if os.name != "nt":
        return False
    tool_path = Path(tool_binary)
    if not tool_path.is_file():
        return False
    try:
        return tool_path.resolve() == _LOCAL_TOOL_PATHS["luacheck"].resolve()
    except OSError:
        return False


def _compact_tool_output(output: str) -> str:
    return " ".join(output.strip().split())


def _build_luacheck_environment(tool_binary: str) -> dict[str, str] | None:
    if not _luacheck_requires_lua_launcher(tool_binary):
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


def _looks_like_luacheck_infrastructure_failure(output: str) -> bool:
    normalized = output.lower()
    return (
        "luacheck" in normalized
        and (
            "unexpected symbol near" in normalized
            or "cannot open" in normalized
            or "module 'luacheck" in normalized
        )
    )


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

    if "array_allocation" in risk_tags and not _has_array_result_container(combined_segments):
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
        if not _has_array_result_container(combined_segments):
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


def _has_array_result_container(candidate: str) -> bool:
    return "_utils.array.new()" in candidate or has_in_place_array_field_enrichment(candidate)


def has_in_place_array_field_enrichment(candidate: str) -> bool:
    aliases = {match.group("alias"): match.group("source") for match in _LOCAL_ARRAY_ALIAS_PATTERN.finditer(candidate)}
    for match in _IP_ARRAY_LOOP_PATTERN.finditer(candidate):
        item = match.group("item")
        source = match.group("source")
        body = match.group("body")
        if not _loop_body_assigns_item_field(body, item):
            continue
        if re.search(r"\bif\b", body) and not _loop_body_assigns_same_item_field_in_conditional_branches(body, item):
            continue
        if _candidate_returns_array_source(candidate, source, aliases):
            return True
    return False


def _loop_body_assigns_item_field(body: str, item: str) -> bool:
    escaped_item = re.escape(item)
    return bool(re.search(rf"\b{escaped_item}\.[A-Za-z_][A-Za-z0-9_]*\s*=", body))


def _loop_body_assigns_same_item_field_in_conditional_branches(body: str, item: str) -> bool:
    escaped_item = re.escape(item)
    field_assignment = rf"\b{escaped_item}\.(?P<field>[A-Za-z_][A-Za-z0-9_]*)\s*="
    conditional_match = re.search(
        rf"\bif\b(?P<then>.*?)\belse\b(?P<else>.*?)(?:\bend\b|$)",
        body,
        re.DOTALL,
    )
    if conditional_match is None:
        return False

    then_fields = set(re.findall(field_assignment, conditional_match.group("then")))
    else_fields = set(re.findall(field_assignment, conditional_match.group("else")))
    return bool(then_fields & else_fields)


def _candidate_returns_array_source(candidate: str, source: str, aliases: dict[str, str]) -> bool:
    source_aliases = {source}
    if source in aliases:
        source_aliases.add(aliases[source])
    for alias, target in aliases.items():
        if target == source:
            source_aliases.add(alias)

    for source_name in source_aliases:
        if _candidate_returns_name_bare(candidate, source_name):
            return True
    return False


def _candidate_returns_name_bare(candidate: str, source_name: str) -> bool:
    escaped = re.escape(source_name)
    return bool(
        re.search(rf"(?m)\breturn\s+{escaped}\s*(?:$|[;,])", candidate)
        or re.search(rf"(?m)\breturn\s*\(\s*{escaped}\s*\)\s*(?:$|[;,])", candidate)
    )


def _local_array_alias_pattern_for_source(source: str) -> re.Pattern[str]:
    return re.compile(
        rf"\blocal\s+(?P<alias>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<source>{re.escape(source)})\b"
    )


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


def _iter_leaf_values(node: Any, location: str = "$") -> list[tuple[str, object]]:
    if isinstance(node, dict):
        leaves: list[tuple[str, object]] = []
        for key, value in node.items():
            leaves.extend(_iter_leaf_values(value, f"{location}.{key}"))
        return leaves
    if isinstance(node, list):
        leaves: list[tuple[str, object]] = []
        for index, value in enumerate(node):
            leaves.extend(_iter_leaf_values(value, f"{location}[{index}]"))
        return leaves
    return [(location, node)]


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
