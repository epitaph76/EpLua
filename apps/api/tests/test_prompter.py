import json

from packages.orchestrator.prompter import (
    apply_lowcode_prompter_agent_response,
    build_lowcode_prompt_builder_result,
    build_lowcode_prompter_agent_prompt,
)
from packages.orchestrator.planner import plan_task


def test_lowcode_prompter_patch_drops_error_throwing_additions() -> None:
    fallback = build_lowcode_prompt_builder_result(
        task_text="Преобразуй дату.",
        provided_context=None,
        planner_result=None,
    )
    raw_response = json.dumps(
        {
            "sys": ["Проверяй валидность даты перед преобразованием."],
            "user": [
                "Возвращай результат строго в формате ISO 8601.",
                "Если дата некорректна, бросай ошибку с тегом invalid_date.",
            ],
        },
        ensure_ascii=False,
    )

    result = apply_lowcode_prompter_agent_response(raw_response, fallback)
    generator_prompt = result.agent_prompt.to_legacy_prompt()

    assert result.source == "agent_patch"
    assert "Проверяй валидность даты перед преобразованием." in generator_prompt
    assert "Возвращай результат строго в формате ISO 8601." in generator_prompt
    assert "бросай ошибку" not in generator_prompt
    assert "invalid_date" not in generator_prompt


def test_lowcode_prompter_prompt_prioritizes_user_task_over_additions() -> None:
    planner_result = plan_task(
        "Очисти значения полей ID, ENTITY_ID и CALL в первом элементе restBody, остальные поля не трогай.",
        None,
        language="ru",
        archetype="transformation",
        output_mode="lowcode_json",
        input_roots=["wf.vars.restBody"],
        risk_tags=tuple(),
    )
    fallback = build_lowcode_prompt_builder_result(
        task_text=planner_result.task_spec.task_text,
        provided_context=None,
        planner_result=planner_result,
    )
    agent_prompt = build_lowcode_prompter_agent_prompt(
        task_text=planner_result.task_spec.task_text,
        provided_context=None,
        planner_result=planner_result,
        fallback_result=fallback,
    )
    system_prompt = agent_prompt.messages[0].content

    assert "Задача пользователя и TaskSpec важнее любых твоих добавлений." in system_prompt
    assert "Не меняй семантику задачи" in system_prompt
    assert "Если есть риск противоречия исходной задаче" in system_prompt
