# SKILLS

## Назначение

Этот документ фиксирует skill decomposition для `S-3`.

Здесь `skills` означают не внешний marketplace и не tool zoo, а узкие
orchestration-роли, которые later stages смогут реализовать как отдельные
модули, prompt packs или локальные функции.

## Skill Set

| Skill | States | Input | Output | Что skill не делает |
| --- | --- | --- | --- | --- |
| `task_understanding` | `task_classification`, `context_check` | user task + optional context | task intent, sufficiency verdict, missing fields | не генерирует код |
| `mode_router` | `mode_selection` | task intent + format hints | one canonical output mode | не выбирает archetype |
| `archetype_router` | `archetype_selection` | task intent + domain hints | one canonical archetype | не меняет response mode |
| `generator` | `generation` | prompt package | initial candidate | не валидирует свой результат |
| `format_validator` | `format_validation` | candidate + output mode | structured format report | не запускает repair |
| `rule_validator` | `rule_validation` | candidate + domain constraints | structured domain report | не решает, нужен ли clarification |
| `critic` | `critic_step` | validator reports + candidate | localized repair task or stop signal | не переписывает user goal |
| `repair_generator` | `repair_generation` | candidate + critic report | repaired candidate | не подменяет critic |
| `clarifier` | `clarification` | missing inputs or ambiguity summary | one precise question | не пытается чинить задачу без данных |

## Execution Rules

- Ни один skill не владеет общим state: ownership всегда у orchestrator.
- Ни один skill не может сам запустить loop или увеличить budget.
- `generator` и `critic` должны оставаться разными ролями даже при общей модели.
- Validators обязаны быть детерминированнее и уже по scope, чем generation.
- `clarifier` может сформулировать максимум один вопрос за весь run.

## Why This Decomposition

Из **Qwen Code** берётся идея раскладывать workflow на узкие cognitive steps:

- classify;
- choose mode;
- generate;
- review;
- ask clarification.

Из **Claw Code** берётся идея, что эти шаги должны жить внутри явного pipeline:

- с наблюдаемыми состояниями;
- с отдельными validators/checks;
- с модульным разделением orchestrator / model adapter / validators.

## Mapping To Existing Project Artifacts

- `task_understanding` и `archetype_router` обязаны опираться на
  [TASK_ARCHETYPES.md](TASK_ARCHETYPES.md);
- `mode_router` обязан опираться на
  [OUTPUT_MODES.md](OUTPUT_MODES.md);
- `rule_validator` обязан использовать ограничения из
  [DOMAIN_MODEL.md](DOMAIN_MODEL.md) и [CONSTRAINTS.md](CONSTRAINTS.md);
- все skills подчиняются bounded loop из
  [STATE_MACHINE.md](STATE_MACHINE.md).

## Non-Goals

Этот skill set намеренно не включает:

- интернет-поиск;
- IDE-like tool execution;
- длинные автономные research loops;
- retrieval;
- UI-специфичные способности.

Именно это удерживает `S-3` в границах минимального agent contour.
