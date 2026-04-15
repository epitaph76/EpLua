# Agentic Iteration Plan

Этот документ описывает будущий слой агентных итераций для метрики:

```text
Агентность и качество итераций: 0-25 баллов
```

Цель: показать, что `luaMTS` умеет не только дать один ответ модели, но и вести управляемый цикл:

```text
уточнить задачу -> сгенерировать -> проверить -> исправить -> принять обратную связь
```

Документ описывает план. Не всё из него является текущим active behavior.

## 1. Current baseline

Текущий быстрый pipeline уже работает так:

```text
request_received
-> planner
-> prompter
-> generation
-> deterministic_validation
-> optional repair_generation
-> response_ready
```

Этот pipeline нужно сохранить как быстрый default path.

Новый interaction layer не должен превращать каждую простую задачу в длинный диалог. Он должен включаться:

- явно через `/plan`;
- при неоднозначности, если planner уверен, что без уточнения высок риск ошибки;
- после исчерпания короткого repair loop;
- при явном feedback пользователя.

## 2. Target scoring behavior

Метрика жюри проверяет три способности:

1. Система задаёт уточняющие вопросы до генерации.
2. Система воспринимает обратную связь после результата.
3. Система улучшает результат итерационно, а не просто делает новый one-shot.

Для этого нужны четыре режима:

- plan preflight;
- clarification questions;
- feedback re-run;
- user-assisted repair.

## 3. High-level architecture

Новый слой должен быть оболочкой вокруг текущего pipeline:

```text
Interaction Layer
  -> optional plan preflight
  -> current short pipeline
  -> optional feedback / assisted repair
```

Текущий pipeline остаётся ядром:

```text
planner -> prompter -> generator -> deterministic_validation -> repair_generation
```

Если пользовательская подсказка меняет смысл задачи, новый широкий цикл должен стартовать снова с planner, а не только с generator.

## 4. CLI UX

### 4.1. Slash commands

Текущий минимальный набор вокруг planning:

```text
/plan
/feedback <text>
/repair-budget <number>
```

Значения по умолчанию:

```text
plan_mode = off
repair_budget = 2
user_assisted_budget = 1
```

### 4.2. `/plan`

`/plan` включает plan preflight для следующего запроса.

Пример:

```text
luamts> /plan
Plan mode: on for next request

luamts> Преобразуй DATUM и TIME в ISO 8601. {"wf": {...}}
```

CLI вызывает planning endpoint или planning mode и показывает:

```text
План:
- операция: datetime_formatting
- вход: wf.vars.json.IDOC.ZCDF_HEAD.DATUM, wf.vars.json.IDOC.ZCDF_HEAD.TIME
- результат: ISO 8601 string
- некорректный вход: вернуть пустую строку

Запускать генерацию? Enter - да, /cancel - отмена
```

Если нужны уточнения:

```text
Нужно уточнение:

1. Что вернуть, если дата или время некорректны?
   1) пустую строку
   2) nil
   3) свой вариант

> 1
```

После ответа пользователя CLI запускает обычный generation pipeline.

## 5. Planner extension

Сейчас planner умеет `clarification_required` и `clarification_question`.

Нужно расширить protocol до списка вопросов:

```json
{
  "arch": "datetime_conversion",
  "op": "datetime_formatting",
  "mode": "raw_lua",
  "roots": [
    "wf.vars.json.IDOC.ZCDF_HEAD.DATUM",
    "wf.vars.json.IDOC.ZCDF_HEAD.TIME"
  ],
  "shape": "iso8601_string",
  "risks": ["invalid_date", "invalid_time"],
  "edges": ["invalid_format"],
  "clar": true,
  "questions": [
    {
      "id": "invalid_datetime_behavior",
      "q": "Что вернуть, если дата или время некорректны?",
      "options": [
        {"id": "empty_string", "label": "пустую строку"},
        {"id": "nil", "label": "nil"},
        {"id": "custom", "label": "свой вариант"}
      ],
      "default": "empty_string"
    }
  ],
  "intents": ["datetime_conversion"]
}
```

Rules:

- максимум 3 вопроса;
- вопрос должен быть конкретным;
- варианты должны быть взаимоисключающими;
- default option должен быть безопасным;
- planner не должен спрашивать то, что уже очевидно из задачи;
- planner не должен спрашивать про внутренние implementation details, если пользователь их не задавал.

## 6. Structured clarification state

Ответы пользователя нужно хранить структурно, а не просто дописывать свободным текстом.

Форма:

```json
{
  "clarifications": [
    {
      "question_id": "invalid_datetime_behavior",
      "answer_id": "empty_string",
      "answer_text": "пустую строку"
    }
  ]
}
```

В prompt они должны попадать коротким блоком:

```text
Уточнения пользователя:
- invalid_datetime_behavior: empty_string
```

Это снижает риск, что модель воспримет уточнение как новый unrelated task.

## 7. Plan-only API shape

Вариант A: отдельный endpoint.

```text
POST /plan
```

Request:

```json
{
  "task_text": "...",
  "provided_context": "...",
  "mode": "debug",
  "language": "ru"
}
```

Response:

```json
{
  "task_spec": {...},
  "clarification_required": true,
  "questions": [...],
  "trace": ["request_received", "planner", "response_ready"]
}
```

Вариант B: флаг в `/generate`.

```json
{
  "plan_only": true
}
```

Рекомендация: начать с отдельного `/plan`. Так меньше риск сломать текущий `/generate`.

## 8. Feedback mode

Пользователь может после результата сказать:

```text
/feedback Нужно не просто вернуть значение, а сохранить его в wf.vars.lastEmail
```

CLI должен собрать новый request:

```text
Original task:
...

Previous candidate:
...

User feedback:
Нужно не просто вернуть значение, а сохранить его в wf.vars.lastEmail
```

Но запускать его лучше не сразу в generator, а через planner:

```text
original_task
+ previous_candidate
+ user_feedback
-> planner
-> prompter
-> generator
-> deterministic_validation
```

Причина: feedback может поменять output target, allowed mutation, expected shape или input roots.

## 9. User-assisted repair

Обычный короткий repair path:

```text
generation
-> deterministic_validation fail
-> repair_generation
-> deterministic_validation
```

Если после `repair_budget` результат всё ещё invalid, включается user-assisted repair.

### 9.1. Failure summarizer

Нужен слой, который не пишет Lua, а объясняет проблему человеку.

Он получает:

- original task;
- compact `TaskSpec`;
- latest candidate;
- validation failures;
- critic repair instruction;
- repair history summary.

Он возвращает:

```json
{
  "summary": "Код создаёт обычную Lua table, но задача требует LowCode array.",
  "options": [
    {
      "id": "use_lowcode_array",
      "label": "Использовать _utils.array.new()",
      "effect": "Перегенерировать с явным требованием сохранить array semantics."
    },
    {
      "id": "return_scalar",
      "label": "Вернуть одно значение",
      "effect": "Упростить задачу до scalar extraction."
    },
    {
      "id": "custom",
      "label": "Свой вариант",
      "effect": "Пользователь вводит уточнение."
    }
  ]
}
```

CLI показывает:

```text
Код не прошёл проверку.

Проблема:
- результат создаёт обычную Lua table, но задача требует LowCode array

Что сделать?
1. Использовать _utils.array.new()
2. Упростить задачу и вернуть одно значение
3. Свой вариант
```

### 9.2. Wide repair loop

После выбора пользователя запускается широкий цикл:

```text
original_task
+ original_context
+ failed_candidates_summary
+ validation_summary
+ user_assisted_feedback
-> planner
-> prompter
-> generation
-> deterministic_validation
-> optional repair_generation
```

Это важно: если пользовательская подсказка меняет смысл задачи, старый `TaskSpec` может быть неверным.

## 10. Budgets

Предлагаемые budget-и:

```text
repair_budget = 2
clarification_budget = 1
user_assisted_budget = 1
max_questions_per_plan = 3
```

Stop conditions:

- plan clarification budget исчерпан;
- user-assisted budget исчерпан;
- пользователь выбрал cancel;
- validation снова failed после assisted loop;
- API/model error.

## 11. Trace and debug report

Чтобы метрика агентности была видна жюри, trace должен фиксировать interaction events.

Пример:

```json
{
  "trace": [
    "request_received",
    "planner",
    "clarification_requested",
    "user_clarification_received",
    "planner",
    "prompter",
    "generation",
    "deterministic_validation",
    "response_ready"
  ],
  "interaction": {
    "plan_mode": true,
    "clarification_questions": [
      {
        "id": "invalid_datetime_behavior",
        "q": "Что вернуть, если дата или время некорректны?"
      }
    ],
    "user_answers": [
      {
        "question_id": "invalid_datetime_behavior",
        "answer_id": "empty_string"
      }
    ],
    "feedback_used": false,
    "assisted_repair_used": false
  }
}
```

Для user-assisted repair:

```json
{
  "interaction": {
    "assisted_repair_used": true,
    "failure_summary": "...",
    "selected_option": "use_lowcode_array",
    "wide_repair_iteration": 1
  }
}
```

## 12. Benchmark implications

Нужно добавить отдельный benchmark mode для агентности:

1. Ambiguous tasks:
   - ожидается clarification question;
   - генерация без вопроса получает penalty.
2. Feedback tasks:
   - первая генерация intentionally incomplete;
   - пользовательский feedback должен улучшить result.
3. Assisted repair tasks:
   - базовый short loop failed;
   - user-assisted hint должен запустить wide repair и пройти validation.

Метрики:

```text
clarification_precision
clarification_usefulness
feedback_acceptance_rate
assisted_repair_success_rate
unnecessary_question_rate
```

Критичный анти-паттерн:

```text
Система спрашивает вопрос там, где задача уже однозначна.
```

Это должно снижать score.

## 13. Implementation slices

### Slice 1: Plan preflight API

Files likely involved:

- `apps/api/routes/generate.py`
- `apps/api/schemas.py`
- `apps/api/services/generation.py`
- `packages/orchestrator/planner.py`
- `apps/api/tests/test_quality_loop.py`

Behavior:

- добавить `/plan`;
- вернуть `TaskSpec`;
- вернуть `questions`;
- не вызывать generator.

### Slice 2: CLI `/plan`

Files likely involved:

- `apps/api/cli/main.py`
- `apps/api/tests/test_cli.py`

Behavior:

- `/plan` включает preflight для следующей задачи;
- CLI показывает questions/options;
- ответы добавляются как structured clarifications;
- затем запускается обычный `/generate`.

### Slice 3: Feedback re-run

Files likely involved:

- `apps/api/cli/main.py`
- `apps/api/services/generation.py`
- `apps/api/tests/test_cli.py`
- `apps/api/tests/test_quality_loop.py`

Behavior:

- `/feedback <text>` берёт последний task/candidate;
- новый цикл стартует с planner;
- debug trace показывает `feedback_received`.

### Slice 4: User-assisted repair

Files likely involved:

- `apps/api/services/generation.py`
- `packages/orchestrator/prompter.py`
- possible new module: `packages/orchestrator/interaction.py`
- `apps/api/cli/main.py`

Behavior:

- после exhausted repair loop API возвращает structured assisted repair request;
- CLI показывает 2 варианта плюс custom;
- selected option запускает wide repair loop from planner.

### Slice 5: Interaction benchmark

Files likely involved:

- `benchmark/`
- `scripts/`
- `packages/benchmark/tests/`

Behavior:

- добавить маленький набор ambiguous/feedback cases;
- считать assisted repair success;
- сохранять interaction trace в report.

## 14. Data contracts

### 14.1. ClarificationQuestion

```json
{
  "id": "string",
  "question": "string",
  "options": [
    {
      "id": "string",
      "label": "string",
      "description": "string"
    }
  ],
  "default_option_id": "string"
}
```

### 14.2. UserClarification

```json
{
  "question_id": "string",
  "option_id": "string",
  "free_text": "string | null"
}
```

### 14.3. AssistedRepairRequest

```json
{
  "summary": "string",
  "failure_classes": ["string"],
  "options": [
    {
      "id": "string",
      "label": "string",
      "effect": "string"
    }
  ],
  "latest_candidate": "string"
}
```

### 14.4. InteractionTrace

```json
{
  "plan_mode": false,
  "clarification_questions": [],
  "user_clarifications": [],
  "feedback_used": false,
  "assisted_repair_used": false,
  "wide_repair_count": 0
}
```

## 15. Prompting rules

Plan/clarification prompts:

- не просить реализации у пользователя;
- спрашивать только business/contract ambiguity;
- максимум 3 вопроса;
- не раскрывать internal validator jargon без необходимости.

Assisted repair prompts:

- объяснять проблему простым языком;
- показывать конкретное нарушение;
- предлагать 2 безопасных варианта;
- третий вариант: custom user instruction;
- не вставлять expected benchmark solution.

Generator after feedback:

- получает original task;
- получает structured clarifications;
- получает feedback summary;
- не получает full debug payload;
- не получает benchmark expected solution.

## 16. Risks

### Too many questions

Риск: система начинает спрашивать уточнения для простых задач.

Mitigation:

- `/plan` off by default;
- auto-clarification только при high ambiguity;
- метрика `unnecessary_question_rate`.

### User feedback overrides safety

Риск: пользователь просит JsonPath или `error()`.

Mitigation:

- feedback проходит через same LowCode constraints;
- prompter additions filter остаётся активным;
- validators остаются final gate.

### Infinite conversation loop

Риск: assisted repair превращается в чат.

Mitigation:

- `user_assisted_budget = 1`;
- после второго failed статуса вернуть bounded failure with report.

### Prompt bloat

Риск: wide repair prompt становится слишком большим для `num_ctx=4096`.

Mitigation:

- передавать compact summaries;
- не передавать full model calls;
- не передавать полный original prompt;
- хранить только latest candidate и failure summary.

## 17. Recommended first implementation

Самый маленький полезный шаг:

```text
POST /plan
+ CLI /plan one-shot
+ максимум 2 questions
+ запуск обычного /generate после ответов
```

Почему это лучший первый slice:

- он прямо закрывает "умеет задавать уточняющие вопросы";
- не ломает текущий generation path;
- легко тестируется;
- даёт демо-сценарий для жюри;
- создаёт data structures для feedback и assisted repair.

Следующий шаг:

```text
/feedback <text> -> planner re-run -> normal pipeline
```

Третий шаг:

```text
repair_exhausted -> assisted repair request -> user choice -> wide repair loop
```
