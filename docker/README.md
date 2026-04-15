# Docker Runtime

Docker Compose - рекомендуемый способ запустить `luaMTS` на машине проверяющего без ручной настройки Python, Lua tooling и Ollama.

## Что поднимается

`docker-compose.yml` запускает три сервиса:

- `ollama` - локальный Ollama server;
- `ollama-model-init` - одноразовая инициализация модели через `ollama pull` или `ollama create` из локального `.gguf`;
- `api` - FastAPI backend + CLI `luamts`.

API-образ собирается из [docker/api/Dockerfile](api/Dockerfile) и содержит:

- Python API;
- CLI;
- `stylua`;
- `luacheck`;
- `lua5.4`;
- validation/runtime tooling.

## Быстрый запуск

Windows PowerShell:

```powershell
docker compose up --build
```

macOS / Linux:

```bash
docker compose up --build
```

Первый запуск может быть долгим: `ollama-model-init` скачивает модель в volume `ollama-data`.

После старта:

- API: `http://127.0.0.1:8011`
- Ollama: `http://127.0.0.1:11434`

Проверка:

PowerShell:

```powershell
Invoke-RestMethod http://127.0.0.1:8011/health
docker compose exec api luamts doctor
```

macOS / Linux:

```bash
curl http://127.0.0.1:8011/health
docker compose exec api luamts doctor
```

## Генерация через CLI

Интерактивный режим:

```bash
docker compose exec api luamts
```

Одноразовый запрос:

```bash
docker compose exec api luamts generate --mode release --task "Из массива emails верни последний email." --context '{"wf":{"vars":{"emails":["a@example.com","b@example.com"]}}}'
```

Компактный release-like пресет без GPU pin:

```bash
docker compose exec api luamts generate --mode releaseSlim --task "Из массива emails верни последний email." --context '{"wf":{"vars":{"emails":["a@example.com","b@example.com"]}}}'
```

Debug-пример:

```bash
docker compose exec api luamts generate --mode debug --debug-trace --task "Из массива emails верни последний email." --context '{"wf":{"vars":{"emails":["a@example.com","b@example.com"]}}}'
```

## Порты

Если `8011` или `11434` уже заняты:

PowerShell:

```powershell
$env:API_PUBLISHED_PORT='18011'
$env:OLLAMA_PUBLISHED_PORT='21434'
docker compose up --build
```

macOS / Linux:

```bash
API_PUBLISHED_PORT=18011 OLLAMA_PUBLISHED_PORT=21434 docker compose up --build
```

## Модель

По умолчанию:

```text
OLLAMA_MODEL=qwen3.5:9b
```

Это официальный Ollama tag с quantization `Q4_K_M`. При первом запуске `ollama-model-init` выполняет `ollama pull qwen3.5:9b` внутри Docker volume `ollama-data`; прежняя дефолтная модель больше не нужна для Docker-контура.

Переопределение:

PowerShell:

```powershell
$env:OLLAMA_MODEL='qwen3.5:9b'
docker compose up --build
```

macOS / Linux:

```bash
OLLAMA_MODEL=qwen3.5:9b docker compose up --build
```

## Локальный GGUF

Если модель уже скачана как `.gguf`, можно создать Ollama model из локального файла.

PowerShell:

```powershell
$env:OLLAMA_MODEL='local-lua-model:q5'
$env:OLLAMA_LOCAL_GGUF_DIR='C:/Users/epitaph/Downloads'
$env:OLLAMA_LOCAL_GGUF_BASENAME='model.Q5_K_S.gguf'
docker compose up --build
```

macOS / Linux:

```bash
OLLAMA_MODEL=local-lua-model:q5 \
OLLAMA_LOCAL_GGUF_DIR=/home/user/Downloads \
OLLAMA_LOCAL_GGUF_BASENAME=model.Q5_K_S.gguf \
docker compose up --build
```

После успешного `ollama create` модель остаётся в volume `ollama-data`.

## Cloud guard

По умолчанию compose выставляет:

```text
OLLAMA_NO_CLOUD=1
```

Это конкурсный/release-safe режим: cloud inference запрещён.

Для локальной debug-разработки можно явно разрешить cloud:

PowerShell:

```powershell
$env:OLLAMA_NO_CLOUD='0'
docker compose up --build
docker compose exec api luamts generate --mode debug --model qwen3-coder:480b-cloud --allow-cloud-model --task "..."
```

macOS / Linux:

```bash
OLLAMA_NO_CLOUD=0 docker compose up --build
docker compose exec api luamts generate --mode debug --model qwen3-coder:480b-cloud --allow-cloud-model --task "..."
```

Cloud-tags запрещены в `release` и `releaseSlim` даже при `OLLAMA_NO_CLOUD=0`.

## Runtime options

Compose defaults:

```text
OLLAMA_NUM_CTX=4096
OLLAMA_NUM_PREDICT=256
OLLAMA_BATCH=1
OLLAMA_TEMPERATURE=0.7
OLLAMA_TOP_P=0.8
OLLAMA_TOP_K=20
OLLAMA_MIN_P=0.0
OLLAMA_PRESENCE_PENALTY=1.5
OLLAMA_REPEAT_PENALTY=1.0
OLLAMA_PARALLEL=1
```

API отправляет в Ollama top-level `think: false` для `/api/generate` и `/api/chat`, чтобы Qwen3.5 работала в non-thinking режиме.

Режимы runtime:

- `release` - фиксированные compact defaults плюс `num_gpu=-1`, чтобы не использовать CPU offload;
- `releaseSlim` - те же compact defaults, но без `num_gpu`, поэтому CPU offload не блокируется;
- `debug` - без release-only GPU pin, с возможностью менять runtime options и локально диагностировать cloud-tags.

## Проверка compose-файла

Без запуска контейнеров:

```bash
docker compose config
```

Полный smoke:

```bash
docker compose up --build
docker compose exec api luamts doctor
docker compose exec api luamts generate --mode release --task "Из массива emails верни последний email." --context '{"wf":{"vars":{"emails":["a@example.com","b@example.com"]}}}'
docker compose down
```

Эти команды одинаковы для Windows PowerShell, macOS и Linux, кроме синтаксиса переменных окружения, который указан выше.

