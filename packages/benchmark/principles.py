from __future__ import annotations

import json
import re
from typing import Any

from packages.orchestrator.domain_adapter import build_domain_prompt_package
from packages.validators.core import run_validation_pipeline

_KEY_LITERAL_PATTERN = re.compile(r"""(?:\b(?:key|k)\s*~=\s*|\[\s*)["']([^"']+)["']""")
_NUMERIC_OPERAND = r"(?:\([^()\n]+\)|#[A-Za-z_][A-Za-z0-9_\.]*(?:\[[^\]\n]+\])?|\b[A-Za-z_][A-Za-z0-9_\.]*(?:\[[^\]\n]+\])?|\d+(?:\.\d+)?)"
_NUMERIC_EXPRESSION_PATTERN = re.compile(
    rf"{_NUMERIC_OPERAND}\s*[+\-*/%^]\s*{_NUMERIC_OPERAND}"
)
_SINGLETON_ARRAY_WRAP_PATTERN = re.compile(r"(?:return|=)\s*\{\s*[^}\n]+\s*\}")
_RETURN_EXPRESSION_PATTERN = re.compile(r"^\s*return(?:\s+(.*))?$", re.MULTILINE)


def evaluate_case_by_principles(case: dict[str, Any], candidate: str) -> dict[str, object]:
    context = json.dumps(case["context"], ensure_ascii=False) if case.get("context") is not None else None
    prompt_package = build_domain_prompt_package(
        case["prompt"],
        context,
        archetype=case["archetype"],
        output_mode=case["primary_output_mode"],
        input_roots=case["input_roots"],
        risk_tags=case["risk_tags"],
    )

    normalized_candidate, format_report, syntax_report, static_report, principle_report, rule_report = run_validation_pipeline(
        candidate,
        output_mode=prompt_package.output_mode,
        allowed_data_roots=prompt_package.allowed_data_roots,
        forbidden_patterns=prompt_package.forbidden_patterns,
        risk_tags=prompt_package.risk_tags,
        archetype=prompt_package.archetype,
    )
    normalized = normalized_candidate or candidate.strip()

    checks = [
        _report_check("format_contract", format_report),
        _report_check("syntax_contract", syntax_report),
        _report_check("static_contract", static_report),
        _report_check("principle_contract", principle_report),
        *_evaluate_case_specific_checks(case, normalized),
    ]
    required_checks = [check for check in checks if check["required"]]
    passed_required = [check for check in required_checks if check["status"] == "pass"]

    return {
        "status": "pass" if len(required_checks) == len(passed_required) else "fail",
        "normalized_candidate": normalized,
        "checks": checks,
        "summary": {
            "required": len(required_checks),
            "passed": len(passed_required),
        },
        "rule_report": rule_report.to_dict(),
    }


def _report_check(name: str, report) -> dict[str, object]:
    return {
        "name": name,
        "status": "pass" if report.status == "pass" else "fail",
        "required": True,
        "message": report.findings[0].message if report.findings else f"{name} passed.",
    }


def _evaluate_case_specific_checks(case: dict[str, Any], candidate: str) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []
    risk_tags = set(case.get("risk_tags", []))
    expected_output = case["expected_outputs"][case["primary_output_mode"]]

    if case["primary_output_mode"] == "patch_mode":
        checks.append(_patch_key_check(expected_output, candidate))

    if "field_whitelist" in risk_tags:
        checks.append(_field_whitelist_check(expected_output, candidate))

    if "field_value_clearing" in risk_tags:
        checks.append(_field_value_clearing_check(expected_output, candidate))

    if "datetime_format" in risk_tags:
        checks.append(
            _boolean_check(
                "datetime_iso_shape",
                "string.format" in candidate and ".000000Z" in candidate,
                "Candidate must compose an ISO 8601 string with the expected UTC suffix.",
            )
        )

    if "array_allocation" in risk_tags:
        checks.append(
            _boolean_check(
                "array_result_allocation",
                "_utils.array.new()" in candidate and "table.insert" in candidate,
                "Filtering tasks must allocate a result array and append matching items.",
            )
        )

    if "type_normalization" in risk_tags:
        checks.append(
            _boolean_check(
                "type_normalization_guard",
                _has_type_normalization_guard(candidate),
                "Normalization tasks must guard input type and wrap singleton values when needed.",
            )
        )
        checks.append(_type_normalization_return_contract_check(case, candidate))

    if "numeric_transform" in risk_tags and case["primary_output_mode"] != "patch_mode":
        checks.append(
            _boolean_check(
                "numeric_operation_present",
                _has_numeric_operation(candidate),
                "Numeric transformation tasks should preserve an explicit numeric operation.",
            )
        )

    return checks


def _has_numeric_operation(candidate: str) -> bool:
    return bool(
        "tonumber(" in candidate
        or re.search(r"\bmath\.(?:floor|ceil|abs|min|max)\s*\(", candidate)
        or _NUMERIC_EXPRESSION_PATTERN.search(candidate)
    )


def _has_type_normalization_guard(candidate: str) -> bool:
    has_non_array_table_guard = bool(
        "pairs(" in candidate and re.search(r'type\(\s*(?:key|k)\s*\)\s*~=\s*"number"', candidate)
    )
    has_scalar_type_guard = bool(re.search(r'type\(\s*\w+\s*\)\s*==\s*"(?:string|number|boolean)"', candidate))
    return bool(
        "type(" in candidate
        and _SINGLETON_ARRAY_WRAP_PATTERN.search(candidate)
        and (has_non_array_table_guard or has_scalar_type_guard)
    )


def _type_normalization_return_contract_check(case: dict[str, Any], candidate: str) -> dict[str, object]:
    target_leaf = _target_leaf_from_input_roots(case.get("input_roots", []))
    return _boolean_check(
        "type_normalization_return_contract",
        target_leaf is None or _candidate_returns_target_leaf(candidate, target_leaf),
        "Normalization tasks must return the expected normalized output shape.",
    )


def _target_leaf_from_input_roots(input_roots: object) -> str | None:
    if not isinstance(input_roots, list) or not input_roots:
        return None
    root = str(input_roots[0]).strip()
    if not root:
        return None
    return root.split(".")[-1]


def _candidate_returns_target_leaf(candidate: str, target_leaf: str) -> bool:
    escaped = re.escape(target_leaf)
    for match in _RETURN_EXPRESSION_PATTERN.finditer(candidate):
        expression = (match.group(1) or "").strip()
        if not expression:
            continue
        if re.search(rf"(?:^|[\.{{\s]){escaped}\b", expression):
            return True
    return False


def _patch_key_check(expected_output: dict[str, Any], candidate: str) -> dict[str, object]:
    try:
        payload = json.loads(candidate)
    except json.JSONDecodeError:
        payload = {}

    expected_keys = set(expected_output.keys())
    candidate_keys = set(payload.keys()) if isinstance(payload, dict) else set()
    return _boolean_check(
        "patch_expected_keys_present",
        expected_keys.issubset(candidate_keys),
        f"Patch payload must contain the expected additive keys: {', '.join(sorted(expected_keys))}.",
    )


def _field_whitelist_check(expected_output: object, candidate: str) -> dict[str, object]:
    key_literals = _extract_expected_key_literals(expected_output)
    return _boolean_check(
        "field_whitelist_preservation",
        _matches_field_whitelist_family(candidate, key_literals),
        "Whitelist-like tasks must preserve the target fields through explicit key checks or direct named field updates.",
    )


def _field_value_clearing_check(expected_output: object, candidate: str) -> dict[str, object]:
    key_literals = _extract_expected_key_literals(expected_output)
    return _boolean_check(
        "field_value_clearing",
        all(_candidate_assigns_named_field(candidate, literal) for literal in key_literals),
        "Field-clearing tasks must update the named fields directly instead of reshaping the whole object.",
    )


def _extract_expected_key_literals(expected_output: object) -> set[str]:
    return set(_KEY_LITERAL_PATTERN.findall(str(expected_output)))


def _matches_field_whitelist_family(candidate: str, key_literals: set[str]) -> bool:
    if not key_literals:
        return False

    if all(_candidate_compares_key_to_literal(candidate, literal) for literal in key_literals):
        return True

    return all(_candidate_assigns_named_field(candidate, literal) for literal in key_literals)


def _candidate_compares_key_to_literal(candidate: str, literal: str) -> bool:
    escaped = re.escape(literal)
    return bool(re.search(rf'\b(?:key|k)\s*~=\s*"{escaped}"', candidate))


def _candidate_assigns_named_field(candidate: str, literal: str) -> bool:
    escaped = re.escape(literal)
    return bool(
        re.search(rf'\[\s*"{escaped}"\s*\]\s*=', candidate)
        or re.search(rf"\.{escaped}\b\s*=", candidate)
    )


def _boolean_check(name: str, passed: bool, message: str) -> dict[str, object]:
    return {
        "name": name,
        "status": "pass" if passed else "fail",
        "required": True,
        "message": message,
    }
