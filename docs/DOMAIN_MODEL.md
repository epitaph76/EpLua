# DOMAIN MODEL

## Назначение

Этот документ фиксирует каноническую доменную модель для текущего `luaMTS`.
Его задача: описать **LocalScript-специфичный** Lua-контур, с которым работают `planner`, `prompter`, `generator`, validators и benchmark.

Важно: это не описание transport-level API. Архивный `localscript-openapi.yaml` фиксирует текущий HTTP baseline `/generate -> {"code": string}`, но не переопределяет доменные режимы ответа.

## Execution Context

- Базовый язык в публичной выборке: `Lua 5.5`.
- Система генерирует не абстрактный Lua, а LocalScript-совместимый код.
- Доменный код должен работать с данными процесса напрямую, без промежуточного JsonPath-слоя.
- Эти правила уже используются backend pipeline, validators и benchmark-артефактами.

## Data Roots

### `wf.vars.*`

- Основной корень для рабочих переменных runtime.
- Через него читаются данные из контекста задачи, включая:
  - простые значения;
  - массивы;
  - результаты REST-вызовов;
  - вложенные JSON-объекты.

Примеры из публичной выборки:

- `wf.vars.emails`
- `wf.vars.try_count_n`
- `wf.vars.RESTbody.result`
- `wf.vars.json.IDOC.ZCDF_HEAD.DATUM`
- `wf.vars.json.IDOC.ZCDF_HEAD.TIME`
- `wf.vars.parsedCsv`

### `wf.initVariables.*`

- Отдельный корень для стартовых переменных, переданных в схему до выполнения.
- Он рассматривается как самостоятельный источник данных, который нельзя смешивать с `wf.vars.*` без явного основания задачи.

Пример из публичной выборки:

- `wf.initVariables.recallTime`

## Data Types

Публичная выборка фиксирует следующие базовые типы:

- `nil` для отсутствующего значения;
- `boolean`;
- `number`;
- `string`;
- `array`;
- `table`;
- `function`.

## Array Semantics

Для массивов важны не только значения, но и способ создания/маркировки:

- `_utils.array.new()` используется для создания нового массива в ходе вычисления;
- `_utils.array.markAsArray(arr)` используется, когда существующую структуру нужно явно пометить как массив;
- потеря array semantics считается доменной ошибкой даже при внешне похожем JSON-результате.

## Allowed Constructs

Публичная выборка разрешает использовать следующие базовые конструкции:

- `if ... then ... else`
- `while ... do ... end`
- `for ... do ... end`
- `repeat ... until`

## Domain Axes

Домен нормализуется по двум независимым осям:

1. **Task archetype**: какой класс преобразования требуется выполнить.
2. **Output mode**: в каком виде должен быть возвращён результат.

Это важно, потому что `patch_mode` и `clarification` являются режимами ответа, а не archetype-классами задачи.

## Canonical Task Archetypes

- `simple_extraction`
- `filtering`
- `transformation`
- `normalization`
- `datetime_conversion`

Подробная карта archetypes вынесена в [TASK_ARCHETYPES.md](TASK_ARCHETYPES.md).

## Canonical Output Modes

- `raw_lua`
- `json_wrapper`
- `patch_mode`
- `clarification`

Подробные правила вынесены в [OUTPUT_MODES.md](OUTPUT_MODES.md).

## Typical Failure Classes

- Использование JsonPath или другого непрямого способа доступа вместо `wf.*`.
- Смешивание `wf.vars.*` и `wf.initVariables.*`.
- Потеря array semantics при фильтрации или нормализации.
- Возврат prose, markdown fences или служебного текста вне допустимого output mode.
- Невалидный `lua{...}lua` wrapper.
- Переписывание всего payload в `patch_mode` вместо локального additive update.
- Хрупкая работа с `nil`, пустыми строками и пустыми массивами.
- Хрупкий разбор даты/времени без проверок формата и границ.

## What This Document Does Not Fix

- Финальный выбор локальной модели.
- Runtime behavioral validator для всех archetypes.
- UI-поведение.
