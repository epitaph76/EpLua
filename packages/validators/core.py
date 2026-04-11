from __future__ import annotations

import json
import re
from typing import Any

from packages.shared.quality import ValidationFinding, ValidatorReport

RAW_LUA = "raw_lua"
JSON_WRAPPER = "json_wrapper"
PATCH_MODE = "patch_mode"
CLARIFICATION = "clarification"

_WF_ROOT_PATTERN = re.compile(r"wf\.(?:vars|initVariables)\.[A-Za-z0-9_\.]+")
_JSONPATH_PATTERN = re.compile(r"[$@]\.[A-Za-z0-9_.*]+")
_BLOCK_START_PATTERN = re.compile(r"^\s*(?:local\s+function|function|if\b.*then$|for\b.*do$|while\b.*do$)")
_BLOCK_END_PATTERN = re.compile(r"^\s*end\s*$")


def run_validation_pipeline(
    candidate: str,
    *,
    output_mode: str,
    allowed_data_roots: tuple[str, ...],
    forbidden_patterns: tuple[str, ...],
    risk_tags: tuple[str, ...],
    archetype: str,
) -> tuple[str | None, ValidatorReport, ValidatorReport]:
    format_report = validate_format(candidate, output_mode)
    normalized_candidate = format_report.normalized_candidate

    if format_report.status != "pass":
        return normalized_candidate, format_report, ValidatorReport(
            validator="rule_validator",
            status="skipped",
            skipped_reason="format_validation_failed",
        )

    rule_report = validate_rules(
        normalized_candidate or candidate.strip(),
        output_mode=output_mode,
        allowed_data_roots=allowed_data_roots,
        forbidden_patterns=forbidden_patterns,
        risk_tags=risk_tags,
        archetype=archetype,
    )
    return normalized_candidate, format_report, rule_report


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

    findings: list[ValidationFinding] = []
    lua_segments = _extract_lua_segments(candidate, output_mode)

    findings.extend(_validate_paths(lua_segments, allowed_data_roots))
    findings.extend(_validate_forbidden_patterns(candidate, lua_segments, forbidden_patterns))
    findings.extend(_validate_lua_syntax(lua_segments))
    findings.extend(_validate_archetype_specific(lua_segments, candidate, risk_tags, archetype, output_mode))

    if findings:
        return ValidatorReport(
            validator="rule_validator",
            status="fail",
            findings=tuple(findings),
        )
    return ValidatorReport(validator="rule_validator", status="pass")


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

    if "timezone_offset" in risk_tags and not all(
        token in combined_segments for token in ("offset_sign", "offset_hour", "offset_min")
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
