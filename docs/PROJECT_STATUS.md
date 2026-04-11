# PROJECT STATUS

Краткий статусный документ проекта LocalScript Agent.

Полный source of truth по этапам, рискам, scope и зависимостям находится в [../PROJECT_STATUS_full.md](../PROJECT_STATUS_full.md). Этот файл нужен для быстрого входа в контекст без чтения полного roadmap.

## Текущее состояние

- текущий этап baseline закрыт: `S-0 = done`;
- доменная декомпозиция и baseline benchmark layer собраны: `S-1 = done`;
- минимальный agent contour собран документарно: `S-3 = done`;
- локальный backend уже работает: `S-4 = done`;
- репозиторий зафиксирован как локальный LocalScript-агент, а не универсальный AI-ассистент;
- конкурсные ограничения и MVP-границы вынесены в отдельные документы;
- создана стартовая структура каталогов для последующих этапов;
- для разработки agent contour временно заморожен provisional model tag: `qwen2.5-coder:3b`;
- финальный model bake-off перенесён на этап после появления agent contour и validation / repair loop;
- backend поднимает `/health` и `/generate`, отдаёт OpenAPI и вызывает только локальный runtime через `Ollama`.

## Карта этапов

| ID | Этап | Статус | Краткий результат |
| --- | --- | --- | --- |
| S-0 | Репозиторный baseline и конкурсная фиксация | `done` | Зафиксированы рамки проекта, ограничения, MVP и стартовая структура |
| S-1 | Декомпозиция домена LocalScript и benchmark layer | `done` | Собраны формальная карта домена, archetypes задач и baseline regression pack |
| S-2 | Финальный отбор модели под 8 GB VRAM | `deferred` | Финальный bake-off перенесён после agent contour и validator / repair loop; до этого используется provisional `qwen2.5-coder:3b` |
| S-3 | Выделение агентного контура из Qwen Code и Claw Code | `done` | Зафиксированы agent architecture, state machine, pipeline sequence и skill decomposition |
| S-4 | Core generation service и API-контракт | `done` | Рабочий локальный backend с `/health`, `/generate`, OpenAPI и локальным model path |
| S-5 | Domain adapter для LocalScript-формата | `done` | Принуждение ответа к LocalScript-правилам |
| S-6 | Validator, critic и repair loop | `done` | Управляемый контур проверки, critic-driven repair и bounded loop |
| S-7 | Локальная база знаний, шаблоны и retrieval | `planned` | Локальный слой примеров, archetypes и retrieval |
| S-8 | UI как дополнительный необязательный этап | `planned` | Demo-friendly интерфейс без подмены ядра |
| S-9 | Evaluation harness и регрессионный набор | `planned` | Метрики, benchmark runner и regression suite |
| S-10 | Docker-first развёртывание, безопасность и воспроизводимость | `planned` | Однострочный локальный запуск |
| S-11 | Конкурсные артефакты: README, C4, видео, презентация | `planned` | Полный комплект материалов для сдачи |
| S-12 | Финальная полировка и защита | `planned` | Готовность к защите и закрытым тестам |

## Что уже зафиксировано

- проект обязан работать полностью локально;
- runtime обязан идти через `Ollama`;
- внешний AI inference запрещён;
- VRAM budget ограничен `8 GB`;
- система должна генерировать именно **LocalScript-совместимый** Lua;
- validation и хотя бы один полезный agentic шаг обязательны;
- воспроизводимость должна быть оформлена документированно и без ручной магии.

## Что ещё не сделано

- не выбран финальный model tag внутри реального agent pipeline;
- не построен evaluation harness;
- не оформлен Docker-first runtime;
- не подготовлены конкурсные финальные артефакты.

## Следующий фокус

Следующий рабочий этап: `S-7` — локальная база знаний, шаблоны и retrieval, чтобы снизить нагрузку на модель и сделать generation стабильнее за счёт локальных примеров и структурированного контекста.
