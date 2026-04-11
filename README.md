# LocalScript Agent

Базовый репозиторий проекта локального AI-агента для генерации **LocalScript-совместимого Lua-кода**.

На текущий момент в репозитории уже закрыты этапы `S-0`, `S-1`, `S-3`, `S-4`, `S-5`, `S-6`; этап `S-2` остаётся `deferred`. В проекте уже есть локальный backend, domain adapter, validator / critic / repair loop и debug trail для проверки качества generation path.

## Что строится

Проект строится как **узкий локальный агент**, а не как general-purpose coding assistant.

Целевая система должна:

- работать полностью локально;
- использовать открытую модель через `Ollama`;
- укладываться в лимит `8 GB VRAM` без CPU offload;
- генерировать не абстрактный Lua, а ответ под **LocalScript-контур**;
- делать не только генерацию, но и проверку / repair;
- воспроизводиться по документированной инструкции;
- не использовать внешние AI API в runtime.

## Текущее состояние

Сейчас в репозитории уже собран рабочий локальный MVP-контур:

- локальный API поднимает `/health` и `/generate`;
- generation идёт только через локальный `Ollama`;
- domain adapter принуждает ответ к LocalScript-aware output modes;
- quality layer выполняет format checks, path checks, forbidden-pattern checks и archetype-specific checks;
- `critic` переводит validator findings в repair / clarification / finalize решения;
- repair loop ограничен и поддерживает deterministic fixes для части типовых format / wrapper / almost-JSON ошибок;
- debug mode возвращает prompt package, raw model calls, validation passes и critic reports.

Что **ещё не реализовано**:

- retrieval;
- benchmark harness;
- UI;
- рабочий `docker-compose.yml` для полноценного Docker-first runtime.

Иными словами: локальный backend и quality contour уже работают, но проект ещё не доведён до финального конкурсного уровня по retrieval, evaluation, Docker-first поставке и сдачным артефактам.

## Жёсткие ограничения проекта

В проекте запрещено:

- использовать `OpenAI`, `Anthropic` и другие внешние AI API;
- выносить данные, код, промпты и контекст за пределы локального контура;
- использовать облачный inference;
- закладывать ручные шаги, которые не отражены в документации;
- строить “универсального AI-ассистента” вместо узкого решения под кейс.

В проекте обязательно сохраняются:

- локальность;
- воспроизводимость;
- честный лимит `<= 8 GB VRAM`;
- запуск модели через `Ollama`;
- ориентация на **LocalScript-специфичный** Lua.

Полный набор ограничений вынесен в [docs/CONSTRAINTS.md](docs/CONSTRAINTS.md).

## MVP в одном абзаце

Минимально жизнеспособная версия проекта должна принимать задачу на русском или английском языке, локально генерировать LocalScript-совместимый Lua-код через `Ollama`, прогонять результат через проверяемый validation layer, выполнять хотя бы один полезный шаг repair или clarification и оставаться полностью воспроизводимой без внешних AI API.

Подробный definition of done вынесен в [docs/MVP_DEFINITION.md](docs/MVP_DEFINITION.md).

## Целевой архитектурный контур

Целевая система проектируется из следующих слоёв:

1. `Input Layer` — принимает задачу и контекст.
2. `Task Understanding Layer` — определяет archetype и режим ответа.
3. `Model Invocation Layer` — вызывает локальную модель через `Ollama`.
4. `Domain Adapter Layer` — принуждает ответ к LocalScript-правилам.
5. `Validation Layer` — проверяет формат, структуру и запрещённые паттерны.
6. `Critic & Repair Layer` — ограниченно исправляет ошибочные ответы.
7. `Output Layer` — возвращает финальный ответ и trace шагов.

Эта архитектура уже частично реализована в backend: `Model Invocation Layer`, `Domain Adapter Layer`, `Validation Layer`, `Critic & Repair Layer` и `Output Layer` присутствуют в рабочем контуре.

## Стартовая структура репозитория

```text
.
├── apps/
│   ├── api/
│   └── ui/
├── packages/
│   ├── benchmark/
│   ├── model-adapter/
│   ├── orchestrator/
│   ├── retrieval/
│   ├── shared/
│   ├── task-archetypes/
│   └── validators/
├── knowledge/
│   ├── archetypes/
│   ├── examples/
│   └── templates/
├── docs/
│   ├── C4/
│   ├── CONSTRAINTS.md
│   ├── MVP_DEFINITION.md
│   └── PROJECT_STATUS.md
├── docker/
├── scripts/
├── .env.example
├── AGENTS.md
├── OSP.md
└── PROJECT_STATUS_full.md
```

Эта структура фиксирует, **куда будут размещаться** дальнейшие этапы разработки. Наличие каталогов на `S-0` не означает, что соответствующие подсистемы уже реализованы.

## Документы проекта

- [PROJECT_STATUS_full.md](PROJECT_STATUS_full.md) — полный source of truth по этапам, рискам и scope.
- [docs/PROJECT_STATUS.md](docs/PROJECT_STATUS.md) — краткий статус проекта для быстрого входа.
- [docs/CONSTRAINTS.md](docs/CONSTRAINTS.md) — зафиксированные ограничения конкурса и архитектурные guardrails.
- [docs/MVP_DEFINITION.md](docs/MVP_DEFINITION.md) — definition of done для минимально жизнеспособной версии.
- [OSP.md](OSP.md) — исходные опорные материалы по выбору модели и архитектурной линии.
- [AGENTS.md](AGENTS.md) — правила узкой stage-driven работы по репозиторию.

## Runtime

Текущий локальный runtime:

- текущий базовый кандидат модели: `qwen2.5-coder:3b`;
- `num_ctx=4096`;
- `num_predict=256`;
- `batch=1`;
- `parallel=1`;
- GPU budget: `<= 8 GB VRAM`.

Важно: это всё ещё **не финальный модельный выбор**. Финальная фиксация модели должна быть сделана на этапе `S-2`, после bake-off внутри уже собранного agent pipeline.

## Локальный запуск

Запуск `Ollama`:

```powershell
ollama serve
```

Запуск API:

```powershell
cd apps/api
$env:PYTHONPATH='.'
$env:OLLAMA_BASE_URL='http://127.0.0.1:11434'
python -m uvicorn main:app --host 127.0.0.1 --port 8011
```

Пример запроса в debug mode:

```powershell
$body=@{task_text='Из полученного списка email получи последний.';provided_context='{"wf":{"vars":{"emails":["user1@example.com","user2@example.com"]}}}';archetype='simple_extraction';output_mode='raw_lua';input_roots=@('wf.vars.emails');risk_tags=@('array_indexing','empty_array');debug=$true}|ConvertTo-Json -Depth 10 -Compress
Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8011/generate' -ContentType 'application/json; charset=utf-8' -Body $body | ConvertTo-Json -Depth 20
```

В debug-ответе возвращаются:

- `trace`
- `validator_report`
- `critic_report`
- `repair_count`
- `clarification_count`
- `debug.prompt_package`
- `debug.model_calls`
- `debug.validation_passes`

## Docker и воспроизводимость

Полноценная Docker-first поставка всё ещё относится к этапу `S-10`.

Сейчас в репозитории есть [docker/README.md](docker/README.md), но рабочего `docker-compose.yml` для финального локального рантайма ещё нет.

## Что дальше

Ближайшие этапы roadmap:

1. `S-7` — локальная база знаний, шаблоны и retrieval.
2. `S-8` — UI как дополнительный demo-friendly слой.
3. `S-9` — evaluation harness и regression runner.
4. `S-10` — Docker-first runtime, безопасность и воспроизводимость.

## Главный принцип

Проект выигрывает не там, где “модель самая умная”, а там, где:

- домен правильно сужен;
- ограничения зафиксированы письменно;
- validation layer обязателен;
- agentic uplift полезен и ограничен;
- локальность и воспроизводимость не нарушаются.
