# luaMTS Presentation

## 1. luaMTS
- Локальный агент для генерации LowCode/LocalScript-совместимого Lua-кода.
- Формат решения: CLI + FastAPI API + локальный Ollama runtime + validation pipeline.
- Цель: выдавать пригодный к использованию Lua-код, а не просто "ответ модели".
- Конкурсный фокус: качество кода, агентность итераций, локальность и воспроизводимость.

## 2. Проблема
- LowCode и LocalScript-задачи часто формулируются на естественном языке, но требуют точного Lua-кода.
- Ошибки типичны и дорогие: неверные `wf.vars` / `wf.initVariables`, сломанный `lua{...}lua` wrapper, JsonPath вместо прямого Lua, потеря array semantics.
- One-shot LLM-генерация даёт правдоподобный, но часто небезопасный и невалидный код.
- Для enterprise-сценариев критичны локальность, приватность данных и воспроизводимый запуск без внешних AI API.

## 3. Что мы построили
- Узкий прикладной агент, а не универсальный coding assistant.
- Вход: задача на русском или английском + optional JSON context.
- Выход: LowCode JSON object, где Lua лежит в строках вида `lua{...}lua`.
- Система не только генерирует, но и валидирует, чинит и при необходимости задаёт уточняющий вопрос.

## 4. Продуктовая ценность
- Ускоряет написание прикладных Lua-скриптов для LowCode-процессов.
- Снижает объём ручной переписки после генерации.
- Уменьшает риск ошибок в automation-коде до выхода в production-процесс.
- Подходит для контуров, где данные нельзя отправлять во внешние AI-сервисы.

## 5. Пользовательский сценарий
- Пользователь вводит задачу и контекст через CLI или HTTP API.
- `/plan` может заранее запросить уточнение, если недостаточно данных или неоднозначны data roots.
- `/generate` запускает полный pipeline и возвращает `passed`, `repaired` или `failed`.
- CLI показывает живой progress по слоям pipeline, debug trace и assisted-repair summary.

## 6. Архитектура решения
- Пользователь -> CLI / API -> `GenerationService` -> planner -> prompter -> generator -> validators -> critic.
- Внешний runtime модели: только локальный Ollama.
- Локальные данные: prompt templates, knowledge assets, benchmark datasets, reports.
- Docker Compose поднимает три сервиса: `ollama`, `ollama-model-init`, `api`.

## 7. Где здесь агентность
- Генерация не одношаговая: `request -> plan -> prompt -> generate -> validate -> repair/finalize`.
- `planner` строит компактный `TaskSpec` и может инициировать clarification.
- `prompter` усиливает prompt, не нарушая LowCode hard rules.
- `critic` принимает решение: финализировать, отправить в repair или запросить уточнение.
- После исчерпания auto-repair budget система умеет перейти в assisted repair.

## 8. Как мы обеспечиваем качество Lua-кода
- Жёсткий LowCode contract: только JSON object, строки `lua{...}lua`, прямой доступ через Lua, без `print`, markdown и `error()`.
- Deterministic validation pipeline проверяет format, syntax, static rules и principle/domain rules.
- Semantic critic запускается только после успешной deterministic validation.
- Truncation guard защищает generator от обрезки на `num_predict=256`.
- Release path не может выдать `passed`, если validation не прошла.

## 9. Локальность, приватность, воспроизводимость
- Release/demo path работает только локально через Ollama.
- Внешние AI API запрещены проектными ограничениями.
- Cloud model tags запрещены в `release` и `releaseSlim`.
- One-command deployment: `docker compose up --build`.
- Проверяемый smoke path: `/health`, `luamts doctor`, `luamts generate --mode release ...`.

## 10. Конкретные runtime-решения
- Базовая release-модель в Docker story: `qwen3.5:9b`.
- Компактные release-параметры: `num_ctx=4096`, `num_predict=256`, `batch=1`, `parallel=1`, `num_gpu=-1`.
- `releaseSlim` сохраняет компактный контур, но не фиксирует `num_gpu=-1`.
- API работает в non-thinking режиме через `think: false` в Ollama `/api/generate` и `/api/chat`.

## 11. Результаты и измеримые сигналы
- Последний benchmark run: `artifacts/benchmark_runs/7_progon/`.
- На выборке из 50 задач: `50/50 passed`.
- `48` задач прошли на первой генерации.
- Ещё `2` задачи прошли на второй попытке.
- `47` задач прошли без сильной benchmark-подсказки, `3` - с подсказкой.

## 12. Почему решение хорошо ложится на критерии жюри
- Качество кода: domain-specific contract + validation + bounded repair вместо слепого one-shot.
- Агентность: clarification, feedback loop, repair-generation и trace по стадиям.
- Локальность: Docker + Ollama + local-only release path.
- Воспроизводимость: documented setup, benchmark artifacts, CLI/API smoke path.

## 13. Ограничения и следующий шаг
- UI пока не является центром продукта; основной контур уже работает через CLI и API.
- Retrieval и feedback capture подготовлены архитектурно, но ещё не являются главным источником качества.
- Следующий инженерный шаг: финальный bake-off локальной модели и усиление runtime behavioral validation.
- Главный результат уже сейчас: собранная локальная агентная система для генерации и проверки LowCode Lua.
