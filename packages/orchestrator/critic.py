from __future__ import annotations

import json
import re

from packages.orchestrator.agent_prompt import AgentMessage, AgentPrompt
from packages.orchestrator.task_spec import TaskSpec
from packages.shared.quality import ValidationBundle, ValidationFinding, ValidatorReport
from packages.shared.language import DEFAULT_LANGUAGE, natural_language_name, normalize_language

_THINK_BLOCK_PATTERN = re.compile(r"<think>\s*(.*?)\s*</think>", re.DOTALL | re.IGNORECASE)
_CONTROL_TOKEN_PATTERN = re.compile(r"<\|[A-Za-z0-9_:-]+\|>")
_CODE_FENCE_PATTERN = re.compile(r"```(?:json|lua)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_PATTERN_BASED_PRINCIPLE_FAILURES = {
    "missing_field_whitelist_pattern",
}
_MAX_REPAIR_ATTEMPTS = 4


def build_critic_report(
    format_report: ValidatorReport,
    syntax_report: ValidatorReport,
    static_report: ValidatorReport,
    principle_report: ValidatorReport,
    runtime_report: ValidatorReport,
    semantic_report: ValidatorReport,
    *,
    output_mode: str,
    repair_count: int,
    clarification_count: int,
    repeated_failure_class: bool,
    oscillation_detected: bool,
    task_intents: tuple[str, ...] = (),
    language: str = DEFAULT_LANGUAGE,
    validation_bundle: ValidationBundle | None = None,
) -> dict[str, object]:
    normalized_language = normalize_language(language)
    findings = _collect_findings(
        format_report,
        syntax_report,
        static_report,
        principle_report,
        runtime_report,
        semantic_report,
    )
    if not findings:
        return {
            "action": "finalize",
            "failure_class": None,
            "message": _localize_text("Validation passed.", normalized_language),
        }

    primary = _localized_finding(_select_primary_finding(findings, validation_bundle), normalized_language)

    if _has_validator_conflict(principle_report, semantic_report):
        semantic_priority_report = _semantic_priority_conflict_resolution(
            semantic_report=semantic_report,
            task_intents=task_intents,
            repair_count=repair_count,
            language=normalized_language,
        )
        if semantic_priority_report is not None:
            return semantic_priority_report
        return {
            "action": "finalize",
            "failure_class": "validator_conflict",
            "message": _localize_text(
                "Semantic and pattern-based validators disagree on the repair direction.",
                normalized_language,
            ),
        }

    if primary.ambiguous and clarification_count < 1:
        return {
            "action": "clarification",
            "failure_class": primary.failure_class,
            "message": primary.message,
            "clarification_question": primary.suggestion
            or _localize_text("What should be clarified before the next attempt?", normalized_language),
        }

    if oscillation_detected:
        if clarification_count < 1:
            return {
                "action": "clarification",
                "failure_class": "repair_oscillation",
                "message": _localize_text(
                    "Repair loop started oscillating between previously seen candidates or failure patterns.",
                    normalized_language,
                ),
                "clarification_question": _localize_text(
                    "The repair loop is repeating the same failure. What should be changed before the next attempt?",
                    normalized_language,
                ),
            }
        return {
            "action": "finalize",
            "failure_class": "repair_oscillation",
            "message": _localize_text(
                "Repair loop started oscillating between previously seen candidates or failure patterns.",
                normalized_language,
            ),
        }

    if repair_count >= _MAX_REPAIR_ATTEMPTS:
        if clarification_count < 1:
            return {
                "action": "clarification",
                "failure_class": primary.failure_class,
                "message": _localize_text(
                    "Repair budget exhausted or the same failure repeated after the latest repair.",
                    normalized_language,
                ),
                "clarification_question": primary.suggestion
                or _localize_text(
                    "I still cannot produce a valid candidate after several repair attempts. What should I change?",
                    normalized_language,
                ),
            }
        return {
            "action": "finalize",
            "failure_class": primary.failure_class,
            "message": _localize_text(
                "Repair budget exhausted or the same failure repeated after the latest repair.",
                normalized_language,
            ),
        }

    if not primary.repairable:
        if clarification_count < 1:
            return {
                "action": "clarification",
                "failure_class": primary.failure_class,
                "message": primary.message,
                "clarification_question": primary.suggestion
                or _localize_text("What additional input is needed to safely continue?", normalized_language),
            }
        return {
            "action": "finalize",
            "failure_class": primary.failure_class,
            "message": _localize_text("Validation failed with a non-repairable issue.", normalized_language),
        }

    repair_message, repair_prompt = _build_repair_instructions(
        primary,
        output_mode,
        normalized_language,
        validation_bundle=validation_bundle,
    )
    return {
        "action": "repair",
        "failure_class": primary.failure_class,
        "message": repair_message,
        "repair_prompt": repair_prompt,
    }


def _collect_findings(*reports: ValidatorReport) -> list[ValidationFinding]:
    findings: list[ValidationFinding] = []
    for report in reports:
        findings.extend(report.findings)
    return findings


def _select_primary_finding(
    findings: list[ValidationFinding],
    validation_bundle: ValidationBundle | None,
) -> ValidationFinding:
    if validation_bundle is None or not validation_bundle.repair_priority:
        return findings[0]

    priority_index = {failure_class: index for index, failure_class in enumerate(validation_bundle.repair_priority)}
    return min(findings, key=lambda finding: priority_index.get(finding.failure_class, len(priority_index)))


def _build_repair_instructions(
    finding: ValidationFinding,
    output_mode: str,
    language: str,
    *,
    validation_bundle: ValidationBundle | None,
) -> tuple[str, str]:
    if finding.failure_class == "semantic_mismatch":
        fallback = (
            finding.suggestion
            or _localize_text(
                "Return the same output mode and user goal, but repair the candidate so it satisfies the task semantics. Return only the repaired result.",
                language,
            )
        )
        return (
            _localize_text(
                "Repair the candidate to match the task semantics without changing the user goal.",
                language,
            ),
            fallback,
        )

    if finding.failure_class == "runtime_behavior_mismatch":
        fallback = _runtime_behavior_repair_prompt(
            finding,
            validation_bundle=validation_bundle,
            language=language,
        )
        return (
            _localize_text(
                "Repair the candidate to match the runtime behavior expected by the task.",
                language,
            ),
            fallback,
        )

    if finding.failure_class == "markdown_fence" and output_mode == "lowcode_json":
        fallback = _localize_text(
            "Return only a plain JSON object with lua{...}lua string values. Remove markdown fences and any surrounding explanation without changing the user goal.",
            language,
        )
        return (
            _localize_text("Remove markdown fences and keep the LowCode JSON lua{...}lua contract.", language),
            fallback,
        )

    if finding.failure_class == "markdown_fence" and output_mode == "raw_lua":
        fallback = _localize_text(
            "Return only raw Lua code. Remove markdown fences and any surrounding explanation without changing the user goal.",
            language,
        )
        return (
            _localize_text("Remove markdown fences and keep the output in raw_lua mode.", language),
            fallback,
        )

    if finding.failure_class == "invalid_wrapper":
        fallback = _localize_text(
            "Return the same JSON object shape, but ensure every generated code string uses lua{...}lua wrappers and contains no extra prose.",
            language,
        )
        return (
            _localize_text(
                "Wrap every generated code string with lua{...}lua without changing the JSON shape.",
                language,
            ),
            fallback,
        )

    if finding.failure_class in {"disallowed_data_root", "mixed_root_families"}:
        fallback = _localize_text(
            "Repair the candidate by using only the allowed wf.* data roots. Do not invent new roots and do not change the user goal.",
            language,
        )
        return (
            _localize_text(
                "Keep the same user goal, but restrict the candidate to the allowed wf.* data roots.",
                language,
            ),
            fallback,
        )

    if finding.failure_class.startswith("missing_") or finding.failure_class.startswith("empty_"):
        fallback = _localize_text(
            "Repair the candidate by adding the missing domain-specific logic while preserving the requested output mode and user goal.",
            language,
        )
        return (
            _localize_text(
                "Add the missing domain-specific element without changing the requested result shape.",
                language,
            ),
            fallback,
        )

    fallback = _localize_text(
        "Repair the current candidate using the validator finding. Keep the same output mode, preserve the user goal, and return only the repaired result.",
        language,
    )
    return (
        _localize_text(
            f"Repair the candidate for failure class {finding.failure_class} without changing the user goal.",
            language,
        ),
        fallback,
    )


def _runtime_behavior_repair_prompt(
    finding: ValidationFinding,
    *,
    validation_bundle: ValidationBundle | None,
    language: str,
) -> str:
    if validation_bundle is None:
        return finding.suggestion or _localize_text(
            "Repair the candidate so its actual runtime result matches the task expectation. Return only the repaired result.",
            language,
        )

    task_spec = validation_bundle.task_spec
    root = task_spec.input_roots[0] if task_spec.input_roots else "the target input root"
    metadata = validation_bundle.runtime_report.metadata or {}
    failed_fixture = metadata.get("failed_fixture")
    expected_value = metadata.get("expected_value")
    actual_value = metadata.get("actual_value")

    if language == "ru":
        operation_hint = _ru_operation_hint(task_spec.operation, root)
        details = [
            f"Текущий кандидат не соответствует operation={task_spec.operation}.",
            operation_hint,
            f"Ожидаемая форма результата: {task_spec.expected_shape}.",
        ]
        if failed_fixture is not None:
            details.append(f"Сломанный runtime fixture: {failed_fixture}.")
        if expected_value is not None or actual_value is not None:
            details.append(f"Ожидалось: {expected_value}; фактически получено: {actual_value}.")
        details.append("Не возвращай весь входной массив, если expected_shape требует scalar_or_nil.")
        details.append("Верни только исправленный результат без объяснений.")
        return " ".join(details)

    operation_hint = _en_operation_hint(task_spec.operation, root)
    details = [
        f"The current candidate does not satisfy operation={task_spec.operation}.",
        operation_hint,
        f"Expected result shape: {task_spec.expected_shape}.",
    ]
    if failed_fixture is not None:
        details.append(f"Failed runtime fixture: {failed_fixture}.")
    if expected_value is not None or actual_value is not None:
        details.append(f"Expected: {expected_value}; actual: {actual_value}.")
    details.append("Do not return the whole input array when expected_shape requires scalar_or_nil.")
    details.append("Return only the repaired result with no explanation.")
    return " ".join(details)


def _ru_operation_hint(operation: str, root: str) -> str:
    if operation == "last_array_item":
        return f"Нужно вернуть последний элемент массива из {root}, либо nil для пустого массива."
    if operation == "first_array_item":
        return f"Нужно вернуть первый элемент массива из {root}, либо nil для пустого массива."
    return f"Нужно вернуть значение из {root} в форме, соответствующей задаче."


def _en_operation_hint(operation: str, root: str) -> str:
    if operation == "last_array_item":
        return f"Return the last element from {root}, or nil for an empty array."
    if operation == "first_array_item":
        return f"Return the first element from {root}, or nil for an empty array."
    return f"Return the value from {root} in the shape expected by the task."


def _has_validator_conflict(principle_report: ValidatorReport, semantic_report: ValidatorReport) -> bool:
    if principle_report.status != "fail" or semantic_report.status != "fail":
        return False
    return any(
        finding.failure_class in _PATTERN_BASED_PRINCIPLE_FAILURES
        for finding in principle_report.findings
    ) and any(
        finding.failure_class == "semantic_mismatch"
        for finding in semantic_report.findings
    )


def _semantic_priority_conflict_resolution(
    *,
    semantic_report: ValidatorReport,
    task_intents: tuple[str, ...],
    repair_count: int,
    language: str,
) -> dict[str, object] | None:
    if repair_count >= _MAX_REPAIR_ATTEMPTS:
        return None

    if not {"clear_target_fields", "remove_target_fields"} & set(task_intents):
        return None

    semantic_finding = next(
        (finding for finding in semantic_report.findings if finding.failure_class == "semantic_mismatch"),
        None,
    )
    if semantic_finding is None:
        return None

    return {
        "action": "repair",
        "failure_class": semantic_finding.failure_class,
        "message": _localize_text(
            "Prefer semantic intent over the pattern-based rule for explicit field-operation tasks.",
            language,
        ),
        "repair_prompt": semantic_finding.suggestion
        or _localize_text(
            "Repair the candidate to match the explicit field-operation intent and keep unrelated fields intact.",
            language,
        ),
    }


def build_semantic_critic_prompt(
    *,
    prompt: str,
    candidate: str,
    output_mode: str,
    task_spec: TaskSpec | None = None,
    format_report: ValidatorReport,
    syntax_report: ValidatorReport,
    static_report: ValidatorReport,
    principle_report: ValidatorReport,
    runtime_report: ValidatorReport,
    language: str = DEFAULT_LANGUAGE,
) -> str:
    return build_semantic_critic_agent_prompt(
        prompt=prompt,
        candidate=candidate,
        output_mode=output_mode,
        task_spec=task_spec,
        format_report=format_report,
        syntax_report=syntax_report,
        static_report=static_report,
        principle_report=principle_report,
        runtime_report=runtime_report,
        language=language,
    ).to_legacy_prompt()


def build_semantic_critic_agent_prompt(
    *,
    prompt: str,
    candidate: str,
    output_mode: str,
    task_spec: TaskSpec | None = None,
    format_report: ValidatorReport,
    syntax_report: ValidatorReport,
    static_report: ValidatorReport,
    principle_report: ValidatorReport,
    runtime_report: ValidatorReport,
    language: str = DEFAULT_LANGUAGE,
) -> AgentPrompt:
    normalized_language = normalize_language(language)
    validator_summary = json.dumps(
        {
            "mode": output_mode,
            "format": _compact_validator_report(format_report),
            "syntax": _compact_validator_report(syntax_report),
            "static": _compact_validator_report(static_report),
            "principle": _compact_validator_report(principle_report),
            "runtime": _compact_validator_report(runtime_report, include_metadata=True),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    task_summary = json.dumps(
        _semantic_task_summary(prompt=prompt, output_mode=output_mode, task_spec=task_spec),
        ensure_ascii=False,
        separators=(",", ":"),
    )
    system_prompt = "\n".join(
        (
            "You are the semantic critic for a LocalScript Lua generation system.",
            "Judge only whether the candidate satisfies the task semantics.",
            "Ignore syntax/format issues already handled by validators.",
            "Check wrong field, wrong array item, wrong transformation, wrong return value, wrong payload shape, or missed intent.",
            f"Write the JSON message and suggestion fields in {natural_language_name(normalized_language)}.",
            "Return one compact minified JSON object only.",
            'Use short keys: s=status, c=failure_class, m=message, fix=suggestion.',
            'Pass shape: {"s":"pass","m":"ok"}',
            'Fail shape: {"s":"fail","c":"semantic_mismatch","m":"why","fix":"repair instruction"}',
        )
    )
    user_prompt = "\n".join(
        (
            "Task:",
            task_summary,
            "Candidate:",
            candidate,
            "Validators:",
            validator_summary,
        )
    )
    return AgentPrompt(
        agent_name="semantic_critic",
        messages=(
            AgentMessage(role="system", content=system_prompt),
            AgentMessage(role="user", content=user_prompt),
        ),
    )


def _semantic_task_summary(*, prompt: str, output_mode: str, task_spec: TaskSpec | None) -> dict[str, object]:
    summary: dict[str, object] = {"mode": output_mode}
    if task_spec is not None:
        summary.update(
            {
                "text": task_spec.task_text,
                "op": task_spec.operation,
                "shape": task_spec.expected_shape,
                "roots": list(task_spec.input_roots),
                "risks": list(task_spec.risk_tags),
            }
        )
        return summary

    extracted_task = _extract_generation_prompt_task(prompt)
    if extracted_task:
        summary["text"] = extracted_task
    return summary


def _extract_generation_prompt_task(prompt: str) -> str | None:
    match = re.search(r"(?:^|\n)Task:\s*\n(?P<task>.*?)(?:\nProvided context:|\Z)", prompt, re.DOTALL)
    if match is None:
        return None
    task = re.sub(r"\s+", " ", match.group("task")).strip()
    return task or None


def _compact_validator_report(report: ValidatorReport, *, include_metadata: bool = False) -> dict[str, object]:
    compact: dict[str, object] = {"s": report.status}
    if report.skipped_reason:
        compact["skip"] = report.skipped_reason
    if report.findings:
        compact["f"] = [
            {
                "c": finding.failure_class,
                "m": finding.message,
                "fix": finding.suggestion,
            }
            for finding in report.findings[:3]
        ]
    if include_metadata and report.metadata:
        compact["meta"] = _compact_runtime_metadata(report.metadata)
    return compact


def _compact_runtime_metadata(metadata: dict[str, object]) -> dict[str, object]:
    return {
        key: value
        for key, value in metadata.items()
        if key in {"runtime_results", "failed_fixture", "actual_value", "expected_value", "behavioral_fingerprint"}
    }


def parse_semantic_critic_response(raw_response: str) -> ValidatorReport:
    cleaned = _THINK_BLOCK_PATTERN.sub("\n", raw_response or "")
    cleaned = _CONTROL_TOKEN_PATTERN.sub("", cleaned)
    fence_match = _CODE_FENCE_PATTERN.search(cleaned)
    if fence_match is not None:
        cleaned = fence_match.group(1).strip()

    object_start = cleaned.find("{")
    object_end = cleaned.rfind("}")
    if object_start == -1 or object_end == -1 or object_end < object_start:
        return _invalid_semantic_critic_report()

    try:
        payload = json.loads(cleaned[object_start : object_end + 1])
    except json.JSONDecodeError:
        return _invalid_semantic_critic_report()

    status = str(payload.get("status") or payload.get("s") or "").strip().lower()
    if status in {"pass", "p", "ok"}:
        return ValidatorReport(validator="semantic_validator", status="pass")

    if status not in {"fail", "f"}:
        return _invalid_semantic_critic_report()

    finding = ValidationFinding(
        validator="semantic_validator",
        failure_class=str(payload.get("failure_class") or payload.get("c") or "semantic_mismatch"),
        message=str(payload.get("message") or payload.get("m") or "Candidate does not satisfy the task semantics."),
        location="response",
        repairable=bool(payload.get("repairable", True)),
        ambiguous=bool(payload.get("ambiguous", False)),
        suggestion=(
            str(payload.get("suggestion"))
            if payload.get("suggestion") is not None
            else str(payload.get("fix"))
            if payload.get("fix") is not None
            else str(payload.get("repair_prompt"))
            if payload.get("repair_prompt") is not None
            else None
        ),
    )
    return ValidatorReport(
        validator="semantic_validator",
        status="fail",
        findings=(finding,),
    )


def _invalid_semantic_critic_report() -> ValidatorReport:
    return ValidatorReport(
        validator="semantic_validator",
        status="fail",
        findings=(
            ValidationFinding(
                validator="semantic_validator",
                failure_class="semantic_critic_invalid_response",
                message="Semantic critic did not return valid JSON.",
                location="semantic_critic.response",
                repairable=True,
                ambiguous=False,
                suggestion="Regenerate the candidate with explicit task semantics because semantic validation did not complete.",
            ),
        ),
        skipped_reason="semantic_critic_invalid_response",
    )


def _localized_finding(finding: ValidationFinding, language: str) -> ValidationFinding:
    return ValidationFinding(
        validator=finding.validator,
        failure_class=finding.failure_class,
        message=_localize_text(finding.message, language),
        location=finding.location,
        repairable=finding.repairable,
        ambiguous=finding.ambiguous,
        suggestion=_localize_text(finding.suggestion, language) if finding.suggestion else None,
    )


def _localize_text(text: str | None, language: str) -> str:
    if text is None:
        return ""
    normalized_language = normalize_language(language)
    if normalized_language == "en":
        return text
    return _RU_TRANSLATIONS.get(text, text)


_RU_TRANSLATIONS = {
    "Validation passed.": "Валидация пройдена.",
    "Semantic and pattern-based validators disagree on the repair direction.": "Семантический и шаблонный валидаторы расходятся в направлении исправления.",
    "What should be clarified before the next attempt?": "Что нужно уточнить перед следующей попыткой?",
    "Repair loop started oscillating between previously seen candidates or failure patterns.": "Цикл исправления начал зацикливаться на уже встречавшихся вариантах или типах ошибок.",
    "The repair loop is repeating the same failure. What should be changed before the next attempt?": "Цикл исправления повторяет ту же ошибку. Что нужно изменить перед следующей попыткой?",
    "Repair budget exhausted or the same failure repeated after the latest repair.": "Лимит исправлений исчерпан или после последнего исправления снова повторилась та же ошибка.",
    "I still cannot produce a valid candidate after several repair attempts. What should I change?": "После нескольких попыток всё ещё не получается получить валидный кандидат. Что мне изменить?",
    "What additional input is needed to safely continue?": "Какой дополнительный ввод нужен, чтобы безопасно продолжить?",
    "Validation failed with a non-repairable issue.": "Валидация завершилась неисправимой ошибкой.",
    "Semantic critic did not return valid JSON.": "Семантический критик не вернул валидный JSON.",
    "Regenerate the candidate with explicit task semantics because semantic validation did not complete.": "Сгенерируй кандидат заново с явной семантикой задачи, потому что семантическая валидация не завершилась.",
    "Repair the candidate to match the task semantics without changing the user goal.": "Исправь кандидат так, чтобы он соответствовал смыслу задачи, не меняя цель пользователя.",
    "Repair the candidate to match the runtime behavior expected by the task.": "Исправь кандидат так, чтобы его runtime-поведение соответствовало задаче.",
    "Return the same output mode and user goal, but repair the candidate so it satisfies the task semantics. Return only the repaired result.": "Сохрани тот же режим вывода и цель пользователя, но исправь кандидат так, чтобы он удовлетворял смыслу задачи. Верни только исправленный результат.",
    "Repair the candidate so its actual runtime result matches the task expectation. Return only the repaired result.": "Исправь кандидат так, чтобы его фактический runtime-результат соответствовал ожиданию задачи. Верни только исправленный результат.",
    "Remove markdown fences and keep the output in raw_lua mode.": "Убери markdown-ограждения и сохрани режим вывода raw_lua.",
    "Return only raw Lua code. Remove markdown fences and any surrounding explanation without changing the user goal.": "Верни только чистый Lua-код. Убери markdown-ограждения и любые пояснения вокруг, не меняя цель пользователя.",
    "Remove markdown fences and keep the LowCode JSON lua{...}lua contract.": "Убери markdown-ограждения и сохрани LowCode JSON контракт lua{...}lua.",
    "Return only a plain JSON object with lua{...}lua string values. Remove markdown fences and any surrounding explanation without changing the user goal.": "Верни только чистый JSON object со строками lua{...}lua. Убери markdown-ограждения и любые пояснения вокруг, не меняя цель пользователя.",
    "Wrap every generated code string with lua{...}lua without changing the JSON shape.": "Оберни каждую сгенерированную строку кода в lua{...}lua, не меняя форму JSON.",
    "Return the same JSON object shape, but ensure every generated code string uses lua{...}lua wrappers and contains no extra prose.": "Сохрани ту же форму JSON-объекта, но убедись, что каждая сгенерированная строка кода использует обёртку lua{...}lua и не содержит лишнего текста.",
    "Keep the same user goal, but restrict the candidate to the allowed wf.* data roots.": "Сохрани ту же цель пользователя, но ограничь кандидат разрешёнными корнями данных wf.*.",
    "Repair the candidate by using only the allowed wf.* data roots. Do not invent new roots and do not change the user goal.": "Исправь кандидат, используя только разрешённые корни данных wf.*. Не придумывай новые корни и не меняй цель пользователя.",
    "Add the missing domain-specific element without changing the requested result shape.": "Добавь недостающий доменный элемент, не меняя требуемую форму результата.",
    "Repair the candidate by adding the missing domain-specific logic while preserving the requested output mode and user goal.": "Исправь кандидат, добавив недостающую доменную логику, сохранив требуемый режим вывода и цель пользователя.",
    "Repair the current candidate using the validator finding. Keep the same output mode, preserve the user goal, and return only the repaired result.": "Исправь текущий кандидат по замечанию валидатора. Сохрани тот же режим вывода, цель пользователя и верни только исправленный результат.",
    "Prefer semantic intent over the pattern-based rule for explicit field-operation tasks.": "Для задач с явными операциями над полями предпочитай семантическое намерение, а не шаблонное правило.",
    "Repair the candidate to match the explicit field-operation intent and keep unrelated fields intact.": "Исправь кандидат так, чтобы он соответствовал явному намерению операции над полями и сохранял несвязанные поля без изменений.",
    "Candidate mixes wf.vars.* and wf.initVariables.* without a safe basis.": "Кандидат смешивает wf.vars.* и wf.initVariables.* без безопасного основания.",
    "Which data root should be used: wf.vars.* or wf.initVariables.*?": "Какой источник данных нужно использовать: wf.vars.* или wf.initVariables.*?",
    "Candidate does not satisfy the task semantics.": "Кандидат не соответствует смыслу задачи.",
}
