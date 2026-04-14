# MVP Definition

Этот документ фиксирует минимально жизнеспособную версию `luaMTS` и её текущее состояние.

## MVP goal

MVP должен доказать, что проект работает как локальная агентная система для LowCode Lua:

```text
task -> agent pipeline -> Lua candidate -> validation -> repair/final output
```

## MVP requirements

MVP считается выполненным, если:

1. Сервис запускается локально по документированной инструкции.
2. Runtime модели идёт через Ollama.
3. Release path не использует внешние AI API.
4. Есть API `/generate`.
5. Есть CLI.
6. Ответ соответствует LowCode contract `lua{...}lua`.
7. Есть active validation layer.
8. Есть bounded repair.
9. Есть debug trace.
10. Есть Docker Compose путь запуска.
11. Есть benchmark/log artifacts для проверки качества.

## Current status

MVP core реализован:

- FastAPI backend;
- Ollama adapter;
- planner/propmter/generator pipeline;
- deterministic validation;
- repair-generation loop;
- CLI;
- Docker Compose;
- benchmark runner.

Остаётся усилить:

- финальный локальный model bake-off;
- runtime behavioral validation;
- конкурсные презентационные материалы;
- проверку Docker-инструкции на нескольких чистых машинах.

## Non-goals

В MVP не входят:

- сложный UI;
- облачная инфраструктура;
- general-purpose coding assistant behavior;
- неограниченная автономия;
- подмешивание benchmark expected solution в prompt.
