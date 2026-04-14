# How Validation Works

Этот документ фиксирует **текущее фактическое состояние** validation / generation pipeline в `luaMTS`.

Актуальное состояние после упрощения:

- рабочий контур сейчас намеренно сведён к **generator-only**;
- старый `planner -> prompter -> generator -> validators -> critic -> repair loop` больше не является активным happy path;
- следующие задачи должны быть сформулированы как **построение нового pipeline поверх стабильного generator baseline**, а не как точечная починка старого repair loop.

Если этот документ расходится с кодом, source of truth для current behavior остаётся код.

## 1. Current State

### 1.1. API path

Текущий API путь:

1. `POST /generate` приходит в `apps/api/routes/generate.py`.
2. Роут вызывает `GenerationService.generate(...)`.
3. `GenerationService` строит единый LowCode prompt через `build_lowcode_generator_prompt(...)`.
4. Модель вызывается через `model_adapter.generate_from_prompt(prompt)`.
5. Ответ модели возвращается наружу без validation / critic / repair.

Фактический trace:

```text
request_received -> generation -> response_ready
```

Фактический статус:

```text
validation_status = "not_run"
stop_reason = "not_run"
```

Важно:

- `archetype`, `output_mode`, `input_roots`, `risk_tags` сейчас не запускают старый quality loop;
- `validator_report = null`;
- `critic_report = null`;
- `repair_count = 0`;
- `clarification_count = 0`;
- debug payload показывает prompt и model call, но не validator pass/fail.

Ключевой файл:

- `apps/api/services/generation.py`

Ключевые функции:

- `GenerationService.generate(...)`
- `GenerationService._build_debug_payload(...)`

### 1.2. CLI with-api path

CLI `with-api` отправляет запрос в API и получает тот же generator-only результат.

Для debug сейчас ожидаемо видеть:

```text
Pipeline Trace:
request_received -> generation -> response_ready

Agent Layers:
generation:generator
```

При этом `Validation Passes` пустой, потому что validation ещё не построена заново.

### 1.3. CLI without-api path

CLI `without-api` идёт напрямую в Ollama `/api/generate`.

Фактический trace:

```text
request_received -> direct_ollama -> response_ready
```

Фактический статус:

```text
validation_status = "not_run"
```

Это означает только то, что validation не запускалась. Модель при этом запускалась.

Debug payload для `without-api` должен показывать:

- `prompt_package.prompt`;
- `pipeline_layers` со stage `direct_ollama`;
- `model_calls[0].request_payload`;
- `model_calls[0].raw_response`;
- пустой `validation_passes`.

Ключевой файл:

- `apps/api/cli/main.py`

Ключевые функции:

- `_generate_without_api(...)`
- `_print_pipeline_debug(...)`
- `_print_literal(...)`

Важно про вывод CLI:

- сгенерированный код и debug JSON печатаются literal-выводом;
- это нужно, чтобы Rich markup не съедал Lua-индексы вида `[#wf.vars.emails]`.

### 1.4. Inline JSON и context

CLI больше не должен автоматически отрезать хвостовой inline JSON из task text.

Текущее правило:

- если JSON написан прямо в строке задачи, он остаётся частью `task_text`;
- если нужен отдельный context, используй `/context <json-or-path>` или `--context`;
- сужение context по roots возможно только для явного context, а не для произвольной части task text.

Это сделано, чтобы CLI не принимал семантические решения за пользователя и не менял prompt незаметно.

## 2. Current LowCode Generator Contract

Generator prompt собирается в:

- `packages/orchestrator/prompter.py`

Ключевая функция:

- `build_lowcode_generator_prompt(task_text, provided_context)`

Текущий глобальный контракт:

- модель генерирует Lua 5.5 выражения / скрипты для LowCode;
- ответ должен быть только JSON object;
- каждое Lua-значение должно быть строкой формата `lua{<Lua код>}lua`;
- нельзя добавлять markdown, пояснения, debug output, demonstration JSON или текст вокруг JSON object;
- нельзя использовать JsonPath;
- нельзя создавать новые поля внутри `wf.vars` или `wf.initVariables`, если пользователь явно не попросил изменить существующие данные;
- если задача просит получить значение, Lua внутри `lua{...}lua` должен возвращать значение через `return`;
- все LowCode-переменные лежат в `wf.vars`;
- входные variables лежат в `wf.initVariables`;
- для доступа к элементам существующего массива используется обычная Lua-индексация.

Пример ожидаемого ответа для задачи “получи последний email”:

```json
{
  "result": "lua{return wf.vars.emails[#wf.vars.emails]}lua"
}
```

## 3. What Is Not Active Now

Следующие части кода могут оставаться в репозитории, но сейчас не являются активным happy path для `/generate`:

- `build_domain_prompt_package(...)`;
- planner agent layer;
- prompter agent layer;
- `run_quality_loop(...)`;
- format / syntax / static / principle validators;
- runtime behavioral validation;
- semantic critic;
- deterministic repair tools;
- repair loop;
- bounded repair / oscillation policy.

Их нельзя описывать как “текущий pipeline”, пока они снова явно не подключены и не подтверждены тестами.

## 4. Why The Old Document Was Wrong

Старый документ описывал фактический контур так, будто `/generate` уже проходит через:

```text
build_domain_prompt_package -> run_quality_loop -> validators -> critic -> repair
```

В текущей ветке это неверно.

Правильный current-state:

```text
raw task/context -> LowCode generator prompt -> model call -> raw model response
```

Validation пока отсутствует как runtime decision layer.

## 5. Source Files For Current State

Открывай эти файлы, если нужно проверить фактическое поведение:

| Что проверить | Файл |
| --- | --- |
| API entrypoint | `apps/api/routes/generate.py` |
| API generator-only service | `apps/api/services/generation.py` |
| LowCode generator prompt | `packages/orchestrator/prompter.py` |
| CLI direct Ollama path | `apps/api/cli/main.py` |
| Runtime options including temperature | `apps/api/runtime_policy.py` |
| API schema for runtime options | `apps/api/schemas.py` |
| Generator-only service regression | `apps/api/tests/test_quality_loop.py` |
| CLI debug / direct Ollama / literal output regression | `apps/api/tests/test_cli.py` |

## 6. Confirmed Tests

Полезные тесты для текущего состояния:

- `test_generation_service_uses_generator_only_lowcode_contract_without_agentic_layers`
  - подтверждает, что `GenerationService` вызывает только generator prompt;
  - подтверждает `validation_status = "not_run"`;
  - подтверждает отсутствие agent calls и validation passes.
- `test_cli_generate_without_api_debug_prints_ollama_request_payload`
  - подтверждает debug payload для direct Ollama path.
- `test_cli_chat_temperature_command_updates_status_and_direct_ollama_payload`
  - подтверждает slash-команду `/temperature` и проброс в Ollama options.
- `test_cli_literal_print_preserves_lua_length_index_markup`
  - подтверждает, что CLI literal output не теряет `[#...]` из Lua-кода.
- `test_cli_chat_keeps_inline_json_inside_task_text`
  - подтверждает, что inline JSON больше не вырезается из task text.

Команда для проверки:

```powershell
python -m pytest .\apps\api\tests -q
```

## 7. New Pipeline Goal

Следующая большая задача: построить новый validation pipeline поверх generator-only baseline.

Целевой принцип:

```text
generate first, validate explicitly, repair only after observable failure
```

Новый pipeline должен быть:

- явным;
- трассируемым;
- тестируемым;
- без silent mutation model output;
- без хардкода под один пример;
- без CLI-эвристик, которые заранее решают semantic intent за пользователя;
- без скрытой “починки” candidate между generator и validator.

## 8. Proposed Pipeline Stages

Ниже не описание текущей реализации, а target backlog.

### Stage 0. Keep Generator Baseline

Цель:

- сохранить текущий generator-only path как baseline;
- не ломать LowCode prompt contract;
- не возвращать старый repair loop целиком.

Done criteria:

- `/generate` стабильно возвращает model response;
- debug показывает prompt и raw response;
- `validation_status = "not_run"` остаётся честным, пока validation не подключена.

### Stage 1. Output Contract Validation

Цель:

- добавить первый post-generator validator только для outer response shape.

Проверять:

- ответ является JSON object;
- все Lua values являются строками;
- Lua strings обёрнуты в `lua{...}lua`;
- нет markdown;
- нет текста до или после JSON object;
- нет `print` / debug output;
- Lua fragment использует `return`, если задача просит получить значение.

Не делать:

- не чинить ответ автоматически;
- не запускать repair;
- не менять candidate перед validator;
- не хардкодить `emails`.

Минимальный результат:

```text
request_received -> generation -> output_contract_validation -> response_ready
```

### Stage 2. LowCode Static Rules

Цель:

- проверить доменные ограничения Lua / LowCode без исполнения.

Проверять:

- JsonPath не используется;
- доступ идёт через `wf.vars` или `wf.initVariables`;
- новые поля не создаются без явного запроса на mutation/save;
- используются разрешённые конструкции;
- массивы создаются через `_utils.array.new()`, если создаётся новый массив;
- существующие массивы индексируются обычной Lua-индексацией.

Минимальный результат:

```text
request_received -> generation -> output_contract_validation -> lowcode_static_validation -> response_ready
```

### Stage 3. Runtime Behavioral Validation

Цель:

- проверять фактическое поведение на простых кейсах, где есть безопасный execution context.

Порядок:

1. извлечь Lua fragment из JSON object;
2. собрать runtime fixture из `provided_context`;
3. выполнить Lua в контролируемой среде;
4. сравнить actual result с expected behavior.

Важно:

- runtime validator должен запускаться только для поддержанных операций;
- если операция не определена, status должен быть `skipped`, а не fake pass;
- first slice лучше делать на простых extraction задачах, например last array item / first array item.

### Stage 4. Critic Decision Layer

Цель:

- добавить отдельный decision layer поверх validator reports.

Critic должен решать:

- `finalize`;
- `repair`;
- `clarification`;
- `bounded_failure`.

Важно:

- critic не должен скрывать validator failure;
- semantic reasoning не должен подменять фактический runtime failure;
- любые false-positive overrides должны быть явными и трассируемыми.

### Stage 5. Repair Loop

Цель:

- вернуть repair только после того, как есть явные validator reports.

Правила:

- repair prompt должен получать компактный structured failure summary;
- repair iteration должна добавлять trace entry;
- repeated failure / oscillation должны быть ограничены budget'ом;
- deterministic repairs допустимы только как отдельный явный stage с report.

Не делать:

- не восстанавливать старый `repair_loop.py` как black box;
- не чинить model output перед validation без report;
- не смешивать cleanup, validation и repair в одном helper'е.

### Stage 6. Observability And Metrics

Цель:

- сделать debug trail достаточным для расследования каждого решения.

Debug должен показывать:

- prompt package;
- raw model response;
- каждый validation stage;
- статус каждого stage;
- findings;
- repair prompt, если repair был;
- final decision и stop reason.

## 9. Task Backlog

Актуальные задачи теперь такие:

1. Зафиксировать generator-only baseline.
   - Статус: done in current branch.
   - Проверка: generator-only тесты и debug trace.

2. Добавить `output_contract_validation`.
   - Статус: next.
   - Минимальный scope: JSON object + `lua{...}lua` string leaves + no markdown/prose.
   - Без repair.

3. Добавить `lowcode_static_validation`.
   - Статус: pending.
   - Минимальный scope: JsonPath, forbidden roots, mutation without explicit request.

4. Добавить `runtime_behavior_validation`.
   - Статус: pending.
   - Минимальный scope: supported extraction operations with explicit context.

5. Добавить critic decision layer.
   - Статус: pending.
   - Минимальный scope: choose finalize / clarification / bounded_failure before repair.

6. Добавить repair loop.
   - Статус: pending.
   - Минимальный scope: one repair attempt from structured validator report.

7. Добавить metrics / regression suite.
   - Статус: pending.
   - Минимальный scope: compare generator-only baseline vs validated pipeline on типовые запросы.

## 10. Non-goals For The Next Slice

Для следующего шага не нужно:

- возвращать старый planner / prompter agent path;
- возвращать весь старый `run_quality_loop(...)`;
- чинить старый `_prepare_candidate_for_validation(...)`;
- добавлять silent cleanup;
- добавлять task-specific hacks для `emails`;
- заставлять CLI парсить смысл задачи;
- делать repair до появления validator reports.

## 11. Smallest Viable Next Slice

Самый маленький полезный следующий шаг:

```text
generator-only -> output_contract_validation -> response
```

Минимальные файлы для чтения:

- `apps/api/services/generation.py`
- `packages/orchestrator/prompter.py`
- `apps/api/schemas.py`
- `apps/api/tests/test_quality_loop.py`

Минимальные файлы для изменения:

- новый или существующий validator module для output contract;
- `apps/api/services/generation.py`;
- `apps/api/tests/test_quality_loop.py`;
- при необходимости `apps/api/schemas.py`.

Минимальное поведение:

- валидный JSON object с `lua{...}lua` получает `validation_status = "passed"`;
- markdown / prose / invalid JSON получает `validation_status = "failed"`;
- debug показывает отдельный `output_contract_validation` stage;
- raw model output не мутируется перед validation.

## 12. Operational Summary

Текущий контур:

```text
/generate -> GenerationService.generate -> build_lowcode_generator_prompt -> generate_from_prompt -> response_ready
```

Целевой следующий контур:

```text
/generate -> generator -> output_contract_validation -> response_ready
```

Полный новый pipeline нужно строить постепенно после этого, stage by stage.
