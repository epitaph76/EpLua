import textwrap

import scripts.run_lua_7_progon_benchmark as benchmark_script


def test_parse_source_extracts_hint_and_solution() -> None:
    text = textwrap.dedent(
        """
        300 Lua-задач

        ==================================================
        ЗАДАЧА 042
        Категория: дата и время
        Запрос пользователя:
        Преобразуй дату.

        Контекст:
        {"wf":{"vars":{"date":"20231015"}}}

        Что нужно вернуть:
        ISO-строку.

        Сильная подсказка:
        - Возьми `wf.vars.date`.
        - Собери строку через `string.format`.

        Ожидаемое решение (Lua):
        return wf.vars.date
        """
    )

    cases = benchmark_script.parse_source_text(text)

    assert len(cases) == 1
    assert cases[0].task_id == "042"
    assert cases[0].hint.startswith("- Возьми")
    assert cases[0].solution == "return wf.vars.date"


def test_hint_prompt_keeps_original_task_and_adds_hint() -> None:
    case = benchmark_script.BenchmarkCase(
        task_id="001",
        category="извлечение данных",
        prompt="Верни значение.",
        context='{"wf":{"vars":{"value":1}}}',
        return_description="Число.",
        hint="- Используй wf.vars.value.",
        solution="return wf.vars.value",
    )

    prompt = benchmark_script.build_task_text(case, include_hint=True)

    assert "Верни значение." in prompt
    assert "Сильная подсказка:" in prompt
    assert "- Используй wf.vars.value." in prompt
