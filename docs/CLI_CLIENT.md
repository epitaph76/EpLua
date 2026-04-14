# CLI Client

`luamts` - тонкий CLI поверх API и Ollama runtime. Он не является вторым orchestrator: основной pipeline живёт в API.

## Режимы

### Release

Release mode нужен для демо и проверки.

Свойства:

- cloud model tags запрещены;
- `--allow-cloud-model` запрещён;
- runtime options фиксированы компактно;
- API запускает полный validation pipeline;
- вывод минимальный: статус и результат.

Параметры:

```text
num_ctx=4096
num_predict=256
batch=1
parallel=1
num_gpu=-1
```

`num_gpu=-1` применяется только в release mode, чтобы не использовать CPU offload.

### Debug

Debug mode нужен для разработки, анализа prompt package, model calls, validator reports и repair behavior.

Свойства:

- можно менять model tag;
- можно менять `num_ctx`, `num_predict`, `batch`, `temperature`;
- можно разрешить cloud tags через `--allow-cloud-model`;
- GPU-only ограничение release mode не применяется;
- выводится live progress по слоям pipeline.

Пример:

```powershell
luamts generate --mode debug --model qwen3-coder:480b-cloud --allow-cloud-model --debug-trace --task "..."
```

## Команды

```text
luamts doctor
luamts generate
luamts bench
luamts vram-check
luamts
```

## Interactive mode

Запуск:

```powershell
luamts
```

Slash-команды:

```text
/debug
/release
/model <tag>
/model n
/allow-cloud on
/allow-cloud off
/repair-budget <number>
/with-api
/without-api
/exit
```

`/model n` возвращает стандартную модель.

`/repair-budget 2` задаёт количество generator-pass попыток в API quality loop. По умолчанию используется `2`.

## Multiline input

Интерактивный ввод поддерживает многострочную вставку JSON-контекста. Это нужно, чтобы можно было вставить большой объект:

```text
Преобразуй DATUM и TIME в ISO 8601.
{
  "wf": {
    "vars": {
      "json": {
        "IDOC": {
          "ZCDF_HEAD": {
            "DATUM": "20231015",
            "TIME": "153000"
          }
        }
      }
    }
  }
}
```

CLI отправит задачу в API как один запрос.

## With API / without API

`with-api` - основной режим. Запрос идёт в `/generate` или `/generate/progress`, где работают:

```text
planner -> prompter -> generator -> deterministic_validation -> optional repair_generation
```

`without-api` - прямой вызов Ollama для диагностики prompt/model behavior. Validation pipeline в этом режиме не запускается.

## Output rendering

CLI различает:

- raw candidate для API/validator;
- human view для пользователя.

Основной результат печатается человекочитаемо:

- `\n` разворачивается в строки;
- `\"` показывается как `"`;
- Rich markup отключён, чтобы Lua-индексы вида `[#items]` не ломались.

Debug JSON печатается без глобальной замены `\n`, чтобы отчёты `model_calls` и `validation_passes` оставались пригодны для анализа.

## Live progress

В debug mode CLI печатает слои во время работы API:

```text
Debug progress:
  слой 1: request_received прошёл
  слой 2: planner прошёл
  слой 3: prompter прошёл
  слой 4: generation прошёл
  слой 5: deterministic_validation прошёл
  слой 6: response_ready прошёл
```

В release mode используется минимальный индикатор ожидания и итоговый статус.

## Docker usage

CLI доступен внутри API-контейнера:

```bash
docker compose exec api luamts doctor
docker compose exec api luamts generate --mode release --task "Из массива emails верни последний email." --context '{"wf":{"vars":{"emails":["a@example.com","b@example.com"]}}}'
docker compose exec api luamts
```

## Debug cloud example

Cloud mode не является release-сценарием. Для локальной диагностики:

PowerShell:

```powershell
$env:OLLAMA_NO_CLOUD='0'
luamts generate --mode debug --model qwen3-coder:480b-cloud --allow-cloud-model --task "..."
```

macOS / Linux:

```bash
OLLAMA_NO_CLOUD=0 luamts generate --mode debug --model qwen3-coder:480b-cloud --allow-cloud-model --task "..."
```
