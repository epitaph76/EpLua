# Docker Baseline

Каталог `docker/` создан на этапе `S-0` как baseline-заготовка под будущую Docker-first поставку.

Сейчас здесь есть ранний runtime slice:

- `docker-compose.yml` в корне проекта;
- `docker/api/Dockerfile` для API-образа с уже встроенными `stylua`, `luacheck`, `lua5.4`;
- связка `ollama` + `api` для локальной проверки;
- init-сервис, который либо делает `ollama pull`, либо создаёт модель из локального `.gguf`.
- CLI `luamts` внутри API-контейнера для `generate`, `doctor`, `bench`, `vram-check`.

При этом здесь **ещё нет**:

- GPU-aware runtime профилей;
- production-hardening;
- полноценного smoke/e2e сценария;
- финальной deployment-конфигурации.

То есть это docker-preview для воспроизводимой локальной проверки, а не полное закрытие `S-10`.

## Как сейчас инициализируется модель

По умолчанию compose использует сценарий `pull`:

- `OLLAMA_MODEL=qwen2.5-coder:3b`
- `ollama-model-init` ждёт готовности `ollama`
- затем выполняет `ollama pull <tag>`

Для локального `.gguf` используется тот же init-сервис, но с двумя переменными:

- `OLLAMA_LOCAL_GGUF_DIR`
- `OLLAMA_LOCAL_GGUF_BASENAME`

Если `OLLAMA_LOCAL_GGUF_BASENAME` задан, сервис:

- монтирует каталог с моделью в `/models`
- создаёт временный `Modelfile`
- выполняет `ollama create $OLLAMA_MODEL -f <temp-modelfile>`
- сохраняет готовую модель в volume `ollama-data`

Пример для Windows PowerShell:

```powershell
$env:OLLAMA_MODEL='qwen3.5-9b:local-q5ks'
$env:OLLAMA_LOCAL_GGUF_DIR='C:/Users/epitaph/Downloads'
$env:OLLAMA_LOCAL_GGUF_BASENAME='Qwen3.5-9B.Q5_K_S.gguf'
$env:OLLAMA_PUBLISHED_PORT='21434'
$env:API_PUBLISHED_PORT='18011'
docker compose up --build
```

После первого успешного импорта модель останется в `ollama-data`, и повторный старт сможет её переиспользовать без нового `create`.

Параметры `OLLAMA_PUBLISHED_PORT` и `API_PUBLISHED_PORT` нужны, если локальные процессы уже занимают `11434` и `8011`.

Основной запуск остаётся одной командой:

```powershell
docker compose up --build
```

После старта CLI запускается через API-контейнер:

```powershell
docker compose exec api luamts doctor
docker compose exec api luamts generate --mode release --task "Из массива emails верни последний email."
docker compose exec api luamts
```

В интерактивном режиме plain text отправляется как задача генерации, а режимы меняются slash-командами: `/debug`, `/release`, `/model <tag>`, `/allow-cloud on|off`, `/with-api`, `/without-api`, `/exit`.

## Что уже зафиксировано

- поставка проекта должна быть локальной;
- runtime модели обязан идти через `Ollama`;
- внешний AI inference запрещён;
- запуск должен быть воспроизводимым;
- финальная сдача должна поддерживать простой и документированный запуск.

## Что должно появиться позже

На этапе `S-10` здесь должны быть оформлены:

- Dockerfile(ы) для backend и связанных сервисов;
- `docker-compose.yml`;
- описание GPU-требований;
- фиксированный `ollama pull <tag>`;
- smoke path для локальной проверки;
- deployment и security-документация.
