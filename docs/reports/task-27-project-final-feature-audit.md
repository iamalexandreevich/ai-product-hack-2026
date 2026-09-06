# Проверка slopsquatting, SAFE RECOVERY и ответа человека

Дата: 6 сентября 2026. Проверен локальный checkout `e683350` и подготовленные ранее финальные документы. Задача — продолжить проверку Claude по трём спорным утверждениям в `PROJECT_FINAL.md`.

## Результат

| Пункт | Подтверждено кодом | Граница реализации |
|---|---|---|
| Slopsquatting | `service/agentgate/rules/packages.py::PackagesRule.evaluate` всегда возвращает `None`; правило включено последним в `rules/chain.py::STAGE1` | Отдельной проверки существования или репутации пакета нет. `supply_chain` есть в классификаторе ступени 2; это вероятностная оценка, а не реализованный модуль пакетов |
| SAFE RECOVERY | `suggest` проходит через классификатор, `Verdict`, `Decision.to_response`, `resolveOut` и `denyMessage` к агенту. В Pi отказ возвращается с `terminate: false`; в plugin-v1 он оформляется как ошибка конкретного инструмента | Отдельного вердикта нет. Подсказка может быть пустой; её безопасность и успешное завершение задачи не обеспечиваются самим фактом передачи текста |
| Ответ человека | Pi вызывает `ctx.ui.confirm` и применяет согласие или отказ локально | Событие ответа с привязкой к решению не отправляется в сервис. `GuardClient` поддерживает `decide`, `inspect`, `health`; в OpenAPI нет маршрута feedback. История диалога не является структурированным учётом ответа на `ask` |

`service/agentgate/classify/schema.py::_strict_schema` требует поле `suggest` в JSON Schema для модели. При этом `ClassifierOutput.suggest`, `DecideResponse.suggest` и `Verdict.suggest` имеют значение по умолчанию `""`. Поэтому прежняя формулировка «обязательное поле» не должна читаться как гарантия непустой безопасной альтернативы.

## Существующее покрытие

- `service/tests/classify/test_llm.py::test_a_refusal_carries_the_models_reason_and_suggestion` — причина и подсказка сохраняются для `deny` и `ask`.
- `service/tests/engine/test_gate.py::test_gray_zone_goes_to_the_classifier_and_keeps_its_verdict` — движок сохраняет `suggest` классификатора.
- `service/tests/engine/test_decision.py::test_response_is_built_from_the_verdict_and_the_latency` — причина и подсказка попадают в API-ответ.
- `adapters/packages/core/test/core.test.ts`, `renders a deny message the model can act on` — сообщение агенту включает правило, причину и подсказку.
- `adapters/packages/plugin-v1/test/plugin.test.ts`, `blocks a destructive command and lets the turn continue` — блокировка конкретного вызова не отправляет отказ по всем ожидающим разрешениям сессии.
- `service/tests/e2e/test_e2e.py::test_llm_deny_and_log` — существующая проверка с fake LLM ожидает `npm install lodash` в `suggest` после отказа на `npm install lodahs`. Это не измерение качества реальной модели и не прогон задачи до завершения.

## Выполненная проверка

Из `service/`:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/classify/test_llm.py tests/engine/test_decision.py tests/engine/test_gate.py tests/rules/hard_deny/test_rules.py -q -p no:cacheprovider
```

Результат на Windows: **251 passed, 9 failed**. Все проверки в файлах классификатора, движка и API-проекции прошли. Девять падений — параметризации `test_hard_deny_cases` для `~/Documents`, `.git/hooks/*`, `.claude/settings.json` и `~/.ssh/authorized_keys`. Код нормализует пути через платформенный `os.path`, а шаблоны используют POSIX-разделители; кроме того, раскрытие `~` в нормализаторе зависит от `HOME`, тогда как тест использует `os.path.expanduser`. Это отдельная проблема обработки путей в Windows; успешный Linux-прогон в этой проверке не воспроизводился.

Тесты TypeScript прочитаны, но не запускались: Node.js отсутствует в PATH и проверенных локальных местах установки. E2E с Postgres не запускался. Полный прогон сервиса не выполнялся; результат выше относится только к четырём указанным файлам.

## Изменения

В `PROJECT_FINAL.md` и `PRODUCT_FINAL.md` уточнены границы SAFE RECOVERY и обратного канала, добавлены ссылки на существующие тесты и этот отчёт. Статус заглушки slopsquatting подтверждён. Код и публичные контракты не изменялись; новые тесты для дублирования существующего покрытия не добавлялись. Работающие проверки существующего поведения не выдаются за TDD новой реализации.

Не реализовывались новые функции: модуль пакетов, API обратной связи и оценка успешного продолжения после отказа требуют отдельных задач. Ранее подготовленные изменения пользователя сохранены.
