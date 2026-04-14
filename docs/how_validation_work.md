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
plan explicitly -> prompt explicitly -> generate -> validate deterministically -> critic decides -> repair with bounded retries
```

Новый pipeline должен быть:

- явным;
- трассируемым;
- тестируемым;
- без silent mutation model output;
- без хардкода под один пример;
- без CLI-эвристик, которые заранее решают semantic intent за пользователя;
- без скрытой “починки” candidate между generator и validator;
- с bounded repair budget;
- с truncation guard на каждом agentic этапе.

Целевой полный контур:

```text
request_received
-> planner
-> prompter
-> generator
-> deterministic_validation
-> critic
-> finalize | repair_prompting | clarification
```

Если critic выбирает repair:

```text
critic_findings
-> prompter
-> generator
-> deterministic_validation
-> critic
```

Если после 4 прогонов candidate generation результат всё ещё невалидный или critic не может уверенно завершить задачу, pipeline должен остановиться и попросить уточнение у пользователя.

Agentic truncation guard:

- применяется к `planner`, `prompter`, `generator`, `critic` и repair-вариантам этих шагов;
- если agentic output упёрся в лимит `num_predict`, например `256` токенов, считать это подозрением на truncation;
- такой output нельзя молча принимать как нормальный успех;
- тот же agentic stage должен быть запущен заново с явной инструкцией сделать ответ короче и вернуть только минимально нужную структуру;
- truncation retry должен быть ограниченным и трассируемым, чтобы не получить бесконечный цикл.

## 8. Proposed Pipeline Stages

Ниже не описание текущей реализации, а target backlog.

### Stage 0. Keep Generator Baseline

Цель:

- сохранить текущий generator-only path как baseline;
- не ломать LowCode prompt contract;
- не возвращать старый repair loop целиком как black box.

Done criteria:

- `/generate` стабильно возвращает model response;
- debug показывает prompt и raw response;
- `validation_status = "not_run"` остаётся честным, пока новый pipeline не подключен.

### Stage 1. Planner Agent

Цель:

- вернуть planner как явный agentic слой, но без старых CLI-эвристик.

Planner получает:

- raw `task_text`;
- raw или явно переданный `provided_context`;
- structural facts, если они есть и явно помечены как facts/hints.

Planner возвращает компактный `TaskSpec`:

- `operation`;
- `output_mode`;
- `input_roots`;
- `expected_shape`;
- `risk_tags`;
- `edge_cases`;
- `clarification_required`;
- `clarification_question`.

Не делать:

- не генерировать Lua;
- не принимать обрезанный output за успех;
- не подменять пустой/битый planner output молчаливым semantic fallback.

### Stage 2. Prompter Agent

Цель:

- собрать короткий и точный prompt для generator из `TaskSpec`, user task и context.

Prompter получает:

- `TaskSpec` от planner;
- raw user task;
- relevant context;
- hard output contract.

Prompter возвращает:

- system/user prompt или короткие prompt sections для generator;
- без Lua candidate;
- без полного echo всего fallback prompt.

Правило:

- если prompter упёрся в `num_predict`, stage повторяется с требованием сократить output.

### Stage 3. Generator Agent

Цель:

- получить candidate в текущем LowCode формате.

Generator получает:

- prompt от prompter;
- task/context в достаточном для решения виде;
- output contract.

Generator возвращает:

- только JSON object;
- Lua-фрагменты только как JSON string values формата `lua{<Lua код>}lua`;
- без markdown, prose, debug text, copied prompt text.

Пример:

```json
{
  "result": "lua{return wf.vars.emails[#wf.vars.emails]}lua"
}
```

Правило:

- если generator output упёрся в `num_predict`, это не validator failure, а agentic truncation failure;
- generator stage повторяется с указанием вернуть более короткий JSON candidate.

### Stage 4. Deterministic Validation

Цель:

- проверить candidate неагентно перед critic.

Проверять outer contract:

- ответ является JSON object;
- все generated Lua values являются JSON strings;
- Lua strings обёрнуты в `lua{...}lua`;
- нет markdown;
- нет текста до или после JSON object;
- нет `print` / debug output;
- Lua fragment использует `return`, если задача просит получить значение.

Проверять Lua syntax:

- извлечь каждый `lua{...}lua` fragment;
- проверить, что Lua fragment синтаксически валиден;
- не исполнять код на этом этапе;
- не использовать LLM для синтаксической проверки.

Не делать:

- не чинить ответ автоматически;
- не запускать repair;
- не менять candidate перед validator;
- не хардкодить `emails`.

Минимальный результат:

```text
request_received -> planner -> prompter -> generator -> deterministic_validation
```

### Stage 5. Critic Decision Layer

Цель:

- добавить отдельный decision layer поверх validator reports.

Critic получает:

- `TaskSpec`;
- raw candidate от generator;
- deterministic validator reports;
- номер текущего прогона;
- историю предыдущих failures, если это repair iteration.

Critic решает:

- `finalize`;
- `repair`;
- `clarification`;
- `bounded_failure`.

Важно:

- critic не должен скрывать validator failure;
- critic не должен чинить candidate сам;
- critic должен вернуть structured findings для prompter, если выбран `repair`;
- если critic output упёрся в `num_predict`, critic stage повторяется с требованием короткого structured decision.

### Stage 6. Repair Prompting And Bounded Loop

Цель:

- вернуть repair только после того, как есть явные validator reports.

Repair flow:

```text
critic findings -> prompter repair prompt -> generator repaired candidate -> deterministic_validation -> critic
```

Prompter на repair iteration получает:

- исходный `TaskSpec`;
- текущий invalid candidate;
- validator findings;
- critic findings;
- короткую инструкцию, что именно исправить.

Правила budget:

- repair prompt должен получать компактный structured failure summary;
- repair iteration должна добавлять trace entry;
- максимум 4 прогона candidate generation;
- если после 4 прогонов не получилось, остановиться и попросить уточнение у пользователя;
- repeated failure / oscillation должны быть отдельными stop reasons;
- deterministic repairs не добавлять на первом этапе нового pipeline.

Не делать:

- не восстанавливать старый `repair_loop.py` как black box;
- не чинить model output перед validation без report;
- не смешивать cleanup, validation и repair в одном helper'е.

### Stage 7. Observability And Metrics

Цель:

- сделать debug trail достаточным для расследования каждого решения.

Debug должен показывать:

- prompt package;
- raw model response;
- каждый agentic stage;
- каждый deterministic validation stage;
- статус каждого stage;
- findings;
- truncation retries;
- repair prompt, если repair был;
- final decision и stop reason.

## 9. Task Backlog

Актуальные задачи теперь такие:

1. Зафиксировать generator-only baseline.
   - Статус: done in current branch.
   - Проверка: generator-only тесты и debug trace.

2. Вернуть `planner` как явный agentic stage.
   - Статус: next.
   - Минимальный scope: planner возвращает compact `TaskSpec`, без Lua.

3. Вернуть `prompter` как явный agentic stage.
   - Статус: pending.
   - Минимальный scope: prompter строит короткий generator prompt из `TaskSpec`.

4. Подключить `generator` к prompter output.
   - Статус: pending.
   - Минимальный scope: generator возвращает JSON object с `lua{...}lua`.

5. Добавить deterministic validation.
   - Статус: pending.
   - Минимальный scope: JSON object + `lua{...}lua` string leaves + Lua syntax check.

6. Добавить critic decision layer.
   - Статус: pending.
   - Минимальный scope: choose finalize / repair / clarification from validator reports.

7. Добавить repair loop через prompter.
   - Статус: pending.
   - Минимальный scope: critic findings -> prompter repair prompt -> generator, max 4 generation passes.

8. Добавить truncation guard на agentic stages.
   - Статус: pending.
   - Минимальный scope: если output ровно упёрся в `num_predict=256`, retry того же stage с "сделай короче".

9. Добавить metrics / regression suite.
   - Статус: pending.
   - Минимальный scope: compare generator-only baseline vs validated pipeline on типовые запросы.

## 10. Non-goals For The Next Slice

Для следующего шага не нужно:

- возвращать весь старый `run_quality_loop(...)`;
- чинить старый `_prepare_candidate_for_validation(...)`;
- добавлять silent cleanup;
- добавлять task-specific hacks для `emails`;
- заставлять CLI парсить смысл задачи;
- делать repair до появления validator reports;
- добавлять runtime behavioral validation до базовой deterministic validation.

## 11. Smallest Viable Next Slice

Самый маленький полезный следующий шаг:

```text
generator-only baseline -> planner -> prompter -> generator
```

Минимальные файлы для чтения:

- `apps/api/services/generation.py`
- `packages/orchestrator/prompter.py`
- `packages/orchestrator/planner.py`
- `apps/api/schemas.py`
- `apps/api/tests/test_quality_loop.py`

Минимальные файлы для изменения:

- `apps/api/services/generation.py`;
- `packages/orchestrator/planner.py`;
- `packages/orchestrator/prompter.py`;
- `apps/api/tests/test_quality_loop.py`;
- при необходимости `apps/api/schemas.py`.

Минимальное поведение:

- debug показывает `planner`, `prompter`, `generator`;
- generator получает prompt от prompter, а не старый monolithic fallback prompt;
- response пока может оставаться без full validation, если deterministic validation ещё не подключена в этом slice;
- agentic stages имеют явный план для truncation guard, даже если сам guard добавляется следующим slice.

## 12. Operational Summary

Текущий контур:

```text
/generate -> GenerationService.generate -> build_lowcode_generator_prompt -> generate_from_prompt -> response_ready
```

Целевой следующий контур:

```text
/generate -> planner -> prompter -> generator -> response_ready
```

Целевой полный контур после следующих slices:

```text
/generate -> planner -> prompter -> generator -> deterministic_validation -> critic -> finalize
```

Repair ветка:

```text
critic -> prompter(repair) -> generator(repair) -> deterministic_validation -> critic
```

После 4 неудачных прогонов pipeline должен просить уточнение у пользователя, а не крутиться бесконечно.
