# luaMTS LocalScript Agent

`luaMTS` - локальный агент для генерации LowCode/LocalScript-совместимого Lua-кода.

Проект не является универсальным coding assistant. Он решает узкую задачу: по пользовательскому описанию и JSON-контексту сгенерировать Lua-скрипт для LowCode, проверить результат детерминированными валидаторами и при необходимости сделать ограниченный repair.

## Что делает проект

- принимает задачу на русском или английском языке;
- понимает LowCode-контекст `wf.vars` и `wf.initVariables`;
- генерирует Lua в контракте `lua{...}lua` внутри JSON object;
- запрещает JsonPath, markdown, пояснения вокруг JSON, `print/debug output` и `error()`;
- проверяет результат через validation pipeline;
- делает bounded repair без бесконечного цикла;
- показывает debug trace по слоям pipeline;
- запускается локально через Docker Compose и Ollama.

## Текущий pipeline

Основной API path: `POST /generate`.

Фактический happy path:

```text
request_received
-> planner
-> prompter
-> generation
-> deterministic_validation
-> response_ready
```

Если deterministic validation нашла repairable ошибку, включается ограниченный repair loop:

```text
deterministic_validation
-> repair_generation
-> deterministic_validation
-> response_ready
```

По умолчанию repair budget равен `2`: первая генерация плюс один repair-generation проход. Значение можно менять в CLI/debug-запросах.

### Роли слоёв

`planner` строит компактный `TaskSpec`: операция, output mode, input roots, expected shape, риски и edge cases. Planner не генерирует Lua.

`prompter` не переписывает основной generator prompt. Он добавляет короткие русские подсказки к уже готовому LowCode-контракту. Добавки фильтруются: подсказки вида "бросай ошибку", `error()` и похожие конфликтующие инструкции не попадают в prompt генератора.

`generator` получает полный LowCode prompt и возвращает только JSON object. Каждое Lua-значение должно быть строкой вида:

```json
{
  "result": "lua{return wf.vars.emails[#wf.vars.emails]}lua"
}
```

`truncation guard` защищает generator от обрезки на `num_predict=256`. Если модель вернула ровно лимит токенов, частичный ответ временно сохраняется, а генератор получает continuation prompt с уже выведенным фрагментом. Так продолжается до ответа короче лимита или до внутреннего лимита продолжений. Временные файлы удаляются после финального статуса задачи.

`deterministic_validation` включает форматные, синтаксические, статические, principle/domain и rule checks. Для LowCode JSON сначала проверяется валидность JSON и wrapper `lua{...}lua`, затем извлекаются Lua-сегменты и проверяются Lua-правила.

`critic_report` в текущем контуре не является отдельной LLM-генерацией ответа. Он структурирует итог validation pass/fail и говорит pipeline: finalize или repair.

## LowCode Lua contract

Generator обязан соблюдать эти правила:

- ответ только JSON object;
- Lua внутри JSON-строк: `lua{<Lua код>}lua`;
- Lua-код возвращает значение через `return`, если задача просит получить значение;
- доступ к данным только напрямую через Lua;
- LowCode-переменные лежат в `wf.vars`;
- входные `variables` лежат в `wf.initVariables`;
- JsonPath запрещён;
- `print`, debug output, markdown и пояснения запрещены;
- `error()` запрещён: для некорректного входа надо вернуть `nil`, `false` или пустую строку по смыслу задачи;
- новый массив создаётся через `_utils.array.new()`;
- существующий массив при необходимости помечается через `_utils.array.markAsArray(arr)`.

Разрешённое базовое подмножество Lua ориентировано на:

- `nil`, `boolean`, `number`, `string`, `array`, `table`, `function`;
- `if...then...else`;
- `while...do...end`;
- `for...do...end`;
- `repeat...until`.

## Быстрый запуск через Docker

Это рекомендуемый путь для жюри и проверки на любой ОС.

### Требования

- Docker Desktop или Docker Engine с Docker Compose v2;
- доступ в интернет на первом запуске, если модель скачивается через `ollama pull`;
- свободные порты `8011` для API и `11434` для Ollama, либо переопределённые порты.

### Windows PowerShell

```powershell
git clone <repo-url>
cd luaMTS
docker compose up --build
```

Проверка API во втором терминале:

```powershell
Invoke-RestMethod http://127.0.0.1:8011/health
docker compose exec api luamts doctor
```

Интерактивный CLI:

```powershell
docker compose exec api luamts
```

Одноразовая генерация:

```powershell
docker compose exec api luamts generate --mode release --task "Из массива emails верни последний email." --context '{"wf":{"vars":{"emails":["a@example.com","b@example.com"]}}}'
```

Остановка:

```powershell
docker compose down
```

### macOS / Linux

```bash
git clone <repo-url>
cd luaMTS
docker compose up --build
```

Проверка API во втором терминале:

```bash
curl http://127.0.0.1:8011/health
docker compose exec api luamts doctor
```

Интерактивный CLI:

```bash
docker compose exec api luamts
```

Одноразовая генерация:

```bash
docker compose exec api luamts generate --mode release --task "Из массива emails верни последний email." --context '{"wf":{"vars":{"emails":["a@example.com","b@example.com"]}}}'
```

Остановка:

```bash
docker compose down
```

### Если порты заняты

Windows PowerShell:

```powershell
$env:API_PUBLISHED_PORT='18011'
$env:OLLAMA_PUBLISHED_PORT='21434'
docker compose up --build
```

macOS / Linux:

```bash
API_PUBLISHED_PORT=18011 OLLAMA_PUBLISHED_PORT=21434 docker compose up --build
```

После этого API будет доступен на `http://127.0.0.1:18011`.

### Выбор модели

По умолчанию compose использует локальную модель:

```text
qwen2.5-coder:3b
```

Переопределение:

Windows PowerShell:

```powershell
$env:OLLAMA_MODEL='qwen2.5-coder:3b'
docker compose up --build
```

macOS / Linux:

```bash
OLLAMA_MODEL=qwen2.5-coder:3b docker compose up --build
```

Cloud-tags вида `*-cloud` запрещены в release mode. Они допустимы только для debug-разработки с явным `--allow-cloud-model` и не являются конкурсным runtime.

## Локальный запуск без Docker

Этот путь удобен для разработки.

1. Установить Python 3.12.
2. Установить и запустить Ollama.
3. Установить зависимости API.

PowerShell:

```powershell
cd apps/api
python -m pip install -e .
$env:PYTHONPATH='.;..\..'
$env:OLLAMA_BASE_URL='http://127.0.0.1:11434'
python -m uvicorn main:app --host 127.0.0.1 --port 8011
```

macOS / Linux:

```bash
cd apps/api
python -m pip install -e .
export PYTHONPATH=".:../.."
export OLLAMA_BASE_URL="http://127.0.0.1:11434"
python -m uvicorn main:app --host 127.0.0.1 --port 8011
```

## CLI

Команды:

```text
luamts doctor
luamts generate
luamts bench
luamts vram-check
luamts
```

Интерактивный режим поддерживает slash-команды:

```text
/debug
/release
/model qwen2.5-coder:3b
/model n
/allow-cloud on
/repair-budget 2
/with-api
/without-api
/exit
```

`/model n` возвращает стандартную модель.

В debug mode CLI показывает прогресс по слоям во время работы API. В release mode отображается минимальный progress/spinner и итоговый статус.

CLI умеет принимать многострочную вставку JSON-контекста в интерактивном вводе. Человекочитаемый вывод результата разворачивает `\n` в реальные строки, но debug JSON печатается без изменения JSON-экранирования.

## API пример

PowerShell:

```powershell
$body = @{
  task_text = 'Из полученного списка email получи последний.'
  provided_context = '{"wf":{"vars":{"emails":["user1@example.com","user2@example.com"]}}}'
  debug = $true
  mode = 'debug'
  language = 'ru'
  repair_budget = 2
} | ConvertTo-Json -Depth 20 -Compress

Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8011/generate' -ContentType 'application/json; charset=utf-8' -Body $body | ConvertTo-Json -Depth 40
```

curl:

```bash
curl -s http://127.0.0.1:8011/generate \
  -H 'Content-Type: application/json' \
  -d '{"task_text":"Из полученного списка email получи последний.","provided_context":"{\"wf\":{\"vars\":{\"emails\":[\"user1@example.com\",\"user2@example.com\"]}}}","debug":true,"mode":"debug","language":"ru","repair_budget":2}' \
  | python -m json.tool
```

Для live progress можно использовать:

```text
POST /generate/progress
```

Endpoint отдаёт NDJSON events: `progress`, затем `final` или `error`.

## Benchmark

В проекте есть benchmark-артефакты и runner-скрипты.

Последний прогон:

```text
artifacts/benchmark_runs/7_progon/
```

Итог `7_progon` на 50 выбранных задач из файла с 300 задачами:

```text
total: 50
status_counts: {'passed': 50}
passed_without_hint: 47
passed_with_hint: 3
passed_on_generation_counts: {'1': 48, '2': 2}
```

Runner:

```powershell
python scripts\run_lua_7_progon_benchmark.py
```

Сначала каждая задача запускается без сильной подсказки. Если validation не прошла, скрипт делает вторую benchmark-попытку с сильной подсказкой. Эталонное решение сохраняется в отчёт только для анализа и не отправляется в API.

## Проверка разработки

Релевантные тесты:

```powershell
python -m pytest apps\api\tests packages\benchmark\tests -q
```

Проверка compose-файла без запуска:

```powershell
docker compose config
```

## Документы

- [docs/how_validation_work.md](docs/how_validation_work.md) - фактическое состояние validation pipeline.
- [docs/AGENT_PIPELINE_SEQUENCE.md](docs/AGENT_PIPELINE_SEQUENCE.md) - sequence diagram текущего pipeline.
- [docker/README.md](docker/README.md) - Docker-поставка и варианты запуска.
- [docs/CLI_CLIENT.md](docs/CLI_CLIENT.md) - CLI режимы и UX.
- [docs/CONSTRAINTS.md](docs/CONSTRAINTS.md) - ограничения проекта.
- [docs/OUTPUT_MODES.md](docs/OUTPUT_MODES.md) - режимы вывода.
- [docs/TASK_ARCHETYPES.md](docs/TASK_ARCHETYPES.md) - архетипы задач.

## Ограничения

- Конкурсный/release runtime должен быть локальным.
- Внешние AI API запрещены.
- Ollama cloud-tags допустимы только в debug-разработке и только явно.
- Release mode фиксирует компактные параметры: `num_ctx=4096`, `num_predict=256`, `batch=1`, `parallel=1`.
- Debug mode может менять модель, runtime options и cloud guard.

## Структура

```text
apps/api/                  FastAPI backend and CLI
packages/orchestrator/     planner/prompter/generator prompt assembly
packages/validators/       deterministic validation pipeline
packages/benchmark/        benchmark helpers and tests
benchmark/                 benchmark datasets
scripts/                   benchmark/import/report scripts
docker/                    Docker image helpers
docs/                      architecture and operation docs
artifacts/benchmark_runs/  generated benchmark reports
```
