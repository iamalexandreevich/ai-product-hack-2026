# Задача 25: AgentGate v3.2 — атрибуция решения к ключу, `Idempotency-Key` по предъявителю, метод у сетевого действия

Ветка `feat/v3.2-key-attribution` от `main` `cbe3073` (после слияния и деплоя v4). Спека `docs/superpowers/service/specs/2026-09-05-agentgate-v3.2-key-attribution-and-network-method-design.md`, план — семь задач. Владелец 5 сентября 2026: «делай всё необходимое для улучшения»; из известных ограничений закрыты три — атрибуция решения к API-ключу (`api-keys.md`), границы `Idempotency-Key`, метод у `tool: network` (открытый вопрос §7 спеки v3.1). Отчёт написан по факту выполнения, диапазон `cbe3073..cb4900a`.

Что изменилось для интегратора, аддитивно к v3.1 и v4:

- **`args.method` у сетевого действия.** Закрытый набор `GET/HEAD/POST/PUT/PATCH/DELETE/OPTIONS`, uppercase. Значение вне набора — `ask`, `stage: 0`, `rule_id: api.invalid-request`, HTTP 200. Поле входит в `action_hash`, поэтому **allow-кэш прогревается заново**. В промпт добавляется строка `method=` — только при наличии метода; запрос без метода даёт байт в байт прежний промпт.
- **`profile.domain-trusted` требует метод.** Под `network.trusted_allows: true` доверенный домен даёт `allow` на ступени 1 только для `GET` и `HEAD`. `method: None` не квалифицируется никогда (fail-closed): адаптер обязан слать метод, иначе решение принимает ступень 2 — как до v3.2.
- **`key_id` в ленте решений.** Строка решения и строка JSONL несут `key_id` выдавшего решение ключа (`null` для статического токена). В `DecideResponse`/`InspectResponse` поля нет и не будет. Новый фильтр `GET /v1/decisions?key_id=<ULID>`; пустое значение `?key_id=` — это отсутствие фильтра, а не фильтр по пустоте. Сам ключ и его хэш не попадают ни в строку, ни в JSONL, ни в лог.
- **`Idempotency-Key` — в границах предъявителя.** Уникальность и повтор считаются по паре `(principal, idempotency_key)`, где `principal` — id ключа либо литерал `token`. Один и тот же ключ идемпотентности с одним телом от двух разных предъявителей даёт два разных `decision_id` и две строки. К сессии повтор по-прежнему не привязан.
- **Клиентские правила видят сетевое действие.** Для `tool: network` каноническими единицами сопоставления стали домены действия: `deny`/`ask` срабатывают по любому домену, `allow` — только когда покрыты все. `rules.deny: ["*"]` теперь перебивает `profile.domain-trusted`.

| Метрика | До (`cbe3073`) | После (`89eb706`) |
|---|---|---|
| Тестов, exit code | 1446, 0 | **1509**, 0 |
| Голов Alembic | 1 (`0006`) | 1 (`0007`) |
| Латентность ступени 1, 10 000 оценок | — | p50 0.041 мс, p95 0.052 мс, max 0.306 мс (бюджет 1 мс) |

## 1. Как выполнялось

Как v3 и v3.1: исполнители-Sonnet в отдельных git worktree (`a` и `b`, со своей тестовой базой `agentgate_test_a`/`agentgate_test_b`), fast-forward слияние после rebase, ревью на Opus по слитому коду, правки раундами. Спека и план писались против дерева до v4 и были сверены с v4 отдельно: миграция `0007` встаёт после `0006_v4_spans_and_redaction`, новые поля строки — после `spans`/`redacted`/`spans_rejected`.

Один раунд правок был потерян при рестарте процесса Claude Code: незакоммиченная работа исполнителя нашлась в worktree `b` и была доделана контроллером — отсюда сводный коммит `99aac57` вместо нескольких мелких.

| Волна | Задачи | Коммиты |
|---|---|---|
| документы | спека и план | `e7b33fc` |
| 1 | 2 ‖ 4 | `29e22f1`, `640a8dc` |
| 2 | 1, затем 3, затем 5 | `a08b648`, `3ed6a0f`, `80fdf6d` |
| правки | девять пунктов двух ревью | `99aac57` |
| 6 | документация | `62d0f2a` |
| повторное ревью | находка про сетевые действия и клиентские правила | `89eb706` |
| 7 | приёмка и этот отчёт | — |

## 2. Что построено, по задачам

**Задача 2 — метод как поле действия** (`29e22f1`). `ActionArgs.method` в `agentgate/api/schemas.py` (закрытый набор, uppercase-валидатор), `NormalizedAction.method` в `agentgate/normalize/model.py` — входит в `action_hash`; проброс в `agentgate/normalize/__init__.py`, строка `method=` в блоке `[ACTION]` (`agentgate/classify/prompt.py`), фабрика `network_action` в `tests/factories.py`. Поле поставлено на общий `ActionArgs`, а не на отдельный `NetworkArgs`, потому что такого типа в дереве нет; отсюда же оно попало в `inspect_request.schema.json`.

**Задача 4 — метод в доверенном домене** (`640a8dc`). Ветка `tool: network` в `agentgate/rules/profile_domain_trusted.py`: `allow` только при `method in {GET, HEAD}`, `method: None` — молчание.

**Задача 1 — атрибуция на горячем пути** (`a08b648`). `require_token` (`agentgate/api/deps.py`) возвращает `key_id` — `None` для статического токена, который проверяется первым; тип `KeyId = Annotated[str | None, ...]`. Поле `key_id` появилось на `Decision`, `Inspection`, `DecisionRecord` и в протоколе `Stored`. Атрибуция делается в `_answer` через `replace(outcome, key_id=...)` **после** движка и до `Replay.of` — так `Gate.decide` не получает параметра, который не влияет ни на один вердикт (инвариант §7.1.1). `as_cached` атрибуцию не наследует. На время, пока колонки ещё не было, `DecisionRepo.insert` временно исключал `key_id`.

**Задача 3 — колонка, индекс, миграция** (`3ed6a0f`). `DecisionRow.key_id` (`String(26)`, nullable) и генерируемый STORED-столбец `principal = coalesce(key_id, 'token')`; уникальный частичный индекс `ux_decisions_principal_idempotency_key` вместо `ux_decisions_idempotency_key`; индекс `ix_decisions_key_id_id`; mapper исключает `principal` (он вычисляемый); `DecisionRepo.list(key_id=...)` и параметр `?key_id=` в `GET /v1/decisions`; миграция `0007_key_attribution` (`down_revision = "0006"`). Шим из задачи 1 снят.

**Задача 5 — повтор по предъявителю** (`80fdf6d`). `ReplayKey(principal, key)` и `Replay.principal` в `agentgate/domain/replay.py`, `Replay.answers(request, principal)`, хранение под составным ключом `principal:key`, восстановление из базы под ним же (`agentgate/session/replay.py`). Принципал берётся исключительно из auth-зависимости, поэтому предъявитель ключа не может выдать себя за `token`; ULID не содержит `:` и разделитель однозначен.

**Задача 6 — документация** (`62d0f2a`, дополнена `89eb706`). `contracts/README.md`, `docs/connect.md`, `service/README.md`, оба `CLAUDE.md`.

**Правка после повторного ревью** (`89eb706`). `canonical_units` в `agentgate/rules/client_rules.py` отдаёт для `tool: network` домены действия. Шаблон с `/` остаётся путевым и хост назвать не может — то же ограничение, что у MCP-имён.

## 3. Доказательства TDD

- **Задача 2.** `tests/test_schemas.py` — `method: "TRACE"` должен быть отвергнут; падал с «поле принято», потому что валидатора набора ещё не было. `tests/normalize/test_init.py` — метод доезжает до `NormalizedAction` для `network` и остаётся `None` для `shell`. `tests/classify/test_prompt.py` — строка `method=` есть при методе и отсутствует без него (второй тест защищает инвариант §7.1.10).
- **Задача 4.** `tests/rules/test_profile_domain_trusted.py` — таблица по методам; первым падал случай `method=None` (`allow`, ожидалось `None`): без ветки `network` правило судило действие как раньше.
- **Задача 1.** `tests/api/test_deps.py` — «статический токен даёт `key_id: None` при двойном совпадении» падал на распаковке (зависимость возвращала строку, а не пару). `tests/api/test_app.py` — «в ответе `/v1/decide` нет `key_id`» и «в строке ленты он есть».
- **Задача 3.** `tests/store/test_repo.py` (`requires_db`) — «два принципала с одним `Idempotency-Key` дают две строки» падал на нарушении старого уникального индекса по одному столбцу. Он же закрыл растяжку S1: round-trip `insert` → `list` подтвердил, что временный шим задачи 1 снят.
- **Задача 5.** `tests/domain/test_replay.py` — `answers` с чужим принципалом должен вернуть «нет повтора»; падал, потому что ключом был только `Idempotency-Key`. `tests/session/test_replay.py` — восстановление кладёт запись без `key_id` под `token:`.
- **Правка `89eb706`.** `tests/rules/test_client_rules.py` — `rules.deny: ["*"]` на сетевом действии; падал с «правило молчит», потому что у `network` нет argv и путей, а доменов правило не читало. `tests/engine/test_gate_floor.py` — тот же случай сквозь движок: `client.deny` над `profile.domain-trusted`.
- **Латентность.** `tests/rules/test_latency.py` (четыре сценария, включая `tool: network`): 10 000 оценок дают p50 0.041 мс, p95 0.052 мс, max 0.306 мс при бюджете 1 мс. Новая ветка правила — три сравнения, модель не вызывается (инвариант §7.1.11).

## 4. Находки ревью и как закрыты

Ревью задач 1/2/4 (одним заходом), задачи 3, задачи 5 и повторное ревью раунда правок.

- **Потерянный ассерт v4** (near-blocking, задача 3). Правка задела `tests/normalize/test_init.py` и удалила чужой ассерт из v4 — «`record.raw` содержит текст после редакции». Восстановлен в `99aac57`; без него регресс маскирования секретов прошёл бы незамеченным.
- **Индекс не под сортировку ленты** (задача 3). Был `(key_id, ts)`, а лента сортируется по `id`. Та же ошибка, что чинили в v3. Заменён на `ix_decisions_key_id_id`; спека приведена в соответствие.
- **Пустой `?key_id=` фильтровал в пустоту** (задача 3). Пустая строка приходит от клиентов, которые просто не подставили значение; теперь это отсутствие фильтра. Проверено тестом и вживую (см. §5, критерий 2).
- **Литерал `'token'` в двух местах** (should-fix, задача 5). Принципал по умолчанию был записан и в `domain/replay.py`, и в SQL генерируемой колонки. Введён `agentgate/domain/principal.py` (`STATIC_PRINCIPAL`, `principal_of`); SQL колонки в `store/models.py` читает ту же константу. В миграции `0007` литерал остался inline — намеренно: миграция заморожена относительно кода.
- **Клиентские правила не видели `tool: network`** (should-fix, повторное ревью). Дефект существовал до v3.2, но стал наблюдаемым: `rules.deny: ["*"]` не мешал `profile.domain-trusted` выдать `allow`. Закрыто `89eb706`.
- **Нет API-теста на невалидный `method`** (S3). Добавлен: `TRACE` → `ask`, `stage: 0`, `api.invalid-request`, HTTP 200.
- **В миграции нет предупреждения об откате** (задача 3). Комментарий добавлен: `downgrade` после того, как два принципала записали один `Idempotency-Key`, падает на восстановлении старого уникального индекса и требует ручной дедупликации.
- **`method` в `action_hash` перегревает allow-кэш** (нит N1). Не дефект; зафиксировано в `CLAUDE.md` и в разделе для интегратора выше.
- **`Outcome` и `Stored` дублируют пары полей** (нит N3). Отложено: третье общее поле будет сигналом вынести общий протокол.
- **`create_all` не ставит `server_default` у `spans`/`redacted`/`spans_rejected`** (нит, вне диапазона, наследие v4). Безвредно: ORM всегда подставляет значения. Не трогали.
- **Задача 1 тронула `store/repo.py` вне списка файлов** (S2). Вынужденно (временный шим до появления колонки), зафиксировано; шим снят в задаче 3.

Проверено отдельно: `ON CONFLICT` по генерируемой колонке работает на Postgres 16; `\d decisions` совпадает с тем, что строит `create_all`; импортного цикла `store → domain` нет.

## 5. Принятые решения

- **Миграция `0007` поверх v4, а не вторая голова.** v4 оказался на сервере раньше и занял `0006`; линейная цепочка `0005 → 0006 → 0007` проходит одним `upgrade head`, merge-ревизия не нужна.
- **Принципал — генерируемая колонка, а не второе писаемое поле.** Nullable `key_id` не может якорить уникальный индекс (два `NULL` не равны), а `ON CONFLICT` требует именно колонок. Генерируемый STORED `coalesce(key_id, 'token')` решает и то и другое и не даёт полям разъехаться.
- **Атрибуция через `replace` после движка, а не параметром `Gate.decide`.** Так инвариант «ни один вердикт не зависит от `key_id`» держится структурно: движок ключа не видит.
- **`method: None` у сетевого действия никогда не даёт `profile.domain-trusted`.** Fail-closed: неизвестный метод — не «наверное GET». Адаптеры обязаны слать метод, чтобы получить ускорение.
- **Поле на общем `ActionArgs`**, потому что отдельного `NetworkArgs` в дереве нет; побочный эффект — поле видно и в схеме `/v1/inspect`.

## 6. Отношение к v4

v3.2 построен поверх слитого v4. Голова Alembic одна (`0005 → 0006 → 0007`); `key_id` и `principal` встали после `spans`/`redacted`/`spans_rejected`; ни одно решение спеки v4 не отменено и не ослаблено. Единственное пересечение — случайно удалённый ассерт v4 про `record.raw`, восстановленный в `99aac57`.

## 7. Числа приёмки

Локальный прогон 5 сентября 2026 на `89eb706` в worktree `a`. Сервис поднят на `127.0.0.1:8400` с базой `postgresql://agentgate:agentgate@localhost:5433/agentgate` и временной копией `default-dev.yaml` с `network.trusted_allows: true` (`allowed_domains` уже содержит `github.com`); коммитируемый профиль не менялся. Два ключа выпущены из CLI против той же базы и отозваны по завершении:

```
01M1SMSQT64DSH9ZQ38EHFHX8Q  acceptance-a
01M1SMSRDA1KBHESZ1105YT08B  acceptance-b
```

### Критерий 1 — атрибуция

```bash
BODY='{"harness":"t","tool":"shell","raw":"git status","args":{"cwd":"/home/u/repo"},"user_request":"check"}'
curl -s $U/v1/decide -H "Authorization: Bearer $AGK_A" -H 'Content-Type: application/json' -d "$BODY"
curl -s $U/v1/decide -H "Authorization: Bearer $AGENTGATE_TOKEN" -H 'Content-Type: application/json' -d "$BODY"
curl -s "$U/v1/decisions?limit=2" -H "Authorization: Bearer $AGENTGATE_TOKEN" | jq -c '[.items[] | {decision,key_id}]'
```

Оба вызова — `allow`, `stage: 1`, `rule_id: allowlist.readonly`; полный ответ под ключом не содержит поля `key_id`:

```
{"decision":"allow","reason":"","suggest":"","stage":1,"rule_id":"allowlist.readonly","model":null,
 "latency_ms":{"stage1":0,"stage2":null,"total":53},"cached":false,
 "decision_id":"01M1SMVVQMXP9X8WQNPRWNTT8W","protocol":1}
```

Лента:

```
[{"decision":"allow","key_id":null},{"decision":"allow","key_id":"01M1SMSQT64DSH9ZQ38EHFHX8Q"}]
```

### Критерий 2 — фильтр

```bash
curl -s "$U/v1/decisions?key_id=01M1SMSQT64DSH9ZQ38EHFHX8Q" -H "Authorization: Bearer $AGENTGATE_TOKEN" \
  | jq -c '{n:(.items|length), keys:([.items[].key_id]|unique)}'
curl -s "$U/v1/decisions?key_id=&limit=50" -H "Authorization: Bearer $AGENTGATE_TOKEN" \
  | jq -c '{n:(.items|length), keys:([.items[].key_id]|unique)}'
```

```
{"n":1,"keys":["01M1SMSQT64DSH9ZQ38EHFHX8Q"]}
{"n":50,"keys":[null,"01M1SMSQT64DSH9ZQ38EHFHX8Q"]}
```

Фильтр по ключу отдаёт только его строки; пустое значение фильтром не является.

### Критерий 3 — повтор в границах предъявителя

Один и тот же `Idempotency-Key: shared` и одно и то же тело, четыре вызова:

```
A      01M1SMW5FWMBH4NMHZ358R31K8
A      01M1SMW5FWMBH4NMHZ358R31K8
B      01M1SMW5JTDGMTVJ7Z6BSE5SHM
TOKEN  01M1SMW5MTJZBAXF77WVS9DQ2B
```

```sql
select principal, idempotency_key, count(*) from decisions where idempotency_key='shared' group by 1,2;
```

```
 01M1SMSQT64DSH9ZQ38EHFHX8Q | shared | 1
 01M1SMSRDA1KBHESZ1105YT08B | shared | 1
 token                      | shared | 1
```

Повтор своего принципала воспроизводится, чужой — нет; на трёх принципалов ровно три строки.

### Критерий 4 — метод

`{"tool":"network","args":{"cwd":"/home/u/repo","domains":["github.com"],"method":"…"}}` при `trusted_allows: true`:

| Метод | `decision` | `stage` | `rule_id` |
|---|---|---|---|
| `GET` | `allow` | 1 | `profile.domain-trusted` |
| `HEAD` | `allow` | 1 | `profile.domain-trusted` |
| `POST` | `ask` | 2 | `null` |
| `DELETE` | `ask` | 2 | `null` |
| без `method` | `allow` | 2 | `null` |
| `TRACE` | `ask` | 0 | `api.invalid-request` (HTTP 200) |

`TRACE` отвергается с человекочитаемой причиной: `invalid request: args.method: Value error, unknown HTTP method 'TRACE'`. Тот же `GET` с `rules: {"version":1,"deny":["*"]}` даёт `deny`, `stage: 1`, `rule_id: client.deny` — клиентский запрет перебивает доверенный домен (правка `89eb706`).

### Критерий 5 — JSONL

```bash
jq -c '{decision_id,key_id}' service/logs/decisions.jsonl | head -3
```

```
{"decision_id":"01M1SMVVSNHPA9HYR72B38KYRC","key_id":null}
{"decision_id":"01M1SMVVQMXP9X8WQNPRWNTT8W","key_id":"01M1SMSQT64DSH9ZQ38EHFHX8Q"}
{"decision_id":"01M1SMW5FWMBH4NMHZ358R31K8","key_id":"01M1SMSQT64DSH9ZQ38EHFHX8Q"}
```

`grep` по одиннадцати строкам файла: вхождений самого ключа — 0, префикса `agk_` — 0, 64-символьного `key_hash` из `api_keys` — 0. Инвариант §7.1.2 выполнен.

### Критерий 6 — миграция на данных

```bash
AGENTGATE_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate uv run alembic upgrade head
```

`alembic current` → `0007 (head)`. `\d decisions` показывает `key_id character varying(26)`, `principal character varying(26) not null generated always as (COALESCE(key_id, 'token'::character varying)) stored`, индексы `ix_decisions_key_id_id` и `ux_decisions_principal_idempotency_key UNIQUE … WHERE idempotency_key IS NOT NULL`; старый `ux_decisions_idempotency_key` снят. Сразу после миграции:

```
 principal | count
-----------+-------
 token     |   118
```

Все 118 строк, существовавших до миграции, получили `principal = 'token'`; уникальность не нарушена.

### Контрольная группа бенчмарка

Критерий §7.3.5 (прогон `benchmark/` на профиле без `trusted_allows` и на запросах без `method`, ноль расхождений с прогоном до v3.2) в рамках этой задачи **не выполнялся**: `benchmark/` вне границ работы данной сессии. Опорой служат инварианты §7.1.9–7.1.10, закрытые тестами: запрос без `method` не меняет ни промпт, ни вердикт, а `trusted_allows: false` оставляет цепочку прежней.

### Финальная проверка

```bash
cd service && uv run python scripts/export_contracts.py && uv run python scripts/export_openapi.py \
  && git diff --exit-code ../contracts        # чисто
AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test_a uv run pytest -q
# 1509 passed in 21.05s
```

## 8. Отложено

Из §7.5 спеки и по итогам ревью:

- Привязка `Idempotency-Key` к сессии — сделана только привязка к принципалу.
- Блокировка ключа «в полёте»: гонка двух повторов с одним `Idempotency-Key` по-прежнему сдвигает счётчики сессии дважды, строка при этом одна.
- Единый на весь сервис отзыв ключа: кэш проверки остаётся per-process, задержка отзыва — по худшему из воркеров.
- Метод у `tool: shell` из отдельного поля: там он читается из argv, второго источника не будет.
- Фильтр ленты «только строки без ключа».
- Строгий режим `api-keys.md` (non-localhost игнорирует статический токен).
- Общий протокол для `Outcome` и `Stored` — при следующем общем поле.
- Откат `0007` после того, как два принципала записали один `Idempotency-Key`, требует ручной дедупликации.
