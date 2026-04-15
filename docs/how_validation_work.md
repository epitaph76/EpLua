# How Validation Works

Этот документ описывает фактический validation / generation pipeline в текущем `luaMTS`.

Если документ расходится с кодом, source of truth остаётся код:

- `apps/api/services/generation.py`
- `packages/orchestrator/prompter.py`
- `packages/validators/core.py`

## 1. API path

Основной путь:

```text
POST /generate
-> apps/api/routes/generate.py
-> GenerationService.generate(...)
```

Planning path:

```text
POST /plan
-> apps/api/routes/generate.py
-> GenerationService.plan(...)
-> clarifier
-> planner
```

Live-progress путь:

```text
POST /generate/progress
```

`/generate/progress` отдаёт NDJSON events:

```text
{"type":"progress","stage":"planner","index":2}
{"type":"final","payload":{...}}
```

CLI в режиме `with-api` предпочитает `/generate/progress`, чтобы показывать слои во время работы API.

## 2. Current pipeline

Happy path:

```text
request_received
-> planner
-> prompter
-> generation
-> deterministic_validation
-> semantic_validation
-> response_ready
```

Repair path:

```text
request_received
-> planner
-> prompter
-> generation
-> deterministic_validation
-> repair_generation
-> deterministic_validation
-> semantic_validation
-> response_ready
```

`repair_prompter` в текущем API path не используется. Repair идёт напрямую в generator: prompt собирается локально из исходного prompt package, текущего candidate, validation report и critic instruction.

Если repair budget исчерпан, interactive path может закончиться `assisted_repair_summarizer` перед `response_ready`, чтобы показать пользователю краткую сводку проблемы и варианты следующей широкой итерации.

## 3. Planner

Planner agent не генерирует Lua.

Он возвращает компактный JSON со смыслом задачи:

```json
{
  "arch": "transformation",
  "op": "last_array_item",
  "mode": "raw_lua",
  "roots": ["wf.vars.emails"],
  "shape": "scalar_or_nil",
  "risks": ["array_indexing"],
  "edges": ["single_item", "empty_array"],
  "clar": false,
  "q": null,
  "intents": ["extract_last_email"]
}
```

Локальная обвязка разворачивает это в `TaskSpec`.

## 4. Prompter

Prompter agent тоже не генерирует Lua.

Он получает:

- `TaskSpec`;
- task intents;
- исходную задачу пользователя;
- краткую сводку текущего generator prompt.

Он возвращает только patch-additions:

```json
{
  "sys": ["короткая системная подсказка"],
  "user": ["короткая пользовательская подсказка"]
}
```

Полный generator prompt собирается локально.

Важное правило: prompter additions не имеют права противоречить LowCode-контракту. Добавки, которые просят `error()`, "бросить ошибку", "вернуть ошибку" и похожие инструкции, фильтруются и не попадают в prompt generator-а.

## 5. Generator

Generator получает полный LowCode prompt и должен вернуть только JSON object.

Пример:

```json
{
  "result": "lua{local emails = wf.vars.emails\nif not emails or #emails == 0 then\n  return nil\nend\nreturn emails[#emails]}lua"
}
```

В реальном raw JSON переносы внутри строки должны быть экранированы как `\\n`. CLI может показывать их как настоящие строки только в human view.

## 6. Truncation guard

Для generator и repair-generation включён guard против обрезки на `num_predict`.

Алгоритм:

1. Перед каждым generator-stage создаётся временный файл.
2. Первый chunk ответа пишется в этот файл.
3. Если metadata показывает `eval_count >= num_predict`, считается, что вывод мог быть обрезан.
4. Pipeline строит continuation prompt:
   - исходный prompt;
   - уже полученный fragment;
   - инструкция дописать недостающую часть без повторения лишнего.
5. Следующий chunk добавляется в тот же временный файл.
6. Цикл продолжается, пока chunk не станет короче лимита или пока не достигнут внутренний лимит продолжений.
7. Полный текст из временного файла идёт в deterministic validation.
8. После финального статуса задачи временные файлы удаляются.

Для repair-generation создаётся отдельный временный файл, потому что это новый generator-stage с новым prompt.

В debug model call guard отображается так:

```json
{
  "truncation_guard": {
    "continuation_count": 1,
    "limit_reached": false,
    "chunks": [
      {"index": 1, "truncated": true, "eval_count": 256, "num_predict": 256},
      {"index": 2, "truncated": false, "eval_count": 108, "num_predict": 256}
    ],
    "temporary_file_used": true
  }
}
```

## 7. Deterministic validation

Validation pipeline запускается для output mode `LOWCODE_JSON`.

Слои:

1. `format_validator`
   - outer JSON должен быть валидным JSON object;
   - все Lua-значения должны быть строками `lua{...}lua`;
   - без markdown и prose вокруг JSON.
2. `syntax_validator`
   - извлекает Lua-сегменты;
   - проверяет базовую Lua-структуру;
   - использует `stylua`, если он доступен.
3. `static_validator`
   - использует `luacheck`, если он доступен;
   - проверяет path/root restrictions;
   - ловит forbidden patterns.
4. `principle_validator`
   - проверяет проектные LowCode-principles.
5. `rule_validator`
   - агрегирует format/syntax/static/principle findings в итоговый rule report.

Если format validation падает, остальные слои пропускаются, потому что Lua-сегменты ещё нельзя безопасно извлечь.

## 8. Critic report and repair

`critic_report` принимает структурированные отчёты validators и выбирает действие:

- `finalize`;
- `repair`;
- bounded failure / stop.

После успешного deterministic validation отдельно вызывается `semantic_critic`. Это LLM-agent, который проверяет смысловое соответствие task semantics: правильное поле, правильную операцию, правильную форму ответа и отсутствие промаха мимо user intent.

Repair prompt строится локально:

- исходная задача;
- `TaskSpec`;
- текущий невалидный candidate;
- краткий validation report;
- critic instruction.

Дальше вызывается `repair_generation`, а не отдельный repair LLM-agent.

## 9. Status semantics

Возможные итоговые статусы:

- `passed` - первая генерация прошла validation;
- `repaired` - validation прошла после repair;
- `failed` - repair budget исчерпан или ошибка неремонтируемая;
- `not_run` - только для прямых путей, где validation намеренно не запускается.

`stop_reason` уточняет причину:

- `passed`;
- `deterministic_validation_failed`;
- `repair_exhausted`;
- другие bounded stop reasons.

## 10. CLI display

Важно различать raw data и human view.

Внутри API и validator candidate остаётся чистой JSON-строкой с `\\n`.

CLI для основного результата делает human view:

- `\\n` показывает как реальные строки;
- `\"` показывает как `"`;
- это только отображение, не вход validator-а.

Debug JSON печатается без глобальной замены `\\n`, чтобы отчёт оставался похож на настоящий JSON и не создавал ложные хвостовые `\`.

## 11. Debug artifacts

Debug response содержит:

- `trace`;
- `validator_report`;
- `critic_report`;
- `repair_count`;
- `debug.prompt_package`;
- `debug.pipeline_layers`;
- `debug.agent_layer_calls`;
- `debug.model_calls`;
- `debug.validation_passes`.

Live progress в CLI показывает:

```text
Debug progress:
  слой 1: request_received прошёл
  слой 2: planner прошёл
  слой 3: prompter прошёл
  слой 4: generation прошёл
  слой 5: deterministic_validation прошёл
  слой 6: semantic_validation прошёл
  слой 7: response_ready прошёл
```

## 12. LowCode hard rules

Проектные правила:

- Lua 5.5-compatible style;
- script string format: `lua{...}lua`;
- JsonPath запрещён;
- все LowCode variables: `wf.vars`;
- init variables: `wf.initVariables`;
- новый массив: `_utils.array.new()`;
- существующий массив при необходимости: `_utils.array.markAsArray(arr)`;
- `error()` запрещён в LowCode JSON output.

## 13. Useful tests

```powershell
python -m pytest apps\api\tests packages\benchmark\tests -q
```

Фокусные тесты:

- `apps/api/tests/test_quality_loop.py`;
- `apps/api/tests/test_validator_tools.py`;
- `apps/api/tests/test_cli.py`;
- `apps/api/tests/test_prompter.py`;
- `packages/benchmark/tests/test_run_lua_7_progon_benchmark.py`.
