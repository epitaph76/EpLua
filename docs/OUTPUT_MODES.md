# Output Modes

Output mode описывает доменную форму результата, а transport envelope API остаётся отдельным.

В текущем API `/generate` наружу всегда возвращается `GenerateResponse`, где поле `code` содержит строку результата. Внутри active LowCode pipeline generator по умолчанию возвращает доменный JSON object со строками `lua{...}lua`, даже если `TaskSpec.output_mode` называется `raw_lua`.

## Active LowCode JSON contract

Текущий обязательный контракт generator-а:

```json
{
  "result": "lua{return wf.vars.value}lua"
}
```

Правила:

- outer shape: JSON object;
- Lua values: JSON strings;
- Lua wrapper: `lua{...}lua`;
- переносы внутри JSON string: `\\n`;
- без markdown/prose вокруг object.

CLI может показывать этот результат человекочитаемо с настоящими переносами строк, но validator получает raw JSON.

## Historical/canonical modes

Эти mode names остаются в `TaskSpec` и документации:

| Mode ID | Meaning |
| --- | --- |
| `raw_lua` | задача просит Lua-вычисление/скрипт как результат |
| `json_wrapper` | результат должен быть JSON object с Lua-строками |
| `patch_mode` | additive payload для изменения существующего объекта |
| `clarification` | вопрос пользователю вместо кода |

В текущем LowCode API path `raw_lua` не означает, что model response будет голым Lua без JSON. Он означает, что доменный результат - Lua-код, но transport для generator-а остаётся LowCode JSON object.

## Validation implications

Для active path validators используют `LOWCODE_JSON`:

- сначала проверяется JSON;
- затем извлекаются `lua{...}lua` segments;
- после этого запускаются Lua/static/rule checks.

Если outer JSON невалиден, Lua-синтаксис не проверяется, потому что Lua-сегменты нельзя безопасно извлечь.

## Clarification

Clarification mode остаётся допустимым design path, но текущий основной benchmark и CLI examples ориентированы на генерацию Lua-кода.
