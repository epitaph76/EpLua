# How Validation Works

Этот документ фиксирует, как **фактически работает текущий pipeline валидации в коде** `luaMTS`, чтобы следующему агенту не приходилось заново искать entrypoint'ы, validator layers, repair loop и подтверждающие тесты.

Документ специально связывает:

- source-of-truth документы;
- реальные файлы реализации;
- ключевые функции;
- тесты, которые подтверждают поведение.

Если между документами и кодом есть расхождение, для понимания текущего поведения приоритет у кода.

## How Validation Should Work

Ниже зафиксирован **предпочтительный целевой принцип**, от которого стоит отталкиваться при следующих правках.

### Что должно приходить из CLI / API

CLI и API должны в первую очередь передавать **сырой пользовательский запрос** и **сырой контекст**, а не заранее принимать за пользователя семантические решения.

Нормально и полезно оставлять на deterministic-слое:

- разбор inline JSON;
- валидацию, что context действительно является JSON;
- чтение context из файла;
- хранение явно заданных пользователем `input_roots`, если он их сам указал;
- сужение JSON context по **явно заданным** roots;
- технические runtime options, mode, debug flags и transport-level поля.

### Что должен определять planner, а не CLI

Семантические поля должны быть ответственностью planner layer, а не chat-эвристик:

- `archetype`;
- `operation`;
- `expected_shape`;
- `edge_cases`;
- `task_intents`;
- `clarification_required`;
- `clarification_question`;
- при необходимости `output_mode`, если пользователь его явно не зафиксировал;
- `risk_tags`, если они описывают смысловую природу задачи, а не purely technical hints.

### Роль deterministic planner fallback

Deterministic planner fallback должен оставаться только структурным backstop:

- язык;
- валидность и парсинг context;
- явные roots из запроса;
- безопасные defaults;
- компактные facts для planner agent.

Он не должен незаметно подменять semantic planning и выдавать это за финальное понимание задачи.

### Правильная ответственность слоёв

Целевой порядок ответственности должен быть таким:

1. CLI / API собирает raw input.
2. Planner определяет semantic shape задачи.
3. Prompter строит prompt из `TaskSpec` и правил.
4. Generator создаёт candidate.
5. Validators проверяют candidate.
6. Critic решает `repair / clarification / finalize`.

Иными словами, validation pipeline должен зависеть от planner output, а не от CLI-эвристик, которые заранее разруливают смысл задачи.

## How Validation Works Now

### Что сейчас особенно важно помнить

Current implementation уже умеет много полезного:

- отдельные validator layers;
- runtime behavioral validation;
- semantic critic;
- repair loop;
- oscillation detection;
- deterministic repair tools.

Старые проблемы `1`-`7` про CLI / planner / prompter / truncation guard убраны из этого документа, потому что они уже закрыты в текущей рабочей ветке по словам владельца задачи.

## Current Problems And What Must Be Fixed

Ниже перечислены не просто наблюдения, а **конкретные проблемные места**, которые стоит считать кандидатами на исправление.

### Remaining Problem. Pre-validation cleanup после generator нужно пересмотреть

Слой:

- boundary между `generator` и validator pipeline

Связанный файл:

- `packages/orchestrator/repair_loop.py`

Проблемное место:

- `_prepare_candidate_for_validation(...)`

Current behavior:

- generator возвращает `raw_candidate`;
- затем `_prepare_candidate_for_validation(...)` превращает его в:
  - `candidate`;
  - `response_parts`;
- cleanup вырезает `<think>...</think>`, leading/trailing control tokens и auxiliary text;
- дальше validator pipeline получает уже не raw model output, а `visible_response`.

Почему это стоит пересмотреть:

- такой cleanup может скрывать реальное нарушение output contract;
- generator должен уметь возвращать валидный candidate сам, а не рассчитывать на "починку" между generation и validation;
- если model output содержит лишние control tokens, prose, think blocks или service text, это может быть validator finding, а не silent cleanup;
- текущий слой смешивает audit/debug extraction и semantic preparation candidate'а к validation.

Что нужно попробовать:

- убрать этот cleanup из основного happy path или сделать его намного более явным;
- передавать raw generator output напрямую в format validator, чтобы format layer видел фактический model output;
- оставить extraction of `response_parts` только для debug/audit, но не менять candidate молча;
- если cleanup всё-таки нужен, оформить его как отдельный `pre_validation_cleanup` stage с:
  - trace entry;
  - structured report;
  - статусом `pass / changed / fail`;
  - явным finding, если cleanup изменил model output.

Целевой принцип:

- validator должен проверять то, что модель реально вернула;
- любые автоматические изменения между generator и validator должны быть прозрачными, трассируемыми и тестируемыми.

### Рекомендуемое направление рефакторинга

Краткосрочный план пересборки:

1. Временно упростить рабочий контур до:
   - `planner`;
   - `prompter`;
   - `generator`.
2. Цель этого шага - руками протестировать, как реально работает связка agentic слоёв без старого validation / repair шума.
3. На этом этапе не чинить старый validation loop точечно:
   - не латать `pre-validation cleanup`;
   - не усложнять `repair_loop`;
   - не добавлять новые эвристики поверх старых эвристик;
   - не пытаться одновременно спасти текущие semantic/runtime validators.
4. После проверки простого `planner -> prompter -> generator` контура строить новый похожий validation pipeline с нуля:
   - отдельные явные stages;
   - понятные входы/выходы между stages;
   - trace/report для каждого stage;
   - без silent mutation candidate между generator и validator.

Текущий оставшийся фокус:

1. Pre-validation cleanup после generator нужно либо убрать из happy path, либо сделать явным stage с report.
2. Новый validation pipeline строить только после ручной проверки простого `planner -> prompter -> generator` контура.

## 1. Что считать source of truth

### Документы с целевой архитектурой

- `docs/validation_pipline.md`
  - главный target-state документ по новому validation / critic / repair pipeline;
  - описывает стадии `Stage 0` .. `Stage 9`;
  - полезен как архитектурное намерение.
- `docs/STATE_MACHINE.md`
  - описывает упрощённую state-machine версию pipeline;
  - полезен для понимания переходов `format_validation -> rule_validation -> critic_step -> repair_generation/finalize/clarification`.
- `docs/AGENT_ARCHITECTURE.md`
  - фиксирует роли компонентов: `orchestrator`, `generation layer`, `format validator`, `rule validator`, `critic`, `finalizer`.
- `docs/AGENT_PIPELINE_SEQUENCE.md`
  - sequence-диаграмма того же контура.
- `README.md`
  - высокоуровневое описание того, что validation layer, critic и repair loop уже существуют в рабочем контуре.

### Файлы, которые описывают actual implementation

- `apps/api/routes/generate.py`
- `apps/api/services/generation.py`
- `packages/orchestrator/domain_adapter.py`
- `packages/orchestrator/planner.py`
- `packages/orchestrator/task_spec.py`
- `packages/orchestrator/prompter.py`
- `packages/orchestrator/repair_loop.py`
- `packages/orchestrator/critic.py`
- `packages/validators/core.py`
- `packages/shared/quality.py`
- `apps/api/schemas.py`

### Файлы, которые подтверждают поведение тестами

- `apps/api/tests/test_quality_loop.py`
- `apps/api/tests/test_repair_metrics.py`

## 2. Быстрая навигация

Если нужно понять что-то конкретное, открывай сразу эти файлы:

| Что нужно понять | Где смотреть |
| --- | --- |
| HTTP entrypoint запроса | `apps/api/routes/generate.py` |
| Когда validation вообще запускается | `apps/api/services/generation.py` |
| Как строится prompt package | `packages/orchestrator/domain_adapter.py` |
| Как определяется `TaskSpec` | `packages/orchestrator/task_spec.py`, `packages/orchestrator/planner.py` |
| Как работает основной цикл generation/validation/repair | `packages/orchestrator/repair_loop.py` |
| Какие слои валидации реально существуют | `packages/validators/core.py` |
| Как выбирается `repair / clarification / finalize` | `packages/orchestrator/critic.py` |
| Какие структуры данных идут между слоями | `packages/shared/quality.py` |
| Что именно возвращает API | `apps/api/schemas.py`, `packages/shared/quality.py` |
| Какие сценарии уже защищены тестами | `apps/api/tests/test_quality_loop.py` |

## 3. End-to-end путь запроса

Текущий путь запроса выглядит так:

1. `POST /generate` приходит в `apps/api/routes/generate.py`.
2. Роут вызывает `GenerationService.generate(...)`.
3. Если в запросе есть и `archetype`, и `output_mode`, сервис собирает `DomainPromptPackage`.
4. После этого сервис запускает `run_quality_loop(...)`.
5. Quality loop:
   - делает generation;
   - прогоняет candidate через validation layers;
   - при необходимости запускает semantic critic;
   - собирает `ValidationBundle`;
   - либо финализирует результат;
   - либо запускает repair;
   - либо просит clarification;
   - либо завершает bounded failure.
6. Итог возвращается как `QualityOutcome.to_dict()`.

Если `archetype` и `output_mode` не переданы одновременно, quality loop **не запускается**. В этом случае сервис просто вызывает модель и возвращает:

- `validation_status = "not_run"`
- `stop_reason = "not_run"`

Это поведение живёт в `apps/api/services/generation.py`, метод `GenerationService.generate`.

## 4. Entry point слой

### `apps/api/routes/generate.py`

Роль файла:

- FastAPI entrypoint;
- принимает `GenerateRequest`;
- вызывает `GenerationService`;
- валидирует ответ как `GenerateResponse`;
- логирует `generate_requested` и `generate_completed`.

Ключевая функция:

- `generate(...)`

### `apps/api/schemas.py`

Роль файла:

- фиксирует API-контракты запроса и ответа.

Что важно:

- `GenerateRequest` содержит:
  - `task_text`
  - `provided_context`
  - `archetype`
  - `output_mode`
  - `input_roots`
  - `risk_tags`
  - `debug`
  - `mode`
  - `model`
  - `runtime_options`
  - `allow_cloud_model`
  - `language`
- `GenerateResponse` содержит:
  - `code`
  - `validation_status`
  - `stop_reason`
  - `trace`
  - `validator_report`
  - `critic_report`
  - `repair_count`
  - `clarification_count`
  - `output_mode`
  - `archetype`
  - `debug`

### `apps/api/services/generation.py`

Роль файла:

- главный service-layer entrypoint из API в orchestration code.

Ключевые вещи:

- `GenerationService.generate(...)`
  - выбирает model adapter через `_adapter_for_request(...)`;
  - если есть `archetype` и `output_mode`, строит `DomainPromptPackage`;
  - затем вызывает `run_quality_loop(...)`;
  - иначе идёт по упрощённой ветке без validation.

Это главный переключатель между:

- `simple generation without validation`
- `full quality loop`

## 5. Как строится prompt package

### `packages/orchestrator/domain_adapter.py`

Ключевая функция:

- `build_domain_prompt_package(...)`

Она делает следующее:

1. Загружает archetype registry и prompt templates.
2. Проверяет, что:
   - archetype существует;
   - output mode разрешён;
   - output mode совместим с archetype.
3. Вызывает planner:
   - сначала deterministic structural planner;
   - затем, если adapter умеет `generate_from_agent`, planner может быть дополнен agent-response.
4. Вызывает prompter:
   - сначала локальный deterministic prompt builder;
   - затем, если adapter умеет `generate_from_agent`, prompter может вернуть компактный patch к prompt.
5. Собирает `DomainPromptPackage`.

`DomainPromptPackage` содержит критически важные поля:

- `prompt`
- `agent_prompt`
- `archetype`
- `output_mode`
- `expected_result_format`
- `allowed_data_roots`
- `forbidden_patterns`
- `risk_tags`
- `task_intents`
- `clarification_required`
- `task_spec`
- `execution_context`
- `planner_result`
- `prompt_builder_result`
- `agent_layer_calls`
- `language`

Также в этом файле есть `normalize_model_output(...)`, который нормализует ответ модели по mode:

- `raw_lua`
- `json_wrapper`
- `patch_mode`
- `clarification`

### `packages/orchestrator/planner.py`

Роль файла:

- planner layer;
- структурирует задачу до генерации кода.

Ключевые функции:

- `plan_task(...)`
- `build_planner_agent_prompt(...)`
- `apply_planner_agent_response(...)`

Что важно про current behavior:

- базовый deterministic planner сейчас в первую очередь делает **structural plan**:
  - нормализует язык;
  - вытаскивает `input_roots`;
  - парсит `execution_context` из JSON;
  - создаёт начальный `TaskSpec` с `operation="unresolved"` и `expected_shape="unknown"`.
- если есть `planner agent`, он может дообогатить:
  - `operation`
  - `output_mode`
  - `input_roots`
  - `expected_shape`
  - `risk_tags`
  - `edge_cases`
  - `task_intents`
  - `clarification_required`
  - `clarification_question`

Planner также умеет:

- падать обратно в deterministic fallback;
- частично восстанавливать truncated JSON ответ planner agent.

### `packages/orchestrator/task_spec.py`

Роль файла:

- хранит `TaskSpec`;
- строит семантическое описание задачи для следующих слоёв.

Ключевые вещи:

- `TaskSpec` содержит:
  - `task_text`
  - `language`
  - `archetype`
  - `operation`
  - `output_mode`
  - `input_roots`
  - `expected_shape`
  - `risk_tags`
  - `edge_cases`
  - `clarification_required`
  - `clarification_question`
- `build_task_spec(...)` умеет вычислять:
  - `operation`
  - `expected_shape`
  - `edge_cases`
  - default clarification question

### `packages/orchestrator/prompter.py`

Роль файла:

- строит generator prompt;
- строит repair-prompts для следующих итераций;
- принимает compact JSON patch от prompter agent.

Ключевые функции:

- `build_prompt_package_for_generation(...)`
- `build_prompter_agent_prompt(...)`
- `build_repair_prompter_agent_prompt(...)`
- `apply_prompter_agent_response(...)`

Важно:

- локальный fallback prompt собирается детерминированно;
- retrieval сейчас фактически выключен:
  - `RetrievalPack(examples=tuple(), archetype_template=None, format_rules=None)`
- prompter agent не должен возвращать полный prompt, а только короткие `sys` / `user` additions.

## 6. Где живёт основной pipeline

### `packages/orchestrator/repair_loop.py`

Главная функция:

- `run_quality_loop(...)`

Это центральный orchestrator текущего pipeline.

### Полный фактический цикл одной попытки

Ниже описан current behavior в точном порядке.

#### Stage A. Generation

`run_quality_loop(...)` сначала:

- вызывает generator через `_generate_from_agent(...)`;
- получает `raw_candidate`;
- очищает служебные части ответа через `_prepare_candidate_for_validation(...)`.

#### Stage B. Pre-validation normalization

Перед запуском валидаторов применяется `_normalize_candidate_for_validation(...)`.

Что он делает:

- если response целиком обёрнут во внешний markdown fence, снимает fence;
- это ранняя нормализация до format validator.

#### Stage C. Format + rule pipeline

На каждой итерации вызывается `run_validation_pipeline(...)` из `packages/validators/core.py`.

Он возвращает:

- `normalized_candidate`
- `format_report`
- `syntax_report`
- `static_report`
- `principle_report`
- `rule_report`

Фактически `rule_report` у вас является merged-report из трёх слоёв:

- syntax
- static
- principle

#### Stage D. Runtime validation

После `run_validation_pipeline(...)` orchestrator отдельно решает, запускать ли runtime validation.

Это происходит только если:

- `format_report.status == "pass"`
- `rule_report` не блокирует behavioral validation
- есть поддерживаемый `TaskSpec`
- `output_mode != clarification`

Для этого используются:

- `_runtime_task_spec_for_validation(...)`
- `_runtime_supported_task_spec(...)`
- `_runtime_backstop_task_spec(...)`
- `validate_runtime_behavior(...)`

Важно:

- сейчас runtime validation реально поддержан узко;
- основная рабочая зона сейчас: `simple_extraction` с:
  - `last_array_item`
  - `first_array_item`
- есть runtime backstop, который может восстановить usable `TaskSpec`, если planner оставил `operation="unresolved"`, но из задачи и risk tags уже понятно, что это array item extraction.

#### Stage E. Semantic validation

Semantic critic запускается только если:

- format pass;
- rule-layer либо pass, либо только soft-fail;
- runtime не дал hard-fail;
- mode не `clarification`.

Для этого используются:

- `build_semantic_critic_agent_prompt(...)`
- `_generate_from_agent(...)`
- `parse_semantic_critic_response(...)`
- `_apply_semantic_false_positive_overrides(...)`

Очень важно:

- если runtime validation уже упал, semantic critic обычно **не запускается**;
- если semantic critic вернул мусор, это не silent pass, а `semantic_critic_invalid_response`.

#### Stage F. ValidationBundle

После всех reports orchestrator собирает `ValidationBundle` через `_build_validation_bundle(...)`.

Он содержит:

- `task_spec`
- `current_candidate`
- `format_report`
- `syntax_report`
- `static_report`
- `principle_report`
- `runtime_report`
- `semantic_report`
- `final_failure_classes`
- `repair_priority`
- `behavioral_fingerprint`
- `invalid_shape_signature`
- `disallowed_root_signature`

Эта структура нужна для:

- выбора primary failure;
- repair-priority;
- oscillation detection;
- компактного repair prompt;
- debug trail.

#### Stage G. Validation gate

Окончательное решение “можно ли финализировать candidate как валидный” принимает `_validation_gate_passed(...)`.

Логика на высоком уровне такая:

- для `clarification` достаточно не провалить rule-layer;
- для обычных mode'ов:
  - если rule-layer pass, то:
    - `runtime=pass` и semantic не возражает -> pass;
    - `runtime=skipped` и semantic=pass -> pass;
    - для `json_wrapper/patch_mode` есть отдельная поблажка при `semantic_critic_invalid_response`;
  - если rule-layer не pass, но это только soft-fail и semantic pass -> тоже может пройти.

#### Stage H. Critic step

Если validation gate не пройден, orchestrator вызывает `build_critic_report(...)`.

Критик получает:

- все validator reports;
- `repair_count`;
- `clarification_count`;
- флаг repeated failure;
- флаг oscillation;
- `task_intents`;
- язык;
- `ValidationBundle`.

Результат critic step:

- `repair`
- `clarification`
- `finalize`

#### Stage I. Deterministic repair tools

До нового model-call loop пытается сделать auto-fix через `_try_repair_with_tool(...)`.

Сейчас там есть детерминированные repair-функции:

- `_repair_missing_array_allocator(...)`
- `_repair_patch_path_keys(...)`
- `_repair_invalid_json_mode(...)`

То есть часть ошибок чинится **без LLM**.

#### Stage J. Repair via prompter + generator

Если deterministic repair не подошёл:

1. строится repair-prompt через `_build_repair_prompt_via_prompter(...)`;
2. там используется `build_repair_prompter_agent_prompt(...)`;
3. затем generator делает новую итерацию;
4. цикл начинается заново.

#### Stage K. Clarification / bounded stop

Если repair больше не нужен или невозможен:

- pipeline может вернуть clarification question;
- либо завершиться bounded failure;
- либо завершиться статусом `validator_conflict`.

## 7. Слои валидации по файлам и функциям

### `packages/validators/core.py`

Это главный файл validator implementation.

### 7.1. `run_validation_pipeline(...)`

Оркестрирует слои:

1. `validate_format(...)`
2. `validate_syntax(...)`
3. `validate_static(...)`
4. `validate_principles(...)`
5. merge в `rule_report`

Если format уже fail:

- syntax/static/principle получают `skipped`;
- `rule_report` тоже становится `skipped`.

### 7.2. `validate_format(...)`

Проверяет outer contract по `output_mode`.

Для `raw_lua` ловит:

- markdown fences;
- JSON object вместо Lua;
- пустой output;
- prose перед кодом.

Для `json_wrapper` и `patch_mode` ловит:

- markdown fences;
- invalid JSON;
- JSON не-object;
- string leaves без `lua{...}lua`.

Для `clarification` требует:

- plain text;
- наличие вопросительного знака.

### 7.3. `validate_syntax(...)`

Проверяет синтаксический слой Lua.

Порядок:

1. пытается прогнать `stylua`;
2. если tool недоступен, остаётся на внутренних синтаксических проверках.

Внутренние проверки:

- unbalanced parentheses;
- unexpected `end`;
- unbalanced block delimiters.

### 7.4. `validate_static(...)`

Проверяет статические ограничения.

Внутри использует:

- `_validate_paths(...)`
- `_validate_forbidden_patterns(...)`
- `_run_luacheck(...)`

Что он реально ловит:

- `disallowed_data_root`
- `mixed_root_families`
- `jsonpath_usage`
- `invented_data_root`
- `markdown_fence`
- `forbidden_pattern`
- `luacheck_failed`

### 7.5. `validate_principles(...)`

Проверяет более доменные и archetype-specific правила.

Внутри использует:

- `_validate_archetype_specific(...)`
- `_validate_task_spec_shape(...)`

Ключевой пример:

- для `simple_extraction` может поймать `array_item_returns_whole_array`, если задача просила один элемент массива, а candidate возвращает весь массив.

### 7.6. `validate_runtime_behavior(...)`

Это реальный runtime behavioral validator.

Сейчас он:

- не работает для `clarification`;
- пропускается без `execution_context`;
- пропускается для неподдерживаемых archetype/operation;
- поддерживает в основном:
  - `simple_extraction`
  - `last_array_item`
  - `first_array_item`

Как он работает:

1. строит runtime fixtures через `_build_simple_extraction_runtime_fixtures(...)`;
2. достаёт Lua segment;
3. запускает snippet через локальный Lua бинарник;
4. сравнивает `expected` и `actual`;
5. отдаёт:
   - `pass`
   - `runtime_execution_failed`
   - `runtime_behavior_mismatch`

Ключевые helper'ы:

- `_build_simple_extraction_runtime_fixtures(...)`
- `_expected_simple_extraction_result(...)`
- `_resolve_context_path(...)`
- `_clone_context_with_replaced_root(...)`
- `_execute_runtime_candidate(...)`
- `_build_runtime_script(...)`
- `_decode_runtime_result(...)`
- `_build_runtime_metadata(...)`
- `_runtime_values_match(...)`

### 7.7. Внешние инструменты validator layer

`packages/validators/core.py` ищет и использует локальные тулзы:

- `stylua`
- `luacheck`
- `lua`

Связанные файлы и артефакты:

- `.stylua.toml`
- `.luacheckrc`
- `tools/stylua/stylua.exe`
- `tools/lua_modules/bin/luacheck`
- `tools/lua/bin/lua.exe`
- `tools/lua_modules/share/lua/5.4`
- `tools/lua_modules/lib/lua/5.4`
- `tools/mingw/mingw64/bin`

Если tool недоступен или падает инфраструктурно:

- validator не всегда делает hard fail;
- часть проверок может перейти в `skipped` с metadata.

## 8. Critic и stop policy

### `packages/orchestrator/critic.py`

Это не validator, а decision layer поверх validator outputs.

Ключевые функции:

- `build_critic_report(...)`
- `build_semantic_critic_agent_prompt(...)`
- `parse_semantic_critic_response(...)`

### 8.1. Что делает `build_critic_report(...)`

Он:

1. собирает все findings;
2. выбирает primary finding;
3. проверяет special cases:
   - validator conflict;
   - ambiguity;
   - oscillation;
   - repair budget exhaustion;
   - non-repairable issue;
4. выбирает action:
   - `repair`
   - `clarification`
   - `finalize`

### 8.2. Как выбирается primary finding

Используется:

- `_collect_findings(...)`
- `_select_primary_finding(...)`

Если есть `ValidationBundle.repair_priority`, finding выбирается не просто первый, а по repair priority.

### 8.3. Конфликт валидаторов

Специальный кейс:

- `_has_validator_conflict(...)`
- `_semantic_priority_conflict_resolution(...)`

Смысл:

- если pattern-based principle validator и semantic critic спорят, critic может:
  - либо предпочесть semantic intent;
  - либо вернуть `validator_conflict`.

### 8.4. Repair prompts

`_build_repair_instructions(...)` строит локализованный repair prompt для разных failure classes.

Есть special handling для:

- `semantic_mismatch`
- `runtime_behavior_mismatch`
- `markdown_fence`
- `invalid_wrapper`
- `disallowed_data_root`
- `mixed_root_families`
- `missing_*`
- `empty_*`

Для runtime mismatch используется `_runtime_behavior_repair_prompt(...)`, который вставляет:

- operation;
- expected shape;
- failed fixture;
- expected vs actual.

### 8.5. Semantic critic protocol

`build_semantic_critic_agent_prompt(...)` даёт semantic critic:

- compact task summary;
- current candidate;
- compact validator summary.

`parse_semantic_critic_response(...)` ожидает minified JSON и умеет чистить:

- `<think>...</think>`
- control tokens
- fenced JSON/Lua

Если JSON невалидный, возвращается failure:

- `semantic_critic_invalid_response`

## 9. Repair loop policy

### `packages/orchestrator/repair_loop.py`

Ключевые policy-части:

- `_validation_gate_passed(...)`
- `_detect_repair_oscillation(...)`
- `_score_candidate(...)`
- `_stop_reason_for_finalize(...)`

### 9.1. Oscillation detection

Pipeline считает, что repair loop зациклился, если повторяется одно из:

- fingerprint самого candidate;
- `behavioral_fingerprint` runtime report;
- `invalid_shape_signature`;
- `disallowed_root_signature`.

Это важное отличие от простого сравнения текста candidate: цикл может быть признан oscillating даже при формально разном коде, если повторяется то же runtime behavior или та же structural ошибка.

### 9.2. Best candidate fallback

Loop не просто хранит текущий candidate. Он ещё и скорит кандидатов через `_score_candidate(...)` и запоминает best candidate.

Это нужно, чтобы при bounded finalize вернуть не самый последний плохой вариант, а лучший из уже увиденных.

### 9.3. Финальные stop reason'ы

Через `QualityOutcome` наружу могут выйти, как минимум, такие итоговые состояния:

- `passed`
- `repaired`
- `clarification_requested`
- `validator_conflict`
- `bounded_failure`
- `not_run`

А `stop_reason` может быть, например:

- `passed`
- `clarification_requested`
- `repair_exhausted`
- `oscillation_detected`
- `validator_conflict`
- `not_run`

## 10. Data contracts между слоями

### `packages/shared/quality.py`

Это файл с основными dataclass-контрактами pipeline.

Ключевые структуры:

- `ValidationFinding`
- `ValidatorReport`
- `ValidationSnapshot`
- `ValidationSummary`
- `ValidationBundle`
- `QualityOutcome`

Именно они определяют:

- что такое finding;
- как выглядит report любого validator'а;
- как выглядят iteration snapshots;
- что уходит в `validator_report`;
- что возвращает orchestrator наружу.

Если нужно менять shape debug/validator payload, начинать нужно отсюда и из `repair_loop.py`.

## 11. Debug trail

Debug payload собирается в `packages/orchestrator/repair_loop.py`, helper:

- `_build_debug_payload(...)`

В debug сейчас попадают:

- `prompt_package`
- `pipeline_layers`
- `agent_layer_calls`
- `model_calls`
- `validation_passes`

Это лучший способ быстро понять фактический проход конкретного запроса.

Если нужно расследовать “почему candidate прошёл/не прошёл”, открывай именно debug trail.

## 12. Что именно подтверждено тестами

Основной файл:

- `apps/api/tests/test_quality_loop.py`

Ниже список особенно полезных тестов и что они доказывают.

### Вход и happy path

- `test_generation_service_auto_normalizes_fenced_raw_lua_before_repair_budget`
  - показывает, что внешний fence может быть снят до сжигания repair budget.
- `test_generation_service_runs_runtime_validation_before_semantic_for_simple_extraction`
  - подтверждает порядок:
    - `generation`
    - `format_validation`
    - `rule_validation`
    - `runtime_validation`
    - `semantic_validation`
    - `finalize`

### Runtime validator как реальный gate

- `test_generation_service_runtime_blocks_wrong_candidate_after_truncated_planner_json`
  - показывает, что runtime fail блокирует candidate;
  - semantic critic на первой итерации при этом skip.

### Deterministic repair tools

- `test_generation_service_repairs_invalid_json_patch_mode_with_tool`
- `test_generation_service_repairs_string_only_patch_object_with_tool`
- `test_generation_service_repairs_fragment_only_patch_object_with_tool`
- `test_generation_service_repairs_patch_mode_path_keys_with_tool`
- `test_generation_service_repairs_nested_full_rewrite_patch_payload_with_tool`

Все эти тесты подтверждают, что часть repair'ов делается через `repair_source = "deterministic_tool"`.

### Clarification и bounded stop

- `test_generation_service_returns_clarification_output_when_planner_marks_request_ambiguous`
  - показывает, что planner может сразу перевести pipeline в clarification mode.
- `test_generation_service_asks_for_feedback_after_repeated_invalid_shape_after_repair`
- `test_generation_service_asks_for_feedback_when_candidate_returns_to_previous_shape`
- `test_generation_service_asks_for_feedback_when_runtime_behavior_repeats`
- `test_generation_service_asks_for_feedback_after_three_repair_attempts_with_repeated_shape`
- `test_generation_service_asks_for_feedback_after_four_non_oscillating_repairs`

Эти тесты подтверждают:

- oscillation detection;
- clarification after repeated failure;
- bounded repair budget.

### Validator conflict

- `test_generation_service_prefers_semantic_intent_for_clear_field_conflict`
  - показывает разрешимый конфликт, где semantic intent выигрывает.
- `test_generation_service_returns_validator_conflict_for_unresolved_pattern_vs_semantic_disagreement`
  - показывает неразрешимый конфликт со статусом `validator_conflict`.

### Regression / uplift

- `apps/api/tests/test_repair_metrics.py`
  - не описывает сам pipeline по шагам, но подтверждает, что repair loop в aggregate даёт не хуже baseline и должен улучшать baseline success rate.

## 13. Практическая карта “что менять, если сломалось”

### Если сломался outer shape ответа

Смотри:

- `packages/validators/core.py`
  - `validate_format(...)`
- `packages/orchestrator/domain_adapter.py`
  - `normalize_model_output(...)`

### Если сломан выбор roots / shape / operation

Смотри:

- `packages/orchestrator/planner.py`
- `packages/orchestrator/task_spec.py`
- `packages/orchestrator/domain_adapter.py`

### Если candidate проходит format, но должен падать по domain rules

Смотри:

- `packages/validators/core.py`
  - `validate_static(...)`
  - `validate_principles(...)`
  - `_validate_paths(...)`
  - `_validate_forbidden_patterns(...)`
  - `_validate_task_spec_shape(...)`

### Если candidate должен валиться по фактическому поведению, а не валится

Смотри:

- `packages/validators/core.py`
  - `validate_runtime_behavior(...)`
  - `_build_simple_extraction_runtime_fixtures(...)`
  - `_execute_runtime_candidate(...)`
- `packages/orchestrator/repair_loop.py`
  - `_runtime_task_spec_for_validation(...)`
  - `_validation_gate_passed(...)`

### Если semantic critic делает странные решения

Смотри:

- `packages/orchestrator/critic.py`
  - `build_semantic_critic_agent_prompt(...)`
  - `parse_semantic_critic_response(...)`
  - `_apply_semantic_false_positive_overrides(...)` в `repair_loop.py`

### Если repair loop крутится не туда

Смотри:

- `packages/orchestrator/critic.py`
  - `build_critic_report(...)`
  - `_build_repair_instructions(...)`
- `packages/orchestrator/repair_loop.py`
  - `_detect_repair_oscillation(...)`
  - `_try_repair_with_tool(...)`
  - `_build_repair_prompt_via_prompter(...)`
  - `_score_candidate(...)`
  - `_stop_reason_for_finalize(...)`

## 14. Главное расхождение между docs и current code

Самое важное, что нужно помнить следующему агенту:

- `docs/validation_pipline.md` описывает **целевую** архитектуру;
- current code уже реализует значительную часть этой схемы, но не буквально 1:1;
- в actual code pipeline сейчас особенно важны дополнительные детали, которых полезно не упустить:
  - `syntax/static/principle` как отдельные подслои rule validation;
  - runtime behavioral validator как отдельный шаг;
  - `ValidationBundle`;
  - oscillation detection по нескольким fingerprint'ам;
  - deterministic repair tools до нового model call;
  - semantic false-positive overrides;
  - best-candidate fallback при bounded finalize.

## 15. Самый короткий operational summary

Если нужен один абзац вместо всего документа:

`/generate` -> `GenerationService.generate` -> `build_domain_prompt_package` -> `run_quality_loop` -> `run_validation_pipeline(format + syntax + static + principle)` -> при возможности `runtime_validation` -> затем `semantic_validation` -> `ValidationBundle` -> `critic_step` -> либо `finalize`, либо deterministic repair, либо repair через prompter/generator, либо clarification, либо bounded failure / validator conflict.
