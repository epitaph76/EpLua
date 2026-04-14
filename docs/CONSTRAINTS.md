# Constraints

Этот документ фиксирует инженерные ограничения `luaMTS`.

## Project boundary

Проект строится как локальный агент для генерации LowCode/LocalScript-совместимого Lua-кода.

Проект не строится как:

- универсальный coding assistant;
- чат-обёртка вокруг внешнего AI API;
- UI-first продукт без проверяемого backend pipeline;
- benchmark, который подмешивает эталонные решения в prompt.

## Release/runtime constraints

Release/demo контур обязан:

- работать локально;
- запускать модель через Ollama;
- запрещать cloud model tags;
- быть воспроизводимым через Docker Compose;
- использовать компактные runtime-параметры:
  - `num_ctx=4096`;
  - `num_predict=256`;
  - `batch=1`;
  - `parallel=1`;
- не использовать CPU offload в release mode (`num_gpu=-1`);
- выполнять validation pipeline перед выдачей статуса `passed`.

## Debug constraints

Debug mode может:

- менять model tag;
- менять runtime options;
- включать cloud model tags только с явным `--allow-cloud-model`;
- не применять release-only `num_gpu=-1`;
- возвращать расширенный debug trace.

Debug mode не является конкурсным runtime.

## Forbidden

Запрещено:

- использовать OpenAI, Anthropic и другие внешние AI API;
- полагаться на недокументированные ручные шаги;
- молча менять пользовательский task/context;
- пропускать candidate как `passed`, если deterministic validation не прошла;
- использовать JsonPath вместо прямого доступа Lua;
- генерировать `error()` в LowCode JSON output;
- использовать benchmark expected solution как prompt context.

## LowCode hard rules

- LowCode variables: `wf.vars`.
- Init variables: `wf.initVariables`.
- Script wrapper: `lua{...}lua`.
- New arrays: `_utils.array.new()`.
- Existing array mark: `_utils.array.markAsArray(arr)`.
- Allowed baseline constructs: `if`, `while`, `for`, `repeat`.

## Documentation/reproducibility

Каждая проверяющая машина должна иметь простой путь:

```bash
docker compose up --build
docker compose exec api luamts doctor
docker compose exec api luamts generate --mode release --task "..." --context '{...}'
```

Если этот путь ломается, это release blocker.
