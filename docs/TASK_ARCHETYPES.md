# TASK ARCHETYPES

## Назначение

Этот документ фиксирует только **task archetypes** для `S-1`.
Response modes намеренно вынесены в отдельный документ, чтобы не смешивать тип задачи и формат ответа.

## Canonical Archetypes

| Archetype ID | Что означает | Ключевая сложность |
| --- | --- | --- |
| `simple_extraction` | чтение и возврат значения по известному пути | корректный доступ к данным и работа с пустым массивом |
| `filtering` | отбор элементов по условиям | корректная работа с `nil`, пустыми строками и array semantics |
| `transformation` | изменение структуры или вычисление нового значения на базе входа | локальная мутация без разрушения нужных полей |
| `normalization` | приведение входной структуры к каноническому виду | типы, одиночный объект вместо массива, сохранение структуры |
| `datetime_conversion` | разбор и конвертация даты/времени | форматные проверки, границы строк, timezone/offset |

## Explicit Non-Archetypes

Следующие идентификаторы **не являются** archetypes:

- `raw_lua`
- `json_wrapper`
- `patch_mode`
- `clarification`

Это response modes. Они описаны в [OUTPUT_MODES.md](OUTPUT_MODES.md).

## Mapping For Public Cases

| Case ID | Публичный кейс | Archetype | Почему |
| --- | --- | --- | --- |
| `case-01-last-array-item` | Последний элемент массива | `simple_extraction` | извлекается одно значение по известному пути |
| `case-02-try-counter` | Счётчик попыток | `transformation` | вычисляется новое значение на основе текущего |
| `case-03-restbody-cleanup` | Очистка значений в переменных | `transformation` | меняется набор полей внутри существующей структуры |
| `case-04-iso8601-format` | Приведение времени к стандарту ISO 8601 | `datetime_conversion` | конвертация из `YYYYMMDD` и `HHMMSS` |
| `case-05-items-as-arrays` | Проверка типа данных | `normalization` | одиночные объекты приводятся к массиву |
| `case-06-filter-discount-markdown` | Фильтрация элементов массива | `filtering` | отбираются элементы по наличию значений |
| `case-07-add-squared-variable` | Дополнение существующего кода | `transformation` | создаются новые вычисляемые поля в additive payload |
| `case-08-unix-time-conversion` | Конвертация времени | `datetime_conversion` | ISO 8601 переводится в epoch/unix |

## Archetype Notes

### `simple_extraction`

- Обычно использует один root path.
- Самый частый риск: неверная индексация массива и отсутствие проверки на пустой вход.

### `filtering`

- Результат должен сохранять массивную природу.
- Новый массив создаётся через `_utils.array.new()`, если формируется с нуля.

### `transformation`

- Включает арифметические изменения, локальные патчи и selective field updates.
- В `patch_mode` transformation не должна перерастать в переписывание всего payload.

### `normalization`

- Цель не вычислить новое бизнес-значение, а привести структуру к ожидаемому виду.
- Критичный риск: превратить массив в объект или наоборот без явного правила.

### `datetime_conversion`

- Требует проверки входного формата до вычисления результата.
- Для `wf.initVariables.*` и `wf.vars.*` действует один archetype, но разные data roots.
