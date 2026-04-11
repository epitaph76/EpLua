# AGENT ARCHITECTURE

## Назначение

Этот документ фиксирует минимальный agent contour для `S-3`.

Contour нужен не для превращения проекта в general-purpose coding agent, а для
контролируемого улучшения single-shot generation за счёт явной, конечной и
воспроизводимой orchestration-схемы.

## Design Goals

- отделить понимание задачи, генерацию, валидацию и repair;
- держать loop конечным: максимум 2 repair-итерации и максимум 1 clarification;
- опираться на явную state machine, а не на "скрытую магию" модели;
- использовать уже зафиксированные артефакты `S-1` вместо новой доменной логики;
- не заходить в `S-4` backend/API design, `S-6` validator implementation и `S-7`
  retrieval.

## Fixed Inputs

Pipeline `S-3` опирается на следующие зафиксированные входы:

- текст пользовательской задачи;
- опциональный payload или контекст задачи;
- provisional model tag `qwen2.5-coder:3b`;
- canonical archetypes из [TASK_ARCHETYPES.md](TASK_ARCHETYPES.md);
- canonical output modes из [OUTPUT_MODES.md](OUTPUT_MODES.md);
- typical failure classes из [DOMAIN_MODEL.md](DOMAIN_MODEL.md);
- architectural guardrails из [CONSTRAINTS.md](CONSTRAINTS.md).

## Core Components

| Component | Responsibility | Boundary |
| --- | --- | --- |
| `orchestrator` | владеет состоянием, budget-ами и переходами | не генерирует код сам |
| `task understanding layer` | классифицирует задачу, проверяет достаточность контекста, выбирает mode и archetype | не делает repair и не меняет state |
| `generation layer` | получает prompt package и возвращает candidate | не валидирует свой же output |
| `format validator` | проверяет соответствие выбранному output mode | не принимает решение о clarification |
| `rule validator` | проверяет доменные ограничения LocalScript | не генерирует исправления |
| `critic` | локализует проблему и формирует repair task | не меняет user goal |
| `finalizer` | отдаёт валидный результат или bounded stop result | не запускает новый цикл |

## Control Rules

- Только `orchestrator` может менять текущее состояние pipeline.
- `generation` и `critic` разделены по ролям даже в случае, если позже будут
  использовать одну и ту же модель.
- Validators обязаны возвращать структурированный результат: `pass/fail`,
  failure class, локализацию проблемы и признак repairability.
- `critic` работает только от validator reports и текущего candidate, а не
  придумывает новую задачу.
- `clarification` разрешён только тогда, когда без дополнительного ввода риск
  ошибочного кода выше, чем польза от ещё одной repair-попытки.
- Интернет, неограниченная автономия и произвольный tool zoo в этот contour не
  входят.

## End-To-End Flow

1. `task_classification` определяет класс задачи.
2. `context_check` проверяет, достаточно ли данных для безопасного решения.
3. `mode_selection` выбирает response mode.
4. `archetype_selection` фиксирует canonical archetype.
5. `generation` строит первичный candidate.
6. `format_validation` проверяет форму ответа.
7. `rule_validation` проверяет доменные правила.
8. При ошибке `critic_step` превращает validator findings в repair task.
9. `repair_generation` делает bounded retry.
10. `finalize` отдаёт валидный результат.
11. `clarification` завершает попытку одним точным вопросом, если данных не
    хватает.

Полная последовательность вынесена в
[AGENT_PIPELINE_SEQUENCE.md](AGENT_PIPELINE_SEQUENCE.md).

## Terminal Outcomes

| Outcome | When produced |
| --- | --- |
| `success` | формат и доменные правила пройдены |
| `clarification_requested` | отсутствует критичный вход или repair небезопасен без уточнения |
| `bounded_failure` | repair budget исчерпан и один вопрос уже не спасает результат |

`bounded_failure` на этом этапе является внутренним архитектурным исходом.
Его transport-level представление будет определено только на `S-4`.

## What This Document Does Not Define

- HTTP contract или response schema backend;
- способ вызова `Ollama` и runtime wiring;
- конкретную реализацию validators;
- benchmark harness;
- retrieval layer;
- UI-поведение.
