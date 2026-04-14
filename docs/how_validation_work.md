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

Сейчас система работает гибридно: часть семантических решений действительно принимает LLM planner, но часть значимых полей уже заранее заполняется до planner.

### Что сейчас делает chat CLI до backend

В `apps/api/cli/main.py` chat path сейчас:

- режет строку на `task_text` и `inline_context`;
- выводит `input_roots` через `_effective_input_roots(...)`;
- сужает context через `_narrow_json_context(...)`;
- добавляет `risk_tags` через `_infer_risk_tags(...)`;
- выбирает `archetype` через `_infer_chat_archetype(...)`;
- жёстко ставит `output_mode="raw_lua"`.

Это означает, что planner получает уже частично предразмеченную задачу.

### Что сейчас делает planner

Planner в `packages/orchestrator/planner.py` получает:

- `task_text`;
- `provided_context`;
- `language`;
- уже выбранный `archetype`;
- уже выбранный `output_mode`;
- `input_roots`;
- `risk_tags`.

Deterministic planner при этом пока mostly structural:

- нормализует язык;
- нормализует roots;
- парсит `execution_context`;
- создаёт стартовый `TaskSpec` с `operation="unresolved"` и `expected_shape="unknown"`.

Потом planner agent может дообогатить этот skeleton, но он уже работает не с полностью raw input, а с partially pre-classified request.

### Что сейчас особенно важно помнить

Current implementation уже умеет много полезного:

- отдельные validator layers;
- runtime behavioral validation;
- semantic critic;
- repair loop;
- oscillation detection;
- deterministic repair tools.

Но при этом текущий chat entry path всё ещё содержит архитектурный debt: часть semantic classification происходит раньше planner.

## Current Problems And What Must Be Fixed

Ниже перечислены не просто наблюдения, а **конкретные проблемные места**, которые стоит считать кандидатами на исправление.

### Problem 1. Chat CLI слишком рано выбирает `archetype`

Файл:

- `apps/api/cli/main.py`

Проблемное место:

- `_infer_chat_archetype(...)`

Почему это плохо:

- это semantic classification;
- оно делается эвристикой по ключевым словам;
- planner получает уже навязанный `archetype`;
- из-за этого planner перестаёт быть единственным владельцем semantic routing.

Что нужно исправить:

- убрать автоматическое назначение `archetype` из chat CLI;
- либо оставить его только как `planner_hint`, а не как final field;
- финальный `archetype` должен выставлять planner.

### Problem 2. Chat CLI жёстко ставит `output_mode="raw_lua"`

Файл:

- `apps/api/cli/main.py`

Проблемное место:

- при сборке `task_args` в chat path `output_mode` задаётся жёстко как `raw_lua`.

Почему это плохо:

- mode-selection превращается в transport-level константу;
- planner не может полноценно решать, нужен ли другой mode;
- система искусственно зажата в один response shape ещё до semantic planning.

Что нужно исправить:

- перестать жёстко ставить `output_mode` в chat path;
- либо передавать его только как optional hint/default;
- final mode должен определяться planner/mode-selection logic, если пользователь явно не задал его сам.

### Problem 3. `risk_tags` сейчас частично выводятся тупой локальной эвристикой

Файл:

- `apps/api/cli/main.py`

Проблемное место:

- `_infer_risk_tags(...)`

Current behavior:

- если есть `input_roots` и в `task_text` встречается `послед`, `перв`, `last` или `first`,
- CLI автоматически добавляет:
  - `array_indexing`
  - `empty_array`

Почему это плохо:

- это не полноценное semantic understanding;
- это brittle keyword heuristic;
- risk tags могут влиять на planner hints, prompt building и runtime backstop;
- часть reasoning оказывается в CLI вместо planner.

Что нужно исправить:

- semantic `risk_tags` должны определяться planner;
- если какие-то risk tags нужны как deterministic hints, их надо явно разделить на:
  - `transport_hints`
  - `planner_output`
- текущую эвристику нельзя считать финальным источником truth.

### Problem 4. Quality loop зависит от того, что `archetype` и `output_mode` уже заполнены заранее

Файл:

- `apps/api/services/generation.py`

Проблемное место:

- `GenerationService.generate(...)` запускает `build_domain_prompt_package(...)` и `run_quality_loop(...)` только если уже есть и `archetype`, и `output_mode`.

Почему это плохо:

- planner-first architecture получается неполной;
- без заранее заполненных полей система не входит в полноценный domain-adapted path;
- часть product path вынужденно переносит semantic decisions вверх, в CLI.

Что нужно исправить:

- сделать planner-first вход в quality loop;
- разрешить backend самому определять `archetype` и `output_mode`, если пользователь их явно не указал;
- сохранить explicit override только для случая, когда пользователь или тест сознательно фиксирует эти поля.

### Problem 5. `input_roots` не так токсичны, как `archetype` и `risk_tags`, но их роль надо прояснить

Файлы:

- `apps/api/cli/main.py`
- `packages/orchestrator/planner.py`

Важно не перепутать:

- deterministic extraction of roots из JSON context сама по себе нормальна;
- это ближе к structural parsing, чем к semantic reasoning.

Но здесь тоже нужен аккуратный контракт:

- если roots определены пользователем явно, это source of truth;
- если roots только эвристически выведены из context, planner должен видеть это как inferred evidence, а не как unquestionable final truth.

Что нужно исправить:

- развести понятия:
  - `explicit_input_roots`
- `inferred_input_roots`
- planner должен понимать, какие roots пользователь задал явно, а какие были только выведены автоматически.

### Problem 6. Generator prompt нельзя "оптимизировать" за счёт потери user task и релевантного context

Слой:

- `generation`

Связанные файлы:

- `packages/orchestrator/prompter.py`
- `packages/orchestrator/domain_adapter.py`

Проблема:

- при обсуждении сокращения prompt нельзя скатиться в ложную оптимизацию, где из generator prompt убираются:
  - raw user task;
  - релевантный context;
  - явное expected behavior.

Почему это важно:

- user prompt несёт фактическую постановку задачи;
- context несёт данные и структуру, по которым generator должен строить код;
- слишком агрессивное "сжатие" prompt может сделать его короче, но одновременно менее понятным и менее управляемым для модели;
- generation layer должен получать не просто короткий prompt, а **короткий и содержательный prompt**.

Что должно быть реализовано:

- generator prompt должен сохранять:
  - raw `task_text` пользователя;
  - релевантный `provided_context` или его корректно narrowed version;
  - явное expected behavior;
  - allowed roots;
  - output mode contract;
  - короткие anti-pattern instructions.
- сокращать нужно:
  - лишние policy blocks;
  - дублирующие секции;
  - служебные описания, не помогающие генерации;
  - внутреннюю сериализацию состояния, которую generator не обязан "понимать" как протокол.

Практический принцип:

- не "самый короткий prompt любой ценой";
- а "минимальный prompt, который остаётся однозначным для generator".

### Problem 7. На каждом agentic слое нужен guard на обрезанный output по лимиту `num_predict`

Слои:

- `planner`
- `prompter`
- `semantic_critic`
- другие agentic sub-steps, если они добавятся

Связанные файлы:

- `apps/api/adapters/model.py`
- `packages/orchestrator/planner.py`
- `packages/orchestrator/prompter.py`
- `packages/orchestrator/critic.py`

Проблема:

- если agentic layer вернул ответ ровно в лимит `num_predict` (например, ровно `256` токенов),
- это сильный сигнал, что ответ мог быть обрезан по budget limit, а не завершён естественным образом;
- сейчас такой случай не выделен как отдельный protocol-level quality gate.

Почему это важно:

- truncated planner output может ломать `TaskSpec`;
- truncated prompter output может давать неполный patch prompt;
- truncated semantic critic output может притворяться валидным reasoning, хотя по факту ответ был насильно оборван;
- без явного guard'а pipeline может продолжить работу на частично повреждённых фактах.

Что должно быть реализовано:

- у каждого agentic слоя должна быть post-check валидация ответа;
- если ответ упёрся ровно в `num_predict`, слой должен считать это подозрением на truncation;
- такой ответ не должен молча считаться нормальным успехом;
- слой должен:
  - либо сделать bounded retry;
  - либо повторить вызов с явной инструкцией "output was too long, compress and return only the minimal valid schema";
  - либо перейти в fallback/clarification path, если повтор тоже неуспешен.

Минимальная практическая политика:

- exact-hit на `num_predict` считать suspicious;
- повторный вызов делать с более жёстким требованием краткости;
- для structured outputs дополнительно валидировать:
  - schema completeness;
  - отсутствие оборванного JSON;
  - наличие всех обязательных полей.

### Рекомендуемое направление рефакторинга

Минимально системное направление такое:

1. CLI передаёт raw task и raw context.
2. CLI может передавать только:
   - explicit user overrides;
   - optional hints;
   - transport/runtime flags.
3. Backend planner становится owner для:
   - `archetype`
   - `operation`
   - `expected_shape`
   - semantic `risk_tags`
   - `clarification_required`
   - `output_mode`, если он явно не зафиксирован пользователем.
4. Generator prompt должен быть коротким, но сохранять raw task и релевантный context.
5. На agentic слоях должен появиться truncation guard при упоре в `num_predict`.
6. Quality loop должен уметь стартовать без заранее заполненных `archetype/output_mode`.
7. Все старые CLI-эвристики, если они вообще сохраняются, должны быть понижены до hints и не притворяться final semantic classification.

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
