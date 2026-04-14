import json

from packages.orchestrator.prompter import (
    apply_lowcode_prompter_agent_response,
    build_lowcode_prompt_builder_result,
)


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
