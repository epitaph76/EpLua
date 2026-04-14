# CLI Client Plan

Этот документ описывает отдельный CLI-клиент для LocalScript Agent. CLI должен быть тонким клиентом поверх текущего API и Ollama runtime, а не вторым orchestrator.

Цель: дать красивый terminal UX для демо и разработки, при этом явно закрыть требования конкурса по локальности, фиксированным runtime-параметрам и воспроизводимости.

## 1. Роли CLI

CLI должен поддерживать два режима:

- `debug` - режим разработки и сравнения моделей.
- `release` - официальный демо-режим с фиксированными ограничениями.

Оба режима используют локальный Ollama endpoint. Внешние AI API не используются.

## 2. Release Mode

Release mode должен быть дефолтным для конкурсного демо.

Команда:

```powershell
luamts generate --mode release --task "..." --context context.json
```

Release mode обязан:

- требовать локальный Ollama endpoint: `http://127.0.0.1:11434`, `http://localhost:11434`, `http://ollama:11434` или другой явно разрешенный local host;
- запрещать model tags с суффиксом `-cloud`, например `qwen3-coder:480b-cloud` и `gpt-oss:20b-cloud`;
- использовать только выбранный финальный local/open-source model tag;
- передавать в Ollama фиксированные параметры:
  - `num_ctx=4096`;
  - `num_predict=256`;
  - `batch=1`;
  - `parallel=1`;
- сохранять `debug=false` по умолчанию;
- запускать API quality loop: generation -> validation -> semantic/rule checks -> repair или clarification -> final output;
- выводить только финальный Lua-код и короткий статус;
- сохранять полный trace в локальный report-файл, если указан `--report`.

Финальная модель пока не фиксируется этим документом. Перед сдачей нужно заменить placeholder на выбранный tag:

```powershell
ollama pull <FINAL_LOCAL_MODEL_TAG>
```

Пример после выбора модели:

```powershell
$env:OLLAMA_MODEL='<FINAL_LOCAL_MODEL_TAG>'
$env:OLLAMA_BASE_URL='http://127.0.0.1:11434'
$env:OLLAMA_NUM_CTX='4096'
$env:OLLAMA_NUM_PREDICT='256'
$env:OLLAMA_BATCH='1'
$env:OLLAMA_PARALLEL='1'
luamts generate --mode release --task "Из массива emails верни последний email."
```

Важно: release mode не должен позволять переопределить эти параметры через CLI flags. Если нужен эксперимент, используется debug mode.

## 3. Debug Mode

Debug mode нужен для локальной разработки, bake-off моделей и анализа validator/repair поведения.

Команда:

```powershell
luamts generate --mode debug --model qwen2.5-coder:7b --num-ctx 4096 --num-predict 256 --batch 1 --parallel 1 --task "..."
```

Debug mode может:

- выбирать локальную модель через `--model`;
- менять параметры:
  - `--num-ctx`;
  - `--num-predict`;
  - `--batch`;
  - `--parallel`;
- включать подробный trace через `--debug-trace`;
- сохранять model calls, prompt package, validation passes и critic reports;
- запускать один и тот же task в двух сценариях:
  - `--without-api` - прямой prompt в Ollama без quality loop;
  - `--with-api` - через `/generate` и полный quality loop;
- писать JSON report в `artifacts/benchmark_runs/`.

Debug mode может использовать cloud Ollama tags для разработки, bake-off и диагностики качества. Это должно быть явно видно в команде и в имени отчёта, чтобы результаты не смешивались с release/submit контуром:

```powershell
luamts generate --mode debug --model gpt-oss:20b-cloud --allow-cloud-model
```

`--allow-cloud-model` разрешён только в debug mode. Этот флаг нельзя использовать в release mode, benchmark submit mode и README demo command.

## 4. CLI Commands

Минимальный набор команд:

```text
luamts generate
luamts bench
luamts doctor
luamts vram-check
```

`luamts generate`:

- принимает natural language task;
- принимает context как inline JSON или путь к JSON-файлу;
- вызывает API `/generate` или прямой Ollama path для `--without-api`;
- красиво показывает trace:
  - request;
  - generation;
  - validation;
  - repair;
  - finalize;
- печатает финальный Lua-код.

`luamts bench`:

- запускает выборку benchmark cases;
- фиксирует model tag, runtime params, seed и scenario;
- пишет отдельные отчёты для `with-api` и `without-api`;
- запрещает cloud tags в release/submit mode.

`luamts doctor`:

- проверяет доступность Ollama;
- проверяет доступность API;
- показывает выбранную модель;
- показывает effective runtime params;
- проверяет, что model tag не cloud в release mode;
- проверяет наличие локальных knowledge/templates;
- предупреждает, если README/runtime params расходятся.

`luamts vram-check`:

- запускает эталонный запрос организаторов или локальный smoke prompt;
- фиксирует параметры:
  - `num_ctx=4096`;
  - `num_predict=256`;
  - `batch=1`;
  - `parallel=1`;
- собирает peak VRAM через `nvidia-smi`;
- пишет `docs/VRAM_BENCHMARK.md`;
- падает, если `nvidia-smi` недоступен или peak VRAM больше `8.0 GB`.

## 5. Required API/Runtime Changes

Чтобы CLI реально закрывал требования, текущий runtime нужно доработать.

### 5.1. Ollama options must be enforced

Сейчас model adapter отправляет:

```json
{
  "model": "...",
  "prompt": "...",
  "stream": false
}
```

Нужно отправлять:

```json
{
  "model": "<MODEL>",
  "prompt": "<PROMPT>",
  "stream": false,
  "options": {
    "num_ctx": 4096,
    "num_predict": 256,
    "batch": 1
  }
}
```

`parallel=1` должен быть зафиксирован на уровне Ollama serve/runtime configuration, потому что это не обычная per-request generation option в том же смысле, что `num_ctx` и `num_predict`.

### 5.2. Cloud model guard

Нужно добавить guard:

```text
release mode: reject model tag matching /(^|[:_-])cloud($|[:_-])|-cloud$/
debug mode: allow cloud tags only with --allow-cloud-model
```

Это закрывает риск, что локальный `OLLAMA_BASE_URL` всё равно проксирует cloud model в конкурсном контуре, но оставляет debug mode удобным для экспериментов.

### 5.3. Final model tag

Нужно заменить provisional model в документации и env defaults:

```text
qwen2.5-coder:3b -> <FINAL_LOCAL_MODEL_TAG>
```

Финальный tag должен быть выбран после VRAM benchmark.

### 5.4. Benchmark default model

`scripts/run_full_benchmark_report.py` не должен иметь cloud model default. Default должен быть:

```text
OLLAMA_MODEL или <FINAL_LOCAL_MODEL_TAG>
```

Если model tag cloud, release/submit benchmark должен падать.

### 5.5. Retrieval/templates documentation

README должен честно описывать текущий локальный слой:

- `packages/retrieval/selector.py`;
- `knowledge/templates/domain_prompt_templates.json`;
- `knowledge/examples/*.json`;
- `knowledge/archetypes/*.json`.

Если retrieval включён в prompt package, он считается частью поставки и должен оставаться локальным.

## 6. UX Shape

CLI должен быть похож на современный coding-agent terminal UI, но без переноса чужого orchestration layer.

Предпочтительный стек:

```text
Python + rich
```

Почему:

- проект уже Python;
- легко красиво показать panels, tables, progress и syntax-highlighted Lua;
- не нужно тащить Node/Ink runtime только ради CLI;
- проще запускать в том же окружении, что API tests и benchmark scripts.

Пример вывода:

```text
LocalScript Agent
Mode: release
Model: <FINAL_LOCAL_MODEL_TAG>
Params: num_ctx=4096 num_predict=256 batch=1 parallel=1

[generation] ok
[format] ok
[rule] ok
[semantic] repaired
[finalize] ok

Lua:
return wf.vars.emails[#wf.vars.emails]
```

В debug mode дополнительно:

```text
Repair count: 1
Critic failure: semantic_mismatch
Final candidate source: current_candidate
Report: artifacts/benchmark_runs/<timestamp>_<model>_debug-report.json
```

## 7. Compliance Checklist

Перед тем как считать CLI готовым к демо:

- [ ] выбран `<FINAL_LOCAL_MODEL_TAG>`;
- [ ] README содержит точный `ollama pull <FINAL_LOCAL_MODEL_TAG>`;
- [ ] release mode запрещает cloud tags;
- [ ] debug mode разрешает cloud tags только через явный `--allow-cloud-model`;
- [ ] API/model adapter передаёт fixed Ollama options;
- [ ] `parallel=1` зафиксирован в runtime instruction;
- [ ] `docs/VRAM_BENCHMARK.md` содержит peak VRAM при `num_ctx=4096`, `num_predict=256`, `batch=1`, `parallel=1`;
- [ ] `luamts doctor` проходит без warning в release mode;
- [ ] `luamts vram-check` подтверждает `<= 8.0 GB VRAM`;
- [ ] README описывает локальные templates/retrieval;
- [ ] benchmark tooling не имеет cloud model default.
