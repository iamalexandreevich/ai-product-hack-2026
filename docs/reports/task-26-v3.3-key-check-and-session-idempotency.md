# Задача 26: AgentGate v3.3 — форма `key_id` как инвариант базы, `Idempotency-Key` в границах предъявителя и сессии

Дата: 2026-09-06. Ветка: `feat/v3.3-key-check-session-idempotency` от `main` (`4abf185`), голова Alembic до работы — `0007`.

Спека: `docs/superpowers/service/specs/2026-09-05-agentgate-v3.3-key-check-and-session-idempotency-design.md`.
План: `docs/superpowers/service/plans/2026-09-05-agentgate-v3.3-key-check-and-session-idempotency.md`.
Предшественник: `docs/reports/task-25-v3.2-key-attribution-and-network-method.md`, раздел «Отложено» — оба пункта закрыты здесь.

Задача владельца (2026-09-06): «сделай follow-up: CHECK на key_id и ключ по сессии». Провод не изменился ни одним полем: схемы `contracts/` перегенерированы и совпали байт в байт, изменён только текст `contracts/README.md`.

---

## 1. Как выполнялось

Спека и план — за один проход, четыре задачи в трёх волнах: 1 ‖ 2 → 3 → 4. Исполнители — Sonnet в отдельных worktree (`-wt/a`, `-wt/b`), слияние ff после rebase, ревью на Opus по уже слитому коду. Условие параллельности волны 1: у задач 1 и 2 не было ни одного общего файла.

| Волна | Задача | Что принадлежало | Файлы |
|---|---|---|---|
| 1 | 1 — форма `key_id` | `domain/principal.py`, `store/keys.py`, `store/models.py` (только `ApiKeyRow` и `CheckConstraint` в `DecisionRow`), миграция `0008` | `tests/domain/test_principal.py`, `tests/store/test_keys.py`, `tests/store/test_repo.py` |
| 1 | 2 — сессия в ключе повтора | `domain/replay.py`, `session/replay.py`, `api/app.py` | `tests/domain/test_replay.py`, `tests/session/test_replay.py`, `tests/api/test_app.py` |
| 2 | 3 — уникальность в базе по тройке | `store/models.py` (индекс), `store/repo.py`, миграция `0009` | `tests/store/test_repo.py` |
| 3 | 4 — документация, приёмка, отчёт | `contracts/README.md`, `docs/connect.md`, `service/README.md`, `service/CLAUDE.md`, корневой `CLAUDE.md`, этот отчёт | — |

Общий файл у задач 1 и 3 — `store/models.py`, но они шли в разных волнах, а не параллельно.

## 2. Что построено, по задачам

**Задача 1 — форма `key_id` (`c4451cc`).**
- `service/agentgate/domain/principal.py`: константа `KEY_ID_PATTERN = r"^[0-9A-HJKMNP-TV-Z]{26}$"` (Crockford base32, ровно длина ULID) рядом со `STATIC_PRINCIPAL = "token"`, и охрана `ensure_key_id_shape(value)`. Единственное место, знающее и литерал принципала, и форму, которая с ним структурно не пересекается.
- `service/agentgate/store/keys.py`: `mint_key_id()` — единственное место, где рождается id ключа; оно же его проверяет, и `ApiKeyRepo.create` зовёт только его.
- `service/agentgate/store/models.py`: `CheckConstraint` `ck_api_keys_id_ulid` на `api_keys.id` и `ck_decisions_key_id_ulid` на `decisions.key_id` (`NULL` разрешён), обе читают `KEY_ID_PATTERN`.
- `service/migrations/versions/0008_key_id_shape.py`: те же два ограничения. Паттерн намеренно продублирован литералом — миграция обязана быть неподвижным слепком; равенство литерала константе проверяет тест.

**Задача 2 — сессия в ключе повтора (`c96dd14`, доработано `6256f20`).**
- `service/agentgate/domain/replay.py`: `ReplayKey` стал тройкой `(principal, session, key)`; `ReplayKey.of(key_id, session_id, key)`; `storage_key()` кодирует тройку JSON-массивом.
- `service/agentgate/session/replay.py`: восстановление из Postgres кладёт запись под тройной ключ.
- `service/agentgate/api/app.py`: `_answer` передаёт `parsed.session_id` в `ReplayKey.of`.
- `Replay` **не** получил поля сессии: `session_id` входит в тело запроса, значит уже в `identity_digest`.

**Задача 3 — уникальность в базе (`bdae128`, доработано `7efa216`).**
- `service/agentgate/store/models.py`: уникальный частичный индекс `ux_decisions_principal_session_idempotency_key` по `(principal, session_id, idempotency_key)` с `postgresql_nulls_not_distinct=True` вместо прежней пары.
- `service/agentgate/store/repo.py`: `ON CONFLICT` по тройке; `load_replayable` несёт `session_id` обратно.
- `service/migrations/versions/0009_session_idempotency.py`: снятие старого индекса и создание нового; `downgrade` сужающий и падает на строках, которые после апгрейда стали законными.
- `service/agentgate/store/writer.py`: предупреждение о пропущенной строке теперь называет область — «для этого принципала и этой сессии».

**Задача 4 — документация и приёмка (этот коммит).** `contracts/README.md` (новый раздел v3.3 плюс правка устаревшего утверждения «ключ глобален для сервиса» в разделе v2), `docs/connect.md`, `service/README.md` (миграции `0008` и `0009`), `service/CLAUDE.md` (карта модулей и инварианты), корневой `CLAUDE.md` (ссылки, «Что построено», три правки и два новых пункта в «Известных ограничениях», один устаревший пункт удалён).

## 3. Доказательства TDD

Базовая линия перед работой — 1509 тестов. После слияния всех волн и доработок по ревью — **1538 passed in 23.76s**.

| Задача | Тест, падавший первым | Ошибка до кода | Что сделало его зелёным |
|---|---|---|---|
| 1 | `tests/domain/test_principal.py::test_the_static_principal_is_not_a_key_id_shape` | `ImportError: cannot import name 'KEY_ID_PATTERN'` | константа и `ensure_key_id_shape` в `domain/principal.py` |
| 1 | `tests/store/test_repo.py` — вставка `key_id` вне формы | строка вставлялась без ошибки (ожидался `IntegrityError`) | `CheckConstraint` в модели и миграция `0008` |
| 2 | `tests/domain/test_replay.py` — `ReplayKey.of(...)` с тремя аргументами | `TypeError: of() takes 3 positional arguments but 4 were given` | поле `session` и новая сигнатура `of` |
| 2 | `tests/api/test_app.py` — тот же ключ из `s2` даёт другой `decision_id` и вторую запись у писателя | запись была одна: вторая уходила в конфликт по паре | `_answer` передаёт `session_id`, ключ хранилища стал тройкой |
| 3 | `tests/store/test_repo.py` — один ключ в двух сессиях даёт две строки | вторая вставка возвращала `False` (`ON CONFLICT DO NOTHING` по паре) | тройной индекс с `NULLS NOT DISTINCT` и миграция `0009` |
| 3 | `tests/store/test_repo.py` — два бессессионных вызова с одним ключом дают одну строку | без `NULLS NOT DISTINCT` вставлялись обе | флаг на индексе |

Шесть DB-тестов, до v3.3 использовавших восьмисимвольные заглушки вида `01HZKEYA`, перестали проходить `CHECK` и переведены на настоящие ULID (фикстуры `KEY_A`/`KEY_B` в `tests/store/test_repo.py`) — код заставил починить тесты, а не наоборот.

## 4. Находки ревью и как закрыты

| Находка | Волна | Как закрыта |
|---|---|---|
| Сентинел `-` для бессессионного вызова делил пространство с сессией, буквально названной `-` | 1 | `6256f20`: сентинел убран целиком, бессессионный вызов — JSON `null`; `NO_SESSION` удалён |
| Кодировка ключа хранилища не была закреплена ни одним тестом — форму можно было молча сменить | 1 | `6256f20`: тесты пиннят точный вид `storage_key()` (JSON-массив, без пробелов, `ensure_ascii=False`) |
| Комментарий в миграции `0008` ссылался на несуществующий тест round-trip | 1 | `6256f20`: комментарий указывает на реальный `test_the_migration_repeats_the_pattern_literally` |
| Предупреждение писателя о пропущенной строке не называло область конфликта | 3 | `7efa216`: текст называет принципал и сессию |
| Не было теста на то, что бессессионная строка и строка с сессией не сталкиваются | 3 | `7efa216`: тест добавлен |

Блокирующих находок не было ни в одной волне.

## 5. Принятые решения

1. **`CHECK`, а не `FOREIGN KEY`.** FK связал бы горячую запись решения с таблицей ключей; отозванный ключ мы храним вечно ради аудита. Нужна форма, а не ссылочная целостность.
2. **Паттерн живёт один раз** — в `domain/principal.py`; единственный дубликат литералом в миграции `0008`, потому что миграция — слепок; равенство проверяет тест.
3. **`0008` падает, а не чинит.** `key_id` вне формы означает запись в базу мимо сервиса; молчаливая правка уничтожила бы след инцидента.
4. **Ключ хранения повтора — JSON-массив `[principal, session, key]`**, а не склейка через `:` (как предполагал план). Ни один символ внутри `session_id` или клиентского ключа не может быть принят за разделитель, поэтому описанных в плане ограничений («сессия с именем `-`», «`:` в `session_id`») не существует вовсе — их и нет в документации.
5. **`Replay` не получает поля сессии.** `identity_digest` — sha256 всего тела без `metadata`, а `session_id` в теле; второе хранение того же знания разошлось бы с дайджестом при первой правке. Отличие от `principal`: тот в тело не входит вовсе.
6. **Две миграции, одна голова.** `0008` и `0009` линейны (`0007→0008→0009`); разделены только чтобы два параллельных исполнителя не правили один файл. Цена разделения нулевая: `0009` ничего не читает из `0008`.
7. **`NULLS NOT DISTINCT` вместо второго генерируемого столбца.** Postgres 16 и SQLAlchemy 2.0.52 в дереве это умеют; фолбэк `session_scope = coalesce(session_id, '-')` не понадобился.

## 6. Что изменилось для интегратора

Граница `Idempotency-Key` — предъявитель **и** сессия. Одна строка ключа в двух сессиях больше не съедает ни аудит, ни повтор: каждая сессия получает свой `decision_id`, свою строку в ленте и свой рабочий повтор. Бессессионные вызовы одного предъявителя с одной строкой ключа делят одно общее пространство — это законное состояние. На проводе не изменилось ничего: ни поля, ни заголовки, ни элемент `GET /v1/decisions`; `contracts/*.json` и `contracts/openapi.yaml` не изменились.

## 7. Числа приёмки

Локальный прогон 2026-09-06: Postgres в контейнере `service-db-1` (localhost:5433, база `agentgate`), сервис на `127.0.0.1:8400`, один API-ключ, выпущенный CLI под меткой `acceptance-v33-run` и отозванный после прогона.

**Миграции.**

```
$ AGENTGATE_DB_URL=…/agentgate uv run alembic upgrade head   # 0007 → 0009, без ошибок
$ AGENTGATE_DB_URL=…/agentgate uv run alembic heads
0009 (head)
$ docker exec service-db-1 psql -U agentgate -d agentgate -c "\d decisions"
"ux_decisions_principal_session_idempotency_key" UNIQUE, btree (principal, session_id, idempotency_key) NULLS NOT DISTINCT WHERE idempotency_key IS NOT NULL
"ck_decisions_key_id_ulid" CHECK (key_id IS NULL OR key_id::text ~ '^[0-9A-HJKMNP-TV-Z]{26}$'::text)
```

Ни одна историческая строка не остановила `0008`: в базе с данными v3.2 `key_id` вне формы не нашлось, как и ожидалось.

**Три вызова `POST /v1/decide` под одним ключом `Idempotency-Key: v33` и одним API-ключом** (`s1`, `s2`, снова `s1`, тело идентично внутри пары):

```
01M1SRBRS8A1FRTSPQGNPKRN6P allow   # s1
01M1SRBRZW9WHZ0MHG6P5K5346 allow   # s2 — другое решение
01M1SRBRS8A1FRTSPQGNPKRN6P allow   # s1 снова — тот же decision_id, повтор
```

**Два бессессионных вызова под ключом `v33ns`:**

```
01M1SRBSDJPJ03VB8EHT3W4T9M allow
01M1SRBSDJPJ03VB8EHT3W4T9M allow   # тот же — повтор, одна строка
```

**Тот же `v33` в `s1`, но статическим токеном вместо API-ключа:** `01M1SRBSX5JDDD0H1VBC1TZE9H` — отдельное решение, принципалы не сталкиваются.

**Строки в базе:**

```
$ psql -c "select principal, session_id, idempotency_key, count(*) from decisions
           where idempotency_key in ('v33','v33ns') group by 1,2,3 order by 3,2;"
         principal          | session_id | idempotency_key | count
----------------------------+------------+-----------------+-------
 01M1SRBQ0DTFX32GBYPVK3R9H5 | s1         | v33             |     1
 token                      | s1         | v33             |     1
 01M1SRBQ0DTFX32GBYPVK3R9H5 | s2         | v33             |     1
 01M1SRBQ0DTFX32GBYPVK3R9H5 |            | v33ns           |     1
```

`GET /v1/decisions?limit=10` показывает те же четыре строки сверху ленты, с `key_id` ULID у трёх и `null` у вызова статическим токеном.

**`CHECK` отвергает литерал `token`** (обе проверки, каждая внутри откаченной транзакции):

```
ERROR:  new row for relation "api_keys" violates check constraint "ck_api_keys_id_ulid"
ERROR:  new row for relation "decisions" violates check constraint "ck_decisions_key_id_ulid"
```

**Финальная проверка.**

```
$ uv run python scripts/export_contracts.py && uv run python scripts/export_openapi.py
$ git diff --stat ../contracts
 contracts/README.md | 14 +++++++++++++-   # изменена только проза
$ AGENTGATE_TEST_DB_URL=…/agentgate_test_a uv run pytest -q
1538 passed in 23.76s
```

## 8. Что код заставил сделать

- Шесть DB-тестов на восьмисимвольных заглушках `01HZKEYA` перестали проходить новый `CHECK` — переведены на настоящие ULID.
- `Replay` не получил поля сессии, потому что `identity_digest` её уже несёт; попытка добавить поле была бы вторым хранением одного знания.
- Две миграции вместо одной — исключительно ради параллельных исполнителей; линейность и одна голова сохранены.
- Сентинел `-` из спеки (решения 5 и 6) не пережил ревью: JSON-кодирование тройки закрывает тот же вопрос без сентинела и без описанных в плане ограничений.
- Переменная окружения для URL миграций — `AGENTGATE_DB_URL` (поле настроек `db_url`), а не отдельная переменная Alembic; тестовая база задаётся `AGENTGATE_TEST_DB_URL`.

## 9. Отложено (§7.3 спеки)

- Привязка `Idempotency-Key` к `call_id` или к чему-либо ещё, кроме принципала и сессии.
- Блокировка ключа «в полёте»: гонка двух одновременных повторов по-прежнему сдвигает счётчики сессии дважды (строка в базе одна).
- FK `decisions.key_id → api_keys.id` — сознательно нет.
- Строгий вариант `api-keys.md` («non-localhost принимает только ключи, статический токен игнорируется»).
- Единый на весь сервис отзыв ключа: кэш проверки остаётся per-process.
- Отказ от бессессионных вызовов: `session_id: null` остаётся законным состоянием со своим пространством повторов.

## Примечание о деплое

Выкатка `e3c7942` завершилась ошибкой `make deploy-main`, хотя сервер поднялся здоровым на `0009` с обоими `CHECK` и индексом по тройке. Причина — не миграции v3.3, а гонка в рецепте деплоя: `Dockerfile` применяет `alembic upgrade head` при старте контейнера, а `Makefile` запускал его второй раз через `compose exec`; проигравший процесс падал на `DuplicateObjectError` для `ck_api_keys_id_ulid`. Та же гонка объясняет «Error 137/2» на выкатках v3.1 и v3.2. Дубль убран из `Makefile`: миграционные ворота деплоя — проверка `/healthz`, которая проходит только после старта приложения, то есть после миграций.
