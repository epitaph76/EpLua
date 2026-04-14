# Validation Pipeline

Этот документ фиксирует целевой пайплайн валидации для `luaMTS` и служит рабочим source of truth для следующего агента, который будет перепроектировать validation / critic / repair loop.

Имя файла оставлено в форме `validation_pipline.md` намеренно, чтобы совпадать с пользовательским запросом.

## 1. Зачем нужен новый пайплайн

Текущий quality loop уже умеет:

- форматные проверки;
- проверки запрещённых паттернов;
- path checks;
- `stylua` / `luacheck`;
- archetype-specific checks;
- critic / repair / clarification loop.

Но для части простых задач система всё ещё ведёт себя хуже, чем сильная модель в идеальных условиях:

- модель может сгенерировать правильное решение;
- но repair loop может зациклиться;
- semantic critic может увести систему в `bounded_failure`;
- простые extraction-кейсы проверяются слишком "словесно", а не по фактическому поведению кода.

Ключевая цель нового пайплайна:

> перевести validation в режим `deterministic-first`, где semantic critic остаётся в системе, но не является единственным и главным арбитром там, где поведение можно проверить по факту.

## 2. Главный принцип

Приоритет слоёв должен быть таким:

1. input normalization подготавливает структурный fallback;
2. `planner agent` формализует смысл задачи;
3. `prompter agent` собирает задание для generator;
4. `generator agent` генерирует код;
5. deterministic validators проверяют контракт;
6. runtime validators проверяют поведение на данных;
7. `semantic_critic agent` помогает там, где deterministic checks не дают полного ответа;
8. critic / stop policy решает: pass, clarification или новый круг;
9. repair-итерация возвращает ошибки в `prompter agent`, а не вызывает отдельный repair LLM-agent.

Практическое правило:

> если поведение кандидата можно проверить исполнением на fixture-контексте, deterministic runtime validation имеет больший вес, чем semantic critic.

Если `planner agent` не вернул валидный план и `TaskSpec.operation` остался `unresolved`, runtime validator всё равно может включить узкий backstop для безопасного случая:

- `archetype = simple_extraction`;
- есть ровно один root;
- есть `array_indexing`;
- текст задачи однозначно содержит `последний/last` или `первый/first`.

Это не заменяет planner и не должно превращаться в широкий deterministic planner. Это только safety-net, чтобы кандидат вида `return wf.vars.emails` не проходил как valid, когда задача явно просит элемент массива.

## 3. Целевой пайплайн

### 3.1. Stage 0 - Input normalization

На входе:

- `task_text`;
- `provided_context`;
- `mode`;
- `language`;
- при наличии: `input_roots`, `output_mode`, `archetype`, `risk_tags`.

На выходе:

- нормализованный payload для planner;
- cleaned context;
- явный user language: `ru` или `en`.

### 3.2. Stage 1 - Planner agent

Planner agent не должен сразу генерировать код.
Он должен построить структурированное описание задачи.
Deterministic planner fallback должен оставаться только структурным: язык, безопасные дефолты и roots из контекста.
Смысловые поля (`operation`, `expected_shape`, `task_intents`, `clarification_required`) должен выставлять LLM planner agent.
Ответ planner agent должен быть компактным и помещаться в маленький `num_predict`, например:

```json
{"op":"last_array_item","mode":"raw_lua","roots":["wf.vars.emails"],"shape":"scalar_or_nil","edges":["single_item","empty_array"],"clar":false}
```

Полные ключи можно поддерживать для совместимости, но целевой протокол использует short keys: `op`, `mode`, `roots`, `shape`, `risks`, `edges`, `intents`, `clar`, `q`.
Во входных facts допустимо дублировать самые важные алиасы, например `mode` + `output_mode` и `explicit_roots` + `explicit_input_basis`: это почти не увеличивает prompt, но снижает риск ошибки у слабой модели или fallback-обвязки.

Минимальный `TaskSpec`:

```text
TaskSpec
- task_text
- language
- archetype
- operation
- output_mode
- input_roots
- expected_shape
- risk_tags
- edge_cases
- clarification_required
- clarification_question
```

Примеры:

- `simple_extraction` + `last_array_item`
- `filtering` + `array_result`
- `transformation` + `field_update`
- `datetime_conversion`
- `patch_mode` + `additive_payload`

Planner agent должен уметь:

- понять archetype;
- выделить корни данных;
- определить expected result shape;
- определить edge cases;
- запросить уточнение, если задача реально двусмысленна.

Planner agent не должен:

- гадать там, где нужен вопрос;
- подменять генерацию "готовым ответом";
- смешивать `wf.vars.*` и `wf.initVariables.*` без основания.

### 3.3. Stage 2 - Prompter agent

Prompter agent должен использовать:

- `TaskSpec`;
- правила домена;
- ограничения output mode;
- risk hints;
- archetype-specific guidance;

На текущем этапе retrieval guidance выключен полностью. Его нельзя добавлять в generator prompt, пока не будет отдельного quality gate для retrieval corpus. Причина: даже один неверный или слишком близкий few-shot пример начинает конкурировать с `TaskSpec` и ломает простые extraction-задачи.

Prompter agent не должен возвращать весь generator prompt целиком.
Он должен возвращать компактный patch к локально собранному fallback prompt, например:

```json
{"sys":["Return the last array item, not the whole array."],"user":["Use wf.vars.emails[#wf.vars.emails]."]}
```

Полный generator prompt разворачивается локально из deterministic fallback + этих коротких additions. Локальный fallback тоже не должен включать `Retrieved guidance`, `Similar examples` или `raw_lua=...`.

Prompter agent не должен:

- подсовывать exact expected answer из benchmark-case в blind evaluation;
- использовать public evaluation cases как few-shot examples в blind benchmark;
- заменять deterministic contract длинным "удачным prompt".
- эхоить полный fallback prompt в своём JSON-ответе.

### 3.4. Stage 3 - Generator

Generator получает:

- prompt package;
- runtime options;
- выбранную модель.

Generator возвращает:

- raw candidate;
- при debug: raw response, normalized visible response, reasoning/debug trail.

### 3.5. Stage 4 - Deterministic contract validator

Это первый жёсткий барьер.

Он должен проверять:

- корректность output mode;
- отсутствие markdown fences там, где они запрещены;
- отсутствие prose вне допустимого режима;
- запрет JsonPath;
- запрет неразрешённых root paths;
- запрет на `wf.data.*` и другие выдуманные data roots;
- ограничения по shape для `raw_lua`, `json_wrapper`, `patch_mode`, `clarification`;
- ограничения по allowed root family.

Если контракт нарушен, валидатор должен возвращать структурированный finding, а не только строку.

Минимальный формат:

```text
ValidationFinding
- validator
- failure_class
- message
- location
- repairable
- ambiguous
- suggestion
```

### 3.6. Stage 5 - Syntax / style / static validation

После прохождения базового контракта запускаются deterministic static checks:

- Lua syntax checks;
- `stylua`;
- `luacheck`;
- path validator;
- forbidden pattern validator;
- archetype-specific static rules.

Это слой, который должен ловить:

- синтаксические ошибки;
- дисбаланс `if/end`, `for/end`, `while/end`, `function/end`;
- неверные пути;
- ошибки patch-mode;
- потерю array semantics;
- отсутствие `_utils.array.new()` там, где результат собирается заново.

### 3.7. Stage 6 - Runtime behavioral validator

Это ключевой недостающий слой.

Для задач, где поведение можно проверить исполнением, надо запускать candidate на fixture-контексте.

Первая обязательная зона для внедрения:

- `simple_extraction`

Следующие кандидаты:

- `filtering`
- `numeric_transform`
- часть `transformation`
- часть `datetime_conversion`

Runtime validator должен:

- подготовить sandboxed Lua execution;
- выполнить candidate на основном context;
- при необходимости выполнить candidate на 1-2 edge case fixtures;
- сравнить фактический результат с expected behavior, а не только с exact string.

Пример для задачи "получи последний email":

- fixture A: `["user1", "user2", "user3"]` -> expected `"user3"`
- fixture B: `["only"]` -> expected `"only"`
- fixture C: `[]` -> expected `nil` или согласованный fallback

Это означает:

- `return wf.vars.emails[#wf.vars.emails]` -> pass
- `return wf.vars.emails` -> fail
- `return (wf.vars.emails and #wf.vars.emails>0) and wf.vars.emails or nil` -> fail

### 3.8. Stage 7 - Semantic critic

Semantic critic в новой архитектуре остаётся обязательным, но его роль меняется.

Он нужен для:

- semantic review после deterministic checks;
- случаев, где deterministic validators не покрывают смысл задачи;
- случаев реальной двусмысленности;
- формирования repair hint;
- постановки focused clarification question.

Semantic critic не должен:

- быть единственным арбитром для простых extraction-задач;
- заменять runtime behavioral validation;
- принимать решение в отрыве от structured failure bundle.

Prompt semantic critic тоже должен быть компактным. Ему нельзя отдавать полный generation prompt, весь fallback prompt и весь debug payload. Достаточно:

- компактный TaskSpec: `text`, `op`, `shape`, `roots`, `mode`, `risks`;
- текущий candidate;
- сжатый статус deterministic validators;
- runtime metadata только если она есть: expected / actual / failed fixture / behavioral fingerprint.

Целевой ответ semantic critic должен помещаться в маленький `num_predict`, например:

```json
{"s":"fail","c":"semantic_mismatch","m":"возвращается массив вместо последнего элемента","fix":"верни wf.vars.emails[#wf.vars.emails]"}
```

Полные ключи `status`, `failure_class`, `message`, `suggestion` можно поддерживать для совместимости.
Если semantic critic вернул пустой или невалидный ответ, это `semantic_critic_invalid_response`, а не успешная semantic validation. Для `raw_lua`, где runtime validation тоже не прошёл или был skipped, такой результат не должен давать `passed`.
Если runtime backstop доступен, semantic critic не должен перекрывать его результат: runtime mismatch должен вести в repair, даже если semantic critic позже мог бы ошибочно сказать `pass`.

### 3.9. Stage 8 - Repair re-prompt loop

Отдельный `repair agent` не нужен как LLM-слой.

Если critic / stop policy видит repairable error, то следующий круг строится так:

```text
ValidationBundle + critic repair hint
-> prompter agent
-> generator agent
-> validators
```

Prompter agent должен получать `ValidationBundle`, например:


```text
ValidationBundle
- task_spec
- current_candidate
- format_report
- static_report
- runtime_report
- semantic_report
- final_failure_classes
- repair_priority
```

Prompter должен заново собрать generator prompt из фактов:

- что именно нарушено;
- какой root использован неправильно;
- что вернул runtime check;
- какой shape ожидался;
- какие edge cases сломаны.

Generator на repair-итерации не должен получать только абстрактное "почини".
Он должен получать разжёванное задание от prompter agent с явным запретом повторять провалившийся candidate shape.
При этом repair-generator prompt должен быть компактным: `Task`, compact `TaskSpec`, `Current candidate`, compact `Validation summary`, `Repair task`. Туда нельзя класть полный `Original prompt`, полный `ValidationBundle facts` или debug payload.

### 3.10. Stage 9 - Stop policy

Нужны жёсткие условия завершения цикла:

- `passed`
- `clarification_requested`
- `repair_exhausted`
- `oscillation_detected`
- `validator_conflict`

Если после нескольких repair-итераций код остаётся невалидным, pipeline должен перейти в `clarification_requested`, попросить помощи у пользователя и затем запустить новый круг уже с его feedback.

Oscillation должна считаться не только по text fingerprint, но и по:

- повторяющемуся runtime result;
- повторяющемуся invalid shape;
- повторяющемуся disallowed root usage.

Повторяющийся failure class сам по себе не должен сразу останавливать цикл: он может быть полезным сигналом для prompter agent, но не является достаточным доказательством осцилляции без повторяющейся формы кандидата или поведения.

## 4. Роль агентов в новом пайплайне

### 4.1. Planner agent

Отвечает за:

- разбор задачи;
- выделение archetype;
- выделение `input_roots`;
- expected behavior;
- edge cases;
- нужен ли clarification.

### 4.2. Generator agent

Отвечает за:

- генерацию кандидата по `TaskSpec` и prompt package.

### 4.3. Prompter agent

Отвечает за:

- сборку исходного generator prompt;
- сборку repair generator prompt из `ValidationBundle`;
- явное объяснение generator-у, какая ошибка была найдена и какой candidate shape нельзя повторять.

### 4.4. Semantic critic agent

Отвечает за:

- semantic critique;
- clarification;
- repair hint;
- анализ сложных случаев, которые не покрываются deterministic checks.

### 4.5. Deterministic validators

Это не LLM-агенты.
Это жёсткие инженерные слои, которые проверяют:

- контракт;
- syntax/style;
- runtime behavior;
- domain restrictions.

## 5. Ограничения Lua в LowCode - hard rules из публичного PDF

Ниже перечислены ограничения, которые должны трактоваться как machine-enforced rules, а не как "желательные подсказки для модели".

### 5.1. Версия языка

- используется `Lua 5.5`

Validator должен проверять совместимость кода с LocalScript Lua baseline и не опираться на конструкции, которых нет в этом контуре.

### 5.2. Формат Lua-кода в LowCode

Из PDF:

- скрипт описывается в формате `JsonString lua{-- действие }lua`

Это означает:

- если выбран режим, где код возвращается внутри JSON-строки, строка с кодом должна использовать wrapper `lua{...}lua`;
- validator должен жёстко ловить поломанный wrapper;
- генератор не должен смешивать wrapper-режим с `raw_lua`.

### 5.3. Запрет JsonPath

Из PDF:

- в lua-скрипте нельзя обращаться к переменным с помощью JsonPath;
- вместо этого необходимо указывать прямое обращение к данным.

Validator должен запрещать:

- JsonPath-подобные обращения;
- любые конструкции, где путь данных передан как строковое выражение вместо прямого доступа;
- подмену прямого доступа эквивалентами "в стиле JsonPath".

### 5.4. Корни данных

Из PDF:

- все объявленные в LowCode переменные хранятся в `wf.vars`
- переменные, которые получает схема при запуске из `variables`, хранятся по пути `wf.initVariables`

Validator должен:

- различать `wf.vars.*` и `wf.initVariables.*`;
- запрещать `wf.data.*` и другие выдуманные семейства;
- запрещать смешение `wf.vars.*` и `wf.initVariables.*` без явного основания;
- требовать clarification, если root family не может быть выбран безопасно.

### 5.5. Допустимые типы данных

Из PDF разрешены:

- `nil`
- `boolean`
- `number`
- `string`
- `array`
- `table`
- `function`

Практическое следствие для validator:

- candidate не должен полагаться на посторонние доменные типы;
- array/table semantics должны проверяться отдельно;
- empty / nil handling должно проверяться явно.

### 5.6. Работа с массивами

Из PDF:

- для создания нового массива используется `_utils.array.new()`
- для объявления существующей переменной массивом используется `_utils.array.markAsArray(arr)`

Это hard rule для runtime и static validation.

Validator должен:

- требовать `_utils.array.new()`, если создаётся новый результирующий массив;
- требовать `_utils.array.markAsArray(arr)`, если existing variable нужно объявить массивом;
- ловить случаи, где массивная задача возвращает plain table без явной array semantics;
- проверять, что array task не ломает downstream shape.

### 5.7. Разрешённые конструкции

Из PDF разрешены базовые конструкции:

- `if...then...else`
- `while...do...end`
- `for...do...end`
- `repeat...until`

Это не означает автоматический запрет всех остальных Lua-конструкций, но задаёт baseline безопасного подмножества, на которое должен ориентироваться planner и generator.

Практическое правило:

- при отсутствии жёсткой необходимости generator должен оставаться внутри простых, прозрачных и легко валидируемых конструкций;
- validator может помечать экзотические или избыточно сложные конструкции как risk factor даже если синтаксис формально корректен.

## 6. Проектные правила валидации поверх PDF

Ниже перечислены ограничения, которые уже зафиксированы в проекте и должны войти в новый pipeline.

### 6.1. Output modes

Поддерживаются:

- `raw_lua`
- `json_wrapper`
- `patch_mode`
- `clarification`

Требования:

#### `raw_lua`

- вернуть только Lua-код;
- без markdown fences;
- без prose;
- без JSON object.

#### `json_wrapper`

- outer shape = валидный JSON object;
- Lua-поля должны быть в строках вида `lua{...}lua`;
- JSON должен быть корректно экранирован.

#### `patch_mode`

- только additive payload;
- только нужные поля;
- нельзя переписывать весь payload;
- не текстовый diff.

#### `clarification`

- plain text вопрос;
- без JSON;
- без markdown;
- без генерации кода вместо вопроса.

### 6.2. Запрещённые паттерны

Validator должен жёстко отсекать:

- JsonPath;
- `wf.data.*`;
- смешение root families без safe basis;
- markdown fences в `raw_lua`;
- prose перед кодом;
- broken `lua{...}lua`;
- patch full rewrite;
- потерю array semantics;
- неявное смешение `nil`, `""` и "есть значение".

### 6.3. Array-specific checks

Нужно отдельно проверять:

- last / first item extraction;
- empty array handling;
- singleton array behavior;
- фильтрацию с сохранением array semantics;
- нормализацию singleton -> array, если этого требует задача.

### 6.4. Date/time checks

Если задача работает со временем:

- надо проверять формат входа;
- учитывать timezone offset;
- не допускать silently wrong conversion;
- при необходимости использовать safe fallback или fail-fast.

## 7. Что именно надо переделать в текущем validator

### 7.1. Что оставить

Оставить:

- format validator;
- forbidden-pattern validator;
- path validator;
- `stylua` / `luacheck`;
- critic;
- repair loop;
- clarification path.

### 7.2. Что поменять концептуально

Поменять:

- critic перестаёт быть главным источником истины;
- planner появляется как отдельный первый слой;
- prompter становится отдельным агентным слоем и участвует не только в первой генерации, но и в repair-итерациях;
- runtime behavioral validation становится обязательным хотя бы для `simple_extraction`;
- repair должен быть re-prompt loop через prompter/generator из structured failures, а не отдельным repair LLM-agent;
- blind benchmark должен быть отделён от retrieval knowledge.

## 8. Приоритеты внедрения

### Slice 1

- `TaskSpec`
- planner
- deterministic contract validator cleanup
- language-aware validation messaging

### Slice 2

- runtime validator для `simple_extraction`
- behavioral fixtures для:
  - last item
  - first item
  - empty array
  - scalar extraction

### Slice 3

- repair re-prompt через `prompter agent` из `ValidationBundle`
- oscillation detection по behavioral fingerprints
- refinement critic role

### Slice 4

- runtime validators для `filtering`
- runtime validators для `numeric_transform`
- runtime validators для части `transformation`

### Slice 5

- blind benchmark mode
- отключение evaluation leakage
- разделение retrieval corpus и evaluation corpus

## 9. Что должен знать следующий агент

Следующий агент не должен начинать с "ещё одного clever prompt".

Он должен исходить из следующих инженерных фактов:

1. сильная модель может решить задачу правильно;
2. текущая система может всё равно увести ответ в `bounded_failure`;
3. значит проблема не всегда в генерации, а часто в validator / critic / repair orchestration;
4. для простых задач надо проверять поведение кода, а не только текст;
5. semantic critic нужен, но как слой поверх deterministic validation, а не вместо неё.

## 10. Non-goals

Этот документ не утверждает:

- финальную модель;
- финальный prompt wording;
- точный implementation detail sandbox runner;
- конкретный формат benchmark report.

Он утверждает только:

- целевую архитектуру validation pipeline;
- жёсткие ограничения домена;
- приоритет deterministic validation;
- необходимость planner + critic + runtime checks в одном контуре.
