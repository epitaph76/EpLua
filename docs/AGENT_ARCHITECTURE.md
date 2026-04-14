# Agent Architecture

`luaMTS` использует небольшой агентный контур вокруг генерации LowCode Lua.

Цель архитектуры - не автономный coding agent, а управляемый pipeline:

```text
understand -> prompt -> generate -> validate -> repair/finalize
```

## Components

| Component | Responsibility |
| --- | --- |
| `GenerationService` | владеет порядком стадий, budget-ом repair и debug trace |
| `planner agent` | строит компактный `TaskSpec`, не пишет Lua |
| `prompter agent` | добавляет короткие русские подсказки к fallback prompt, не пишет Lua |
| `generator` | единственный слой, который пишет candidate |
| `truncation guard` | продолжает generator output, если он обрезан на `num_predict` |
| `deterministic validators` | проверяют JSON/Lua/LowCode contract |
| `critic report` | выбирает `finalize` или `repair` по validator reports |
| `CLI` | тонкий клиент поверх API и прямого Ollama debug path |

## Active flow

```text
request_received
-> planner
-> prompter
-> generation
-> deterministic_validation
-> response_ready
```

Repair flow:

```text
deterministic_validation
-> repair_generation
-> deterministic_validation
-> response_ready
```

## Control rules

- Только `GenerationService` меняет state pipeline.
- Planner и prompter не генерируют Lua.
- Prompter не возвращает полный prompt, а только additions.
- Generator output не чинится скрыто перед validator-ом.
- Если output упёрся в `num_predict`, continuation guard склеивает полный candidate до validation.
- Repair loop bounded.
- Debug trace обязан показывать prompt package, model calls и validation passes.

## Terminal statuses

- `passed`;
- `repaired`;
- `failed`;
- `not_run` для прямых diagnostic paths без validation.

## Related docs

- [how_validation_work.md](how_validation_work.md)
- [AGENT_PIPELINE_SEQUENCE.md](AGENT_PIPELINE_SEQUENCE.md)
- [CLI_CLIENT.md](CLI_CLIENT.md)
