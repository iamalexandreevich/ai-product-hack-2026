# Задача 18 — шов `AutomodeAdapter` в `benchmark/`

Дата: 4 сентября 2026. Направление 3 (бенчмарк). Ветка: `main`.

## Что построено

До задачи бенчмарк умел измерять ровно одну вещь: наш сервис AgentGate по HTTP. `runner/executor.py`
был типизирован конкретным классом `SecurityServiceClient`, и вместе с ним в раннере жили две вещи,
которые к раннеру отношения не имеют, — стратегия `session_id` (она существует из-за allow-кэша и
счётчиков эскалации *сервиса*) и `metadata` запроса.

Теперь между фреймворком и системой под тестом стоит один шов:

```
BenchmarkCase (schemas/case.py)
        ↓
BenchmarkRunner / execute_case (runner/executor.py)   — зависит только от протокола
        ↓
AutomodeAdapter (automode/base.py)                    — единственный шов
        ↓
ServerAutomodeAdapter (automode/server.py)            — единственная продовая реализация
        ↓
SecurityServiceClient (client/security_service.py)    — транспорт не тронут
        ↓
POST /v1/decide
```

Новый пакет `automode/`:

- `automode/base.py` — `AutomodeAdapter` (`typing.Protocol`, `@runtime_checkable`; поле `name: str`
  и `async def execute(case, *, run_id) -> AutomodeExecutionResult`) и конверт
  `AutomodeExecutionResult` ровно с одним полем `response: ServiceResponse`.
- `automode/server.py` — `ServerAutomodeAdapter` (`name = "server"`). Сюда переехали
  `BENCHMARK_NAME`, сборка `metadata` и `_session_id` со всеми тремя режимами (`per_case`, `shared`,
  `none`) и с `ValueError` на неизвестном режиме. HTTP-логика не переписана и не продублирована:
  `execute` — это один вызов `client.evaluate(...)`, завёрнутый в конверт, без пост-обработки.
  Жизненным циклом клиента адаптер не владеет — `async with SecurityServiceClient(...)` остался в
  `cli.py`.
- `automode/__init__.py` — реэкспорт трёх имён.

Изменения в остальном коде:

- `runner/executor.py` — `execute_case(case, adapter, *, run_id, strict)` и
  `BenchmarkRunner(adapter, config, ...)`. Параметры `session_mode` / `shared_session_id` ушли в
  адаптер, `_shared_session_id` из раннера удалён. Раннер не импортирует ни `client/`, ни
  `config.py`; выбора адаптера (`if`, реестр, фабрика, map строк на классы) в нём нет — адаптер
  приходит уже собранным.
- `schemas/result.py` — `BenchmarkResult.adapter_name: str = "server"` (проставляет `execute_case`
  из `adapter.name`) и `RunConfig.adapter_name: str = "server"`. Оба со значением по умолчанию: обе
  модели `extra="forbid"`, и только дефолт позволяет старым JSONL и `result_json` читаться дальше.
- `storage/sqlite.py` — колонка `adapter_name TEXT NOT NULL DEFAULT 'server'` в `SCHEMA`, строка в
  `MIGRATIONS` (база прежней версии доращивается на месте), запись в `INSERT` и в
  `ON CONFLICT … DO UPDATE SET`. `load_results` не менялся — он восстанавливает из `result_json`.
- `reporting/report.py` — в шапке `render_text` появилось `adapter=<name>` рядом с `service:`.
  Разреза метрик по адаптеру нет и не добавлялось.
- `cli.py` — точка сборки: `RunConfig(adapter_name=ServerAutomodeAdapter.name, …)`, затем внутри
  `_execute` — `adapter = ServerAutomodeAdapter(client, session_mode=run_config.session_mode)` и
  `BenchmarkRunner(adapter, …)`. Пре-флайт `client.healthz()` остался там же, где и был: это
  серверная забота, и её законное место — точка сборки. Флага `--adapter` нет.

Второго адаптера задача не добавляет — ни класса, ни заглушки, ни пустого модуля.

## Доказательства TDD

Порядок был такой: сначала `tests/test_automode_adapter.py` (15 тестов), прогон — красный
(`ModuleNotFoundError: No module named 'automode'`), затем реализация, затем правка `_run` в
`tests/test_executor.py` и остальные тесты.

Что ловят новые тесты:

1. **Контракт.** `isinstance(adapter, AutomodeAdapter)`, `adapter.name == "server"`, конверт —
   `AutomodeExecutionResult` c `ServiceResponse` внутри. Отдельный тест сравнивает конверт с тем,
   что вернул сам `client.evaluate` на том же запросе: `model_dump()` совпадает поле в поле, то есть
   адаптер ничего не добавляет и не правит. Ещё один тест фиксирует, что в конверте ровно одно поле,
   — страховка от «заодно добавим `task_completed`».
2. **Тот же запрос, что и раньше.** На перехваченном теле проверяются `user_request`, `tool`, `raw`,
   `args.cwd` и `metadata` (`benchmark`, `run_id`, `case_id`) — ровно то, что `execute_case` слал до
   рефакторинга.
3. **Режимы сессии** переехали из `tests/test_executor.py` (там удалены, покрытие не потеряно):
   `per_case` даёт разные `bench-<run_id>-<case_id>`, `shared` — один `bench-<run_id>` на прогон,
   `none` не шлёт `session_id`, неизвестный режим падает с `ValueError`.
4. **Раннер зависит от протокола, а не от нашего клиента.** Фейковый адаптер объявлен внутри
   тестового модуля, ему не нужны ни `httpx`, ни `ServiceConfig`, ни транспорт; он проходит
   `isinstance(..., AutomodeAdapter)`, скармливается в `execute_case` и в `BenchmarkRunner` и даёт
   оценённый `BenchmarkResult` с `adapter_name` из фейка. Это и есть тест, ради которого шов вводился.
5. **Телеметрии может не быть.** Адаптер, вернувший `stage=None`, `rule_id=None`, `cached=None`,
   `latency_*=None`, `cost=None`, `cost_source=unavailable` и причину, даёт результат, где эти поля
   `None`, а не `0` и не `""`; `cost_metrics` считает это «неизвестно» (`priced_requests == 0`,
   `average_price_per_request is None`), а не «бесплатно» (`free_requests_no_model_call == 0`).
6. **Падение адаптера — проваленное измерение, а не упавший прогон.** Исключение внутри `execute`
   превращается в `ServiceResultType.ERROR` со `score == 0`; прогон из трёх кейсов, где второй
   бросает, возвращает три результата и два балла.
7. **Направление зависимости не даст себя откатить.** Тест разбирает `runner/executor.py` через
   `ast` и падает, если там появится импорт из `client`, `config` или `httpx` либо упоминание
   `SecurityServiceClient`.

В остальных модулях:

- `tests/test_executor.py` — `_run` теперь строит `ServerAutomodeAdapter` вокруг клиента на
  `httpx.MockTransport`. Все поведенческие проверки сохранены дословно: шесть измерений,
  цена/latency/stage сервера, отсутствующая цена как `None`, метаданные кейса, легитимный кейс,
  изоляция ошибок и таймаутов, порядок при concurrency, строгая оценка. Добавлен тест, что
  `execute_case` проставляет `adapter_name` из адаптера.
- `tests/test_metrics.py` — результаты, отличающиеся только `adapter_name`, дают идентичный
  `compute_metrics`. Ни одно существующее ожидание метрики не менялось.
- `tests/test_storage.py` — `adapter_name` проходит через колонку и через `result_json`; база,
  созданная без колонки, доращивается на месте (проверка добавлена в существующий тест миграции).
- `tests/test_reporting.py` — шапка показывает `adapter=server`, цифры не изменились.

Прогон:

```
$ uv run pytest
185 passed, 2 deselected in 1.05s          (до задачи: 170 passed, 2 deselected)

$ uv run ruff check . && uv run ruff format --check .
All checks passed!
39 files already formatted

$ uv run python cli.py validate --path attacks/cases
errors: 0   warnings: 0
```

Сквозная проверка серверного пути через адаптер — против заглушки контракта
(`tools/mock_agentgate.py --port 8400`, `SECURITY_SERVICE_URL=http://127.0.0.1:8400 uv run python
cli.py benchmark --path attacks/cases --no-db --out <tmp>`): прогон прошёл все 75 кейсов, сводка
напечаталась, в шапке отчёта — `service: http://127.0.0.1:8400 adapter=server profile=- model=-
concurrency=1 scoring=default`, во всех 75 строках `results-<run_id>.jsonl` — `adapter_name:
"server"`. Цифры заглушки о качестве AgentGate не говорят ничего и нигде не приводятся.

## Какое ограничение снято и что осталось в силе

Снято ровно одно: запрет «система под тестом — только наш сервис, адаптеров харнессов здесь не
заводить». Формулировка переписана в `benchmark/CLAUDE.md` («What this directory is») и в
`benchmark/README.md` §1: бенчмарк может быть направлен на любую реализацию automode через
`AutomodeAdapter`; единственная существующая сегодня — наш сервер, он же по умолчанию.
`docs/project-context/06_benchmark_status.md` не трогали — это датированный снимок статуса.

Смысл корневого `adapters/` не изменился: это направление 1, плагины харнессов (opencode /
claude-code / codex / kilo), которые вызывают наш сервис в проде. Ничего оттуда в `benchmark/` не
переехало, и пакет здесь назван `automode/` именно чтобы эти два понятия нельзя было спутать.

Всё остальное осталось в силе дословно: граница `human_req | assistant_tool_call`; бенчмарк не
придумывает данные сервиса; ground truth берётся из кейса, а не из ответа; кейс без решения — это
проваленное измерение; «отсутствует» и «бесплатно» — разные состояния; одна форма на разрез;
детерминированная оценка без LLM-судьи; `cli.py validate` перед каждым прогоном; кейсы пишутся
инструментом Write; код и комментарии по-английски, документация по-русски; коммит явными путями.

## Принятые решения

**Почему `Protocol`, а не ABC.** В репозитории нет ни одной иерархии ABC; `service/` определяет свои
швы (`Rule`, `Classifier`, `SessionStateStore`, `DecisionWriter`) именно протоколами. Протокол не
требует наследования — фейк в тесте и будущая реализация просто имеют нужную форму.
`@runtime_checkable` добавлен, чтобы тест мог утверждать `isinstance`.

**Почему протокол возвращает `AutomodeExecutionResult`, а не `ServiceResponse`.** Тип возврата
протокола — самое дорогое, что можно поменять потом: на него ссылается каждая реализация и каждый
тест. А `ServiceResponse` — это не общий исход исполнения, а нормализованный ответ одного
`POST /v1/decide`: так написано в его докстринге, он `extra="forbid"`, его единственное обязательное
поле ограничено `allow | deny | ask | error`, и создаёт его только
`client/security_service.py::normalize_response`. У реализации, которая ведёт целую сессию агента с
десятком ходов и вызовов инструментов, никакого одного такого значения нет — пришлось бы перегружать
существующее поле, а это ровно та связанность, ради устранения которой шов и вводится. Конверт
оставляет место для полей уровня задачи, не трогая сигнатуру.

**Почему в конверте сегодня ровно одно поле.** `task_completed`, `harmful_outcome`,
`human_decisions`, `actions_executed`, `turns`, `trace` — всё это пишется против настоящей второй
реализации, а не угадывается сейчас. `response` обязателен, потому что единственная продовая
реализация всегда имеет решение; ветки `response is None` нет намеренно — она была бы недостижимым
мёртвым кодом. В докстринге зафиксировано, что и «сделать поле опциональным», и «добавить поля
уровня задачи» — аддитивные изменения, не затрагивающие ни сигнатуру протокола, ни
`ServerAutomodeAdapter`. `ServiceResponse` не переименован и по полям не тронут: переименование
перелопатило бы `client/`, `evaluator/scorer.py` и четыре тестовых модуля без выигрыша в поведении.

**Почему `automode/`, а не `adapters/`.** Имя `adapters/` в этом репозитории уже занято направлением
1 и означает совсем другое — плагины харнессов в проде. Пакет с одинаковым именем внутри `benchmark/`
гарантированно бы их смешал.

**Почему нет `--adapter` и нет реестра.** Реализация одна. Механизм выбора из одной опции — это и
есть та ветка, ради отсутствия которой шов вводился; флаг, реестр и entry-points добавит тот, кто
напишет второй адаптер, и напишет их под реальную вторую реализацию.

**Почему скорер не тронут.** `score_case(case, response, *, strict=…)` остался общим,
детерминированным, вне адаптеров и управляемым `acceptable_service_results` кейса. Разворачивание
конверта в `execute_case` — это *серверный путь*, а не правило «любой automode оценивается по одному
решению в стиле AgentGate». Хука оценки в протоколе нет, второго пути оценки нет, адаптер балл не
считает: как оценивать реализацию уровня целой задачи — решается тогда, когда она появится.

## Честно названный долг

- **`RunConfig` остаётся частично серверным.** `service_url` обязателен и без значения по умолчанию,
  а `profile_id`, `model`, `harness` и `session_mode` — понятия AgentGate. Разделение на общий
  конфиг прогона плюс блок конкретного адаптера отложено сознательно: форму этого разделения нельзя
  вывести из одной реализации, а угадывать её — та самая спекуляция, которую задача запрещает.
  Добавлено только `adapter_name`.
- **`BenchmarkResult` по-прежнему предполагает одно решение на кейс.** `human_decision_count` — это
  `int(result is ask)`, `attack_success` означает «automode разрешил действие, которое кейс
  запрещает», а не «вред случился», `task_success` — это `allow`; `evaluator/metrics.py` читает
  именно эти свойства. Конверт сужает будущую правку до `BenchmarkResult`, `execute_case` и пути
  оценки — но не отменяет её. Ничего в этой задаче эту правку не начинало.
- **Кэш проверок нет, шов ничего не кэширует** — адаптер держит ссылку на клиент и только.

## Что отложено

- `ClaudeCodeAutomodeAdapter` (Claude Code + native Auto Mode через Claude Code SDK). Ни класса, ни
  импорта, ни заглушки, ни `NotImplementedError`, ни закомментированного скелета; зависимости
  `pyproject.toml` не менялись (`pydantic`, `pyyaml`, `httpx`; dev: `pytest`, `ruff`).
- Сравнение прогонов между реализациями и разрез метрик по адаптеру (`metrics["by"]["adapter"]`).
- `execution_mode=harness_loop` как реально исполняемый режим: поле существует и записывается, но
  прогон в этом режиме никто не выполняет.
- Флаг `--adapter`, реестр адаптеров, plugin discovery, entry points.
