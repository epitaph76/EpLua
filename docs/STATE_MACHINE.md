# State Machine

Текущая state machine принадлежит `GenerationService`.

## States

```text
request_received
planner
prompter
generation
deterministic_validation
repair_generation
response_ready
```

`repair_generation` появляется только если validation report repairable и repair budget ещё не исчерпан.

## Shared state

Между стадиями переносятся:

- `task_text`;
- `provided_context`;
- `TaskSpec`;
- `PromptBuilderResult`;
- `candidate`;
- `validation_passes`;
- `critic_report`;
- `repair_count`;
- `generation_pass_count`;
- temporary generator files.

## Invariants

- Planner/prompter не пишут Lua.
- Generator output идёт в validator без human-view преобразования.
- Hidden deterministic cleanup candidate запрещён.
- `repair_budget >= 1`.
- `generation_pass_count < repair_budget` для входа в repair loop.
- Temporary files живут до финального статуса запроса и затем удаляются.

## Terminal conditions

Pipeline завершается, когда:

- validation passed;
- critic action не `repair`;
- repair budget исчерпан;
- model/API error проброшена наружу как error response.

## Status mapping

| Condition | Status |
| --- | --- |
| first validation pass | `passed` |
| validation pass after repair | `repaired` |
| budget exhausted or final validation fail | `failed` |
| diagnostic path without validation | `not_run` |

## Progress events

`/generate/progress` публикует stage events в порядке прохождения state machine. CLI использует их для live progress в debug/release режимах.
