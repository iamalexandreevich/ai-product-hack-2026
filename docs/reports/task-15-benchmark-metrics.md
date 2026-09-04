# Задача 15 — метрики бенчмарка: аудит и приведение конвейера в соответствие

Дата: 4 сентября 2026. Направление 3 (`benchmark/`). Сервис не менялся.

## Что требовалось

Пройти весь путь «кейс → раннер → запрос → ответ сервиса → результат → агрегация → отчёт» и
убедиться, что он даёт: серверные `price`, `response_time`, `stage`; бенчмарочные `attack_type` и
`attack_difficulty`; разделение `baseline | team`; метрики ASR, Utility, FP, Friction, latency — с
разрезами по типу атаки, сложности, источнику и ступени.

## Что уже было верно

- `stage`, `latency_ms.{stage1,stage2,total}`, `rule_id`, `cached`, `decision_id` берутся из ответа
  сервиса и доезжают до `BenchmarkResult` (`service_latency_*`, `stage`) и до SQLite. Клиентские
  часы (`execution_time_ms`) хранились отдельно и серверное время не подменяли.
- Отсутствующие значения уже не подделывались: `stage` не число → `None`, latency нет → `None`,
  цена неизвестна → `None` плюс причина (`cost_unavailable_reason`).
- `attack_category` и `difficulty` уже брались из кейса, а не из ответа.
- Атаки и контрольные кейсы уже различались (`is_benign` из `attack_location: [none]`).
- ASR по сути уже считался (`attack_pass_through_rate`), как и FP+friction rate.

## Что было не так

1. **Разрезов не было.** ASR считался только суммарно; `by_attack_category` / `by_difficulty` несли
   accuracy и клиентские миллисекунды, но не ASR, не Utility и не серверную latency.
2. **`dataset_source` отсутствовал** — популяции `baseline` и `team` нельзя было разделить.
3. **Метрики жили в `reporting/report.py`** — то есть в коде визуализации, а не отдельно от него.
4. **`total_known_cost` при неизвестной цене равнялся `0.0`** — прогон с неизвестной стоимостью
   читался как бесплатный.
5. **Утверждённая форма поля цены не подхватывалась.** По `response-cost-reporting.md` сервис
   отдаст объект `cost: {input_tokens, output_tokens, reasoning_tokens, currency, amount}`. Клиент
   искал `cost` как число и `usage.*` как токены — объект был бы молча проигнорирован.
6. **Валюта цены нигде не хранилась.**
7. **Успех атаки выводился из `allow`.** На нынешнем датасете это совпадает с истиной (ни один
   атакующий кейс не допускает `allow`), но правило нигде не было выражено и сломалось бы на первом
   же кейсе, где `allow` допустим.
8. **Повторная запись результата в тот же прогон обновляла лишь пять колонок** — `stage`, `cost`,
   latency в SQL расходились с `result_json`.

## Что сделано

- `schemas/case.py` — `DatasetSource(baseline | team)`, поле `dataset_source` (по умолчанию `team`).
  Решение владельца: все 75 написанных здесь кейсов — `team`; `baseline` резервируется за импортом
  внешнего корпуса. Ни один кейс не переразмечен.
- `schemas/result.py` — `dataset_source`, `cost_currency` и вычисляемые свойства истины:
  `is_attack`, `has_decision`, `blocked`, `human_decision_count`, `attack_success`, `task_success`,
  `false_positive`. Свойства не сериализуются — данные не дублируются, метрики пересчитываются из
  сохранённой строки.
- `evaluator/metrics.py` (новый) — единственное место, где считаются ASR, Utility, FP, Friction,
  decision latency, цена и все разрезы.
- `reporting/report.py` — теперь только форматирует: блок `metrics` в JSON, ASR/Utility/FP/Friction
  и разрезы в тексте, ASR + Utility + серверная latency в групповых таблицах, таблицы
  `by_dataset_source` и `by_stage`. Старые ключи (`security_metrics`, `latency`, `cost`) сохранены и
  заполняются из тех же функций.
- `config.py` / `client/security_service.py` — пробуются пути `cost.amount`, `cost.input_tokens`,
  `cost.output_tokens`, `cost.total_tokens`, `cost.currency`; серверная цена всегда побеждает
  расчёт по таблице.
- `storage/sqlite.py` — колонки `dataset_source`, `cost_currency`, индекс по источнику, миграция
  старой базы на месте, полное обновление всех измеренных колонок при перезаписи результата.
- `dataset/loader.py`, `cli.py` — фильтр `--dataset-source`, счётчик популяций в выводе `validate`.

## Доказательства

165 тестов (было 122), из них 43 новых: `tests/test_metrics.py` (19) плюс дополнения в
`test_client.py`, `test_executor.py`, `test_storage.py`, `test_reporting.py`, `test_case_schema.py`,
`test_dataset_validator.py`. Ни один существующий тест не ослаблен и не удалён. `ruff check` и
`ruff format --check` чисты. Сквозной прогон против `tools/mock_agentgate.py` (75 кейсов) прошёл:
ASR, Utility, FP, Friction, latency и разрезы посчитаны, цена осталась `null` с причиной.

## Что посчитать по-прежнему нельзя

- **Цена.** Сервис v1 не отдаёт ни токенов, ни стоимости. Поле готово принять утверждённый объект
  `cost`, но до его появления `price` — `null` с причиной. Оценок не делается.
- **Task slowdown.** Нужен базовый прогон тех же задач без гейта; бенчмарк меряет одно решение на
  кейс. Поле приходит `null` с текстом причины.
- **`reasoning_tokens`** из будущего объекта `cost` в типизированные поля не разбирается (лежит
  целиком в `service_raw_response`).
- **Группа `baseline` пуста**, пока внешний корпус не импортирован: разрез существует, данных в нём
  нет, и отчёт это показывает, а не скрывает.
