# STATE MACHINE

## Назначение

Этот документ фиксирует конечную state machine для `S-3`.

Её задача: сделать agent loop явным, управляемым и bounded, чтобы последующие
этапы реализовывали не "общее рассуждение модели", а конкретный pipeline.

## Shared Working Context

Между состояниями переносится только минимально нужный context:

- `task_text`
- `provided_context`
- `output_mode`
- `archetype`
- `candidate`
- `format_report`
- `rule_report`
- `critic_report`
- `repair_count`
- `clarification_count`

## Global Invariants

- `repair_count <= 2`
- `clarification_count <= 1`
- output mode и archetype должны быть зафиксированы до первой генерации
- validators не могут менять candidate напрямую
- любой повторный заход в generation после critic идёт только через
  `repair_generation`

## States

| State | Input | Output | Success transition | Failure transition |
| --- | --- | --- | --- | --- |
| `task_classification` | `task_text` | task intent, preliminary class | `context_check` | `clarification` |
| `context_check` | task intent + `provided_context` | sufficiency verdict, missing fields | `mode_selection` | `clarification` |
| `mode_selection` | task intent + explicit format hints | `raw_lua` / `json_wrapper` / `patch_mode` / `clarification` | `archetype_selection` | `clarification` |
| `archetype_selection` | task intent + domain hints | canonical archetype | `generation` | `clarification` |
| `generation` | prompt package | initial candidate | `format_validation` | `format_validation` |
| `format_validation` | candidate + output mode | `format_report` | `rule_validation` | `critic_step` |
| `rule_validation` | candidate + domain rules | `rule_report` | `finalize` | `critic_step` |
| `critic_step` | validator reports + candidate + budgets | repair task or stop decision | `repair_generation` | `clarification` / `finalize` |
| `repair_generation` | candidate + `critic_report` | repaired candidate | `format_validation` | `format_validation` |
| `finalize` | valid candidate or bounded stop result | final system result | terminal | terminal |
| `clarification` | missing input summary or focused question | one clarification question | terminal | terminal |

## Transition Rules

### `task_classification -> context_check`

Переход разрешён, если задача попадает в один из поддерживаемых классов и не
является явным out-of-scope запросом.

### `context_check -> clarification`

Clarification обязателен, если отсутствует хотя бы один из следующих типов
входа:

- целевой payload/path, без которого нельзя строить код;
- требуемый response mode, если он критичен и не выводится надёжно;
- данные, без которых любое repair-действие станет гаданием.

### `mode_selection -> archetype_selection`

Mode выбирается как отдельная ось:

- archetype отвечает на вопрос "что делаем";
- output mode отвечает на вопрос "как возвращаем результат".

Их смешивание запрещено.

### `format_validation -> rule_validation`

`rule_validation` запускается только после того, как candidate уже соответствует
требуемому output mode. Иначе critic будет чинить сразу две разные проблемы.

### `format_validation / rule_validation -> critic_step`

Переход в critic разрешён только если:

- ошибка локализована;
- есть понятный failure class;
- repair ещё возможен в рамках budget.

### `critic_step -> repair_generation`

Repair возможен, если одновременно выполняются все условия:

- `repair_count < 2`;
- проблема чинится локально, а не требует новой постановки задачи;
- critic способен сформулировать точечное исправление без смены user goal.

### `critic_step -> clarification`

Clarification выбирается вместо repair, если:

- данных недостаточно даже для локального исправления;
- validator report показывает, что ошибка вызвана неоднозначностью задачи;
- точный один вопрос реально может разблокировать следующую попытку.

### `critic_step -> finalize`

`finalize` как bounded stop используется, если:

- repair budget исчерпан;
- failure class повторился после последней repair-попытки;
- проблема не локализуется в конкретный repair task.

## Stop Conditions

Pipeline обязан завершиться при первом выполнении любого условия ниже:

1. format и rule validation оба дали `pass`;
2. отправлен один clarification question;
3. исчерпан repair budget;
4. обнаружен повтор того же failure class после последнего repair;
5. задача вышла за поддерживаемый scope.

## Minimal State Data Contract

Для последующей реализации `S-4+` state machine должна передавать между
состояниями только следующие сущности:

- classification result;
- selected output mode;
- selected archetype;
- candidate;
- validator reports;
- critic report;
- counters для repair и clarification.

Этого достаточно, чтобы реализовать bounded agent contour без скрытого
накопления произвольного контекста.
