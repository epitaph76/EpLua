# Agent Pipeline Sequence

Этот документ фиксирует текущую sequence diagram для API path `/generate`.

## Happy path

```mermaid
sequenceDiagram
    participant User
    participant API as FastAPI /generate
    participant Service as GenerationService
    participant Planner as Planner agent
    participant Prompter as Prompter agent
    participant Generator as Generator model call
    participant Validator as Deterministic validators
    participant Critic as Critic report

    User->>API: task_text + optional provided_context
    API->>Service: GenerateRequest
    Service-->>User: progress request_received

    Service->>Planner: compact planner prompt
    Planner-->>Service: TaskSpec JSON
    Service-->>User: progress planner

    Service->>Prompter: TaskSpec + user task + generator summary
    Prompter-->>Service: short Russian prompt additions
    Service-->>User: progress prompter

    Service->>Generator: LowCode generator prompt
    Generator-->>Service: candidate JSON
    Service-->>User: progress generation

    Service->>Validator: candidate
    Validator-->>Service: format/syntax/static/principle/rule reports
    Service->>Critic: validation reports
    Critic-->>Service: finalize
    Service-->>User: progress deterministic_validation

    Service-->>API: GenerateResponse
    API-->>User: final response
```

## Repair path

```mermaid
sequenceDiagram
    participant Service as GenerationService
    participant Generator as Generator model call
    participant Validator as Deterministic validators
    participant Critic as Critic report

    Service->>Validator: initial candidate
    Validator-->>Service: repairable finding
    Service->>Critic: validation reports
    Critic-->>Service: repair instruction

    loop repair_budget
        Service->>Generator: repair_generation prompt with current candidate + validation summary
        Generator-->>Service: repaired candidate
        Service->>Validator: repaired candidate
        Validator-->>Service: validation reports
        Service->>Critic: validation reports
        Critic-->>Service: finalize or repair
    end
```

## Notes

- `planner` and `prompter` are LLM-backed agent layers.
- `generator` is the only layer allowed to emit Lua.
- `prompter` returns only additions, not a full prompt.
- `repair_generation` goes directly to generator. There is no active `repair_prompter` stage in the current API path.
- `deterministic_validation` is not an LLM layer.
- `critic_report` decides whether to finalize or repair based on validator reports.
- Generator stages use truncation guard and temporary files when `eval_count >= num_predict`.
- `/generate/progress` streams progress events while the request is running; `/generate` returns one final JSON response.
