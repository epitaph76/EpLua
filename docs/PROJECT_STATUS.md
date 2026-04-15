# Project Status

Краткий статус `luaMTS`.

## Текущее состояние

Проект имеет рабочий локальный backend, CLI, Docker Compose runtime и active validation pipeline.

Фактический API pipeline:

```text
planner -> prompter -> generator -> deterministic_validation -> semantic_validation -> optional repair_generation
```

Ключевые свойства:

- `/health`, `/generate`, `/generate/progress`;
- `/plan` с one-shot clarifier/planner preflight;
- LowCode JSON contract `lua{...}lua`;
- planner/prompter agent layers;
- semantic critic после deterministic validation;
- generator truncation guard на `num_predict`;
- deterministic validators;
- bounded repair budget;
- assisted repair после exhausted repair loop;
- CLI с режимами `release`, `releaseSlim`, `debug` и one-shot `/plan`;
- Docker Compose runtime с Ollama + API;
- benchmark runner и артефакты `7_progon`.

## Что уже работает

- Локальная генерация через Ollama.
- Release mode с запретом cloud model tags.
- releaseSlim как compact release-like preset без GPU pin.
- Debug mode с возможностью cloud tags только через явный `--allow-cloud-model`.
- Live progress по слоям API.
- Human-readable CLI output без повреждения raw candidate.
- Benchmark scripts и отчёты в `artifacts/benchmark_runs/`.

## Последний benchmark

`artifacts/benchmark_runs/7_progon/`

```text
total: 50
status_counts: {'passed': 50}
passed_without_hint: 47
passed_with_hint: 3
passed_on_generation_counts: {'1': 48, '2': 2}
```

## Остающиеся зоны развития

- Финальный локальный model bake-off под ограничение VRAM.
- Runtime behavioral validation для задач, где можно исполнить Lua на fixture.
- Более строгий semantic/runtime scoring benchmark.
- Финальные конкурсные материалы: видео, презентация, проверка инструкции на чистых машинах.

## Основной запуск

```bash
docker compose up --build
docker compose exec api luamts doctor
docker compose exec api luamts
```

Подробности:

- [../README.md](../README.md)
- [how_validation_work.md](how_validation_work.md)
- [../docker/README.md](../docker/README.md)
