# AGENT PIPELINE SEQUENCE

## Назначение

Этот документ фиксирует sequence diagram для pipeline `S-3`.

```mermaid
sequenceDiagram
    participant User
    participant Orchestrator
    participant Understanding as Task Understanding
    participant Model
    participant Format as Format Validator
    participant Rules as Rule Validator
    participant Critic

    User->>Orchestrator: task + optional context
    Orchestrator->>Understanding: classify + context check + mode/archetype select

    alt missing critical input
        Understanding-->>Orchestrator: clarification question
        Orchestrator-->>User: clarification
    else enough context
        Understanding-->>Orchestrator: prompt package
        Orchestrator->>Model: generation
        Model-->>Orchestrator: candidate
        Orchestrator->>Format: validate output mode

        alt format invalid
            Format-->>Orchestrator: format report
            Orchestrator->>Critic: localize defect
            Critic-->>Orchestrator: repair task

            loop max 2 repair iterations
                Orchestrator->>Model: repair_generation
                Model-->>Orchestrator: repaired candidate
                Orchestrator->>Format: validate output mode
                Format-->>Orchestrator: format report
            end
        else format valid
            Format-->>Orchestrator: pass
        end

        Orchestrator->>Rules: validate domain rules

        alt rules pass
            Rules-->>Orchestrator: pass
            Orchestrator-->>User: finalize
        else repairable rule failure
            Rules-->>Orchestrator: rule report
            Orchestrator->>Critic: localize defect
            Critic-->>Orchestrator: repair task
            Orchestrator->>Model: repair_generation
            Model-->>Orchestrator: repaired candidate
            Orchestrator->>Format: validate output mode
        else missing input or budget exhausted
            Rules-->>Orchestrator: stop signal
            Orchestrator-->>User: clarification or bounded failure
        end
    end
```

## Reading Notes

- `Understanding` объединяет classification, context check, mode selection и
  archetype selection.
- `Critic` не пишет новый answer с нуля, а только формирует repair task.
- bounded repair loop остаётся под управлением `Orchestrator`.
- terminal outcomes ограничены: `success`, `clarification_requested`,
  `bounded_failure`.
