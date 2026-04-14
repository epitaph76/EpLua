# Pipeline Skills

В этом документе `skills` означают внутренние роли pipeline, а не внешний marketplace.

## Active roles

| Role | Stage | Responsibility |
| --- | --- | --- |
| `planner` | `planner` | построить compact `TaskSpec` |
| `prompter` | `prompter` | добавить короткие prompt additions к LowCode fallback prompt |
| `generator` | `generation`, `repair_generation` | сгенерировать candidate |
| `format_validator` | `deterministic_validation` | проверить JSON/output contract |
| `syntax_validator` | `deterministic_validation` | проверить Lua syntax/style |
| `static_validator` | `deterministic_validation` | проверить roots, forbidden patterns, static rules |
| `principle_validator` | `deterministic_validation` | проверить LowCode principles |
| `rule_validator` | `deterministic_validation` | собрать итоговый rule report |
| `critic_report` | после validators | выбрать `finalize` или `repair` |
| `cli` | outside API | показать progress/result и не менять raw candidate |

## Rules

- Planner и prompter не пишут Lua.
- Generator не валидирует сам себя.
- Validators не меняют candidate.
- Critic report не пишет новый answer.
- Repair bounded и идёт через `repair_generation`.
- CLI не подменяет API pipeline.

## Non-goals

- Интернет-поиск.
- Неограниченная автономия.
- IDE/tool zoo.
- Подмешивание benchmark expected solution в prompt.
