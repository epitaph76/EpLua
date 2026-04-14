# Validation Pipeline

Имя файла сохранено как `validation_pipline.md` для совместимости со старыми ссылками.

Актуальное подробное описание находится в [how_validation_work.md](how_validation_work.md). Этот файл фиксирует короткую архитектурную суть.

## Current architecture

```text
request_received
-> planner
-> prompter
-> generation
-> deterministic_validation
-> response_ready
```

Repair branch:

```text
deterministic_validation
-> repair_generation
-> deterministic_validation
-> response_ready
```

## Design principles

1. Planner не пишет Lua.
2. Prompter не пишет Lua и не возвращает полный prompt.
3. Generator единственный слой, который пишет candidate.
4. Deterministic validators первичны.
5. Critic report не заменяет validators; он только выбирает `finalize` или `repair`.
6. Repair ограничен budget-ом.
7. Blind benchmark не должен получать эталонное решение.
8. Retrieval/few-shot не должен подмешивать evaluation cases.

## Deterministic validators

Слои:

- `format_validator`;
- `syntax_validator`;
- `static_validator`;
- `principle_validator`;
- `rule_validator`.

Ключевые проверки:

- outer JSON object валиден;
- Lua-строки имеют wrapper `lua{...}lua`;
- JSON-строки корректно экранируют переносы как `\\n`;
- JsonPath запрещён;
- неизвестные roots вроде `wf.data.*` запрещены;
- `print/debug output` запрещён;
- `error()` запрещён в LowCode JSON;
- новые массивы создаются через `_utils.array.new()`;
- array semantics не теряются.

## Repair

Repair больше не идёт через отдельный `repair_prompter` слой.

Текущий ремонт:

```text
current candidate
+ compact validation report
+ critic repair instruction
-> repair_generation
```

Затем repaired candidate снова проходит deterministic validation.

## Generator truncation

Каждый generator-stage защищён от обрезки на `num_predict`.

Если chunk упёрся в лимит, pipeline:

- пишет fragment во временный файл;
- строит continuation prompt;
- просит generator дописать недостающую часть;
- передаёт в validator только полный склеенный candidate;
- удаляет временные файлы после финального статуса задачи.

## LowCode hard rules

- Lua 5.5-compatible style.
- Script string: `lua{...}lua`.
- Data roots: `wf.vars` и `wf.initVariables`.
- JsonPath запрещён.
- `error()` запрещён.
- `print` запрещён.
- Новые массивы: `_utils.array.new()`.
- Existing array mark: `_utils.array.markAsArray(arr)`.

## Runtime/semantic validation status

Текущий обязательный контур - deterministic validation. Runtime behavioral validation и semantic judge остаются направлением развития, но не должны описываться как обязательный active gate без отдельного подтверждения тестами.

## Useful commands

```powershell
python -m pytest apps\api\tests packages\benchmark\tests -q
```

```powershell
docker compose config
```
