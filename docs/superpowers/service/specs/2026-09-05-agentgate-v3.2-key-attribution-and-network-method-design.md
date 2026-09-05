# AgentGate v3.2 — атрибуция решения к ключу, `Idempotency-Key` в границах ключа, `method` у сетевого действия

Статус: принято владельцем 2026-09-05. Спека закрыта, открытых вопросов нет.
План: `docs/superpowers/service/plans/2026-09-05-agentgate-v3.2-key-attribution-and-network-method.md`.
База: `main` (`cbe3073`), поверх v4. Ветка v4 (Context Guard) слита в `main` и развёрнута на сервере; v3.2 строится на ней, отдельных голов Alembic нет.
Смежные документы: `api-keys.md` (раздел «Что попадает в решения и в логи»), спека v3 §4 (повтор и `DecisionRecord`), спека v3.1 §5 (доверенные домены), `docs/reports/task-23-v3.1-strictness-mcp-domains.md` §7, `docs/reports/task-24-v4-context-guard.md`, корневой `CLAUDE.md` — раздел «Известные ограничения», три пункта из которого закрываются здесь.

---

## 1. Цель и границы

### 1.1. Три дыры

**Первая: решение не привязано к клиенту.** `api-keys.md` требует, чтобы `key_id` (никогда сам ключ и никогда его хэш) попадал в `DecisionRow` и в JSONL. Сегодня `api/deps.py` вычисляет `key_id` только чтобы обновить `last_used_at`, и тут же его теряет; docstring `ApiKeyRow` (`store/models.py`) прямо называет это незакрытым долгом. Следствие: выдав ключи трём интеграторам, оператор не может ответить на вопрос «чьи это решения» и не может отозвать ключ, глядя на то, что он натворил.

**Вторая: `Idempotency-Key` глобален для сервиса.** Ключ выбирает клиент, а пространство ключей одно на весь сервис. Повтор защищён от подмены только совпадением дайджеста запроса (`Replay.answers`), то есть чужой клиент со случайно совпавшим ключом получит не чужой вердикт, а собственное решение, — но два разных клиента делят одну уникальную запись в базе: `ux_decisions_idempotency_key` уникален по одному столбцу, и второй клиент с тем же ключом молча не запишет строку (`DecisionRepo.insert` вернёт `False`, писатель напишет warning). Это потеря аудита, спровоцированная посторонним.

**Третья: сетевое действие не говорит метод.** `tool: network` несёт только `args.domains`. `ProfileDomainTrustedRule` (v3.1 §5.2) умеет отвечать `allow` лишь на `tool: shell`, потому что метод там читается из argv (`curl -X`). У `tool: network` метода нет вовсе, значит и различить `GET https://github.com/o/r` от `DELETE https://github.com/o/r` нечем — оба уходят на ступень 2. Спека v3.1 §5.1 использует ровно этот пример как обоснование того, что домен не является разрешением; для `tool: network` это обоснование пока не выражено в коде, потому что выражать нечем.

### 1.2. Что делает v3.2

1. `key_id` аутентифицированного вызова доезжает до `DecisionRecord`, до строки Postgres и до JSONL; лента `GET /v1/decisions` его показывает и умеет по нему фильтровать.
2. Повтор по `Idempotency-Key` живёт в границах предъявителя: ключ пары — `(principal, Idempotency-Key)`, где `principal` — это `key_id` либо константа `"token"` для статического `AGENTGATE_TOKEN`. Уникальность в базе — по той же паре.
3. `args.method` появляется у `tool: network`, доезжает до `NormalizedAction`, попадает в `[ACTION]` и позволяет `ProfileDomainTrustedRule` ответить `allow` на сетевое чтение — только `GET`/`HEAD`, только при явно разрешённом домене, только при `trusted_allows`.

### 1.3. Чего v3.2 не трогает

- Форма `DecideResponse` и `InspectResponse` не меняется ни одним полем. `key_id` — свойство записи, а не провода: клиент и так знает, каким ключом он ходил, а отдавать ему обратно идентификатор credential'а — лишняя поверхность.
- Каскад решения, ступень 2, инспект, пол строгости, MCP-правила — без изменений.
- Строгий вариант `api-keys.md` («на non-localhost принимается только ключ, статический токен игнорируется») по-прежнему не реализуется: ключи аддитивны, как решено в v1.5. Здесь это важно тем, что `key_id: null` — законное состояние строки, а не признак сбоя.
- Отзыв ключа по-прежнему доходит за время TTL кэша проверки; per-process природа этого кэша не меняется.

---

## 2. Решения

| # | Решение | Почему так, а не иначе |
|---|---|---|
| 1 | `key_id` навешивается на исход **после** движка (`dataclasses.replace` в `api/app.py::_answer`), а не передаётся в `Gate.decide`/`Inspector.inspect` | Движок не знает и не должен знать об аутентификации: `key_id` не влияет ни на один вердикт. Тем же приёмом уже навешивается `idempotency_key` — одна механика вместо двух. Сигнатуры `Gate.decide`/`Inspector.inspect` остаются `(request) -> outcome`, и `RouteSpec.run` не меняет тип. |
| 2 | `key_id` — поле `Decision`, `Inspection`, протокола `Stored` и `DecisionRecord`; не поле `Verdict` | `Verdict` — это исход суждения. Кто предъявил credential, к суждению отношения не имеет; поле там сделало бы `Verdict` местом, где смешаны политика и транспорт. |
| 3 | Зависимость аутентификации возвращает `key_id: str \| None`, маршруты `decide`/`inspect` принимают его параметром | `dependencies=[auth]` выбрасывает возвращаемое значение. Чтобы значение дошло, оно должно быть параметром маршрута. Читающие маршруты (`/v1/decisions`, `/v1/profiles/{id}`) значение не используют и остаются на `dependencies=[auth]`. |
| 4 | `principal = key_id or "token"` — чистая функция в `domain/replay.py`, а не поле запроса | Один источник знания «кому принадлежит повтор». Коллизия невозможна: `key_id` — ULID (26 символов Crockford base32, заглавные и цифры), строка `"token"` в это множество не попадает ни при каком ключе. |
| 5 | В базе — **генерируемый** столбец `principal` (`GENERATED ALWAYS AS (coalesce(key_id, 'token')) STORED`), уникальный частичный индекс по `(principal, idempotency_key)` | Уникальный индекс по `(key_id, idempotency_key)` не работает: в Postgres `NULL` не равен `NULL`, поэтому два вызова со статическим токеном и одним ключом оба вставились бы — ровно та потеря уникальности, которую мы чиним. Индекс по выражению (`coalesce(...)`) сработал бы, но вывод конфликта в `ON CONFLICT` пришлось бы писать выражением через `text()`, а любая разница в форматировании между кодом и индексом — молчаливая ошибка вставки. Генерируемый столбец даёт обычные столбцы в `index_elements`, не добавляет второго записываемого поля (значит, рассинхронизации с `key_id` быть не может по построению) и заполняет исторические строки сам при `ADD COLUMN`. |
| 6 | Ключ хранилища повторов — строка `"{principal}:{idempotency_key}"`, собираемая типом `ReplayKey` | Протокол `ReplayStore` остаётся с одним строковым ключом: ни `TtlStore`, ни его тесты не меняются. Знание о склейке живёт в одном типе, которым пользуются и API-слой, и восстановление из Postgres. |
| 7 | `Replay` дополнительно хранит `principal`, и `answers()` сверяет его | Составной ключ уже разделяет пространства, проверка в `answers` — второй замок на случай реализации хранилища, которая ключи схлопывает. Одна строка, и она делает утечку чужого вердикта невозможной независимо от хранилища. |
| 8 | `method` — поле `ActionArgs`, а не отдельная модель `NetworkArgs` | Модели `NetworkArgs` в дереве нет (в отличие от `McpArgs`): `tool: network` сегодня читает `args.domains` из общего `ActionArgs`. Заводить отдельную модель ради одного поля — менять форму `args` у всех инструментов ради косметики. Поле необязательное и для прочих инструментов игнорируется. |
| 9 | `method: None` никогда не квалифицируется | Fail-closed: «метод неизвестен» — это не «метод безопасен». Адаптер, не научившийся присылать `method`, получает ровно сегодняшнее поведение (ступень 2), а не новый `allow`. |
| 10 | Миграция называется `0007_key_attribution`, `revision = "0007"`, `down_revision = "0006"` | v4 уже в `main` и занял `0006` (`0006_v4_spans_and_redaction.py`, `revision = '0006'`). v3.2 продолжает ту же линейную цепочку следующим номером: одна голова, никакой merge-ревизии, `alembic upgrade head` проходит без оговорок. Нумерация без суффикса — как у всех пяти предыдущих файлов. |

---

## 3. Атрибуция решения к ключу

### 3.1. Путь `key_id`

```
Authorization: Bearer <credential>
        │
        ▼
api/deps.py::require_token  ──►  key_id: str | None      (None: статический токен или dev-режим)
        │
        ▼
api/app.py::_answer         ──►  replace(outcome, key_id=key_id)
        │
        ├──► Replay.of(outcome.to_record())  ──►  запись повтора уже с key_id
        └──► writer.write(outcome)           ──►  DecisionRow.key_id + строка JSONL
```

Порядок внутри `_answer` фиксирован: `key_id` навешивается **до** `Replay.of(...)`, иначе повтор восстановится с `key_id: null` и после перезапуска сервиса атрибуция у повторов пропадёт.

### 3.2. Зависимость аутентификации

`make_require_token` возвращает функцию, чей тип результата становится `str | None`:

```python
async def require_token(
    background: BackgroundTasks,
    authorization: str | None = Header(default=None, include_in_schema=False),
) -> str | None:
```

Три исхода:

| Ситуация | Результат | `DecisionRow.key_id` |
|---|---|---|
| Токен в настройках не задан (dev, localhost) | `None` | `NULL` |
| Bearer совпал со статическим `AGENTGATE_TOKEN` | `None` | `NULL` |
| Bearer совпал с действующим выданным ключом | `key_id` (ULID) | этот ULID |
| Bearer не совпал ни с чем | `HTTPException(401)` | строки нет |

Проверка статического токена идёт первой и остаётся `secrets.compare_digest`, как была. Порядок важен и в другом смысле: если один и тот же bearer каким-то образом равен и статическому токену, и хэшу выданного ключа, побеждает статический токен, и решение записывается без атрибуции — молчаливая потеря атрибуции безопаснее молчаливой атрибуции не тому.

### 3.3. Форма записи

- `DecisionRecord.key_id: str | None = None` — последнее поле модели, после добавленных v4 `spans`/`redacted`/`spans_rejected`; «ULID выданного API-ключа, которым был аутентифицирован вызов; `null` для статического токена и для локального режима без токена».
- `DecisionRow.key_id` — `String(26)`, nullable, последним столбцом после `spans_rejected`; индекс `ix_decisions_key_id_ts` по `(key_id, ts)`: типичный вопрос ленты — «последние решения этого клиента».
- Внешнего ключа на `api_keys.id` нет: удалять выданный ключ мы не умеем (только `revoked_at`), но и запрещать это на уровне схемы ради строки аудита не нужно — запись переживает ключ намеренно.
- JSONL получает поле само: `JsonlDecisionWriter` пишет `stored.to_record().model_dump(mode="json")`.
- `key_id` не входит в `identity_digest()` запроса: дайджест считается по телу запроса, а credential живёт в заголовке. Разделение принципалов делает `principal` в ключе повтора, а не дайджест.

### 3.4. Лента

`GET /v1/decisions` получает необязательный `?key_id=`: точное совпадение по столбцу. Пустое значение и отсутствие параметра — одно и то же (фильтра нет); отфильтровать «решения без ключа» нельзя, и это осознанно: такой фильтр смешал бы «статический токен» с «dev без токена», а различить их запись не позволяет.

Элементы ленты — `DecisionRecord`, поэтому `key_id` в ответе появляется автоматически.

---

## 4. `Idempotency-Key` в границах предъявителя

### 4.1. Принципал

```python
STATIC_PRINCIPAL = "token"


def principal_of(key_id: str | None) -> str:
    """Who a replay belongs to: the issued key's id, or the static token."""
    return key_id or STATIC_PRINCIPAL
```

Живёт в `domain/replay.py` — там же, где `Replay`, потому что это часть определения «чей повтор».

### 4.2. Ключ хранилища

```python
@dataclass(frozen=True)
class ReplayKey:
    principal: str
    key: str

    @classmethod
    def of(cls, key_id: str | None, key: str) -> "ReplayKey":
        return cls(principal_of(key_id), key)

    def storage_key(self) -> str:
        return f"{self.principal}:{self.key}"
```

Разделитель `:` неоднозначности не создаёт: `principal` — либо ULID фиксированной длины 26, либо `"token"`, а всё, что справа от первого `:`, целиком принадлежит клиентскому ключу. Восстановить пару из строки нам нигде не требуется, поэтому экранирование не нужно.

### 4.3. `Replay`

`Replay` получает поле `principal: str`; `Replay.of(record)` берёт его из `principal_of(record.key_id)`; `answers` получает второй аргумент:

```python
def answers(self, request: DecideRequest | InspectRequest, principal: str) -> bool:
    return self.principal == principal and self.request_digest == request.identity_digest()
```

Оба условия обязательны, и ни одно из них не лишнее: дайджест защищает от «тот же клиент, другой запрос», принципал — от «тот же ключ, другой клиент».

### 4.4. Восстановление из Postgres

`PersistentReplayStore.restore()` кладёт запись под `ReplayKey.of(record.key_id, record.idempotency_key).storage_key()`. `Replay.of(record)` берёт принципала из той же строки, так что после рестарта повтор остаётся ровно у того клиента, у которого он был до него.

### 4.5. Уникальность в базе

Было:

```python
Index("ux_decisions_idempotency_key", "idempotency_key", unique=True,
      postgresql_where=text("idempotency_key IS NOT NULL"))
```

Стало:

```python
principal: Mapped[str] = mapped_column(
    String(26), Computed("coalesce(key_id, 'token')", persisted=True)
)

Index("ux_decisions_principal_idempotency_key", "principal", "idempotency_key", unique=True,
      postgresql_where=text("idempotency_key IS NOT NULL"))
```

Частичная семантика сохранена: строка без ключа идемпотентности в индекс не входит вовсе, поэтому обычные решения по-прежнему не конкурируют за уникальность. `DecisionRepo.insert` меняет цель вывода конфликта на `[table.c.principal, table.c.idempotency_key]` с тем же `index_where`.

`principal` — генерируемый столбец, поэтому его нельзя вставлять; в `values` он не попадает, потому что `DecisionRecord` такого поля не имеет. `store/mapper.py::record_from_row` перечисляет столбцы таблицы, поэтому `principal` из него исключается явно — рядом с `metadata`, тем же механизмом.

### 4.6. Что остаётся как было

- TTL повтора — `allow_cache_ttl_seconds` (сутки по умолчанию), поведение по истечении не меняется: повтор после TTL решается заново, а его строка не попадает в базу из-за уникального индекса — теперь уже по паре. Ограничение остаётся записанным в корневом `CLAUDE.md`, меняется только его формулировка (пара вместо одного столбца).
- Гонка двух повторов с одним ключом по-прежнему сдвигает счётчики сессии дважды.
- Ключ длиннее 128 символов по-прежнему игнорируется целиком.
- Отказ хранилища повторов по-прежнему означает «повтора нет», никогда 5xx.

---

## 5. `method` у сетевого действия

### 5.1. Контракт

`ActionArgs` получает поле:

```python
method: str | None = Field(
    default=None,
    description=(
        "HTTP method of a `network` action, uppercase: GET, HEAD, POST, PUT, PATCH, "
        "DELETE, OPTIONS. Optional; omitted means unknown, and unknown never earns a "
        "positive verdict. Ignored for tools other than `network`."
    ),
)
```

Валидация: значение приводится к верхнему регистру и проверяется по закрытому множеству `HTTP_METHODS = {"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"}`. Незнакомое значение — обычная ошибка валидации, то есть fail-closed `ask` с `rule_id: api.invalid-request` (отдельного `rule_id` метод не заслуживает: это опечатка в поле, а не превышение лимита).

Множество закрыто намеренно: расширяемое поле «любая строка» дало бы правилу задачу «доказать, что произвольный глагол — чтение», а закрытый список переносит эту задачу в схему, где она решается один раз.

### 5.2. Нормализация

`NormalizedAction` получает `method: str | None = None`; `normalize()` заполняет его только для `tool: network`:

```python
if req.tool is Tool.network:
    domains = sorted({d.lower() for d in req.args.domains})
    return NormalizedAction(tool=req.tool, cwd=cwd, raw=req.raw, domains=domains, method=req.args.method)
```

Для `tool: shell` метод по-прежнему читается из argv правилом — второго источника знания не заводится: у shell-действия `action.method` всегда `None`.

Побочный, но обязательный к упоминанию эффект: `action_hash()` считается по `to_dict()`, поэтому новое поле меняет хэш **всех** действий. Allow-кэш прогревается заново — как при смене `profile_hash` в v3.1. Данные мигрировать не нужно; записи в `allow_cache` с прежними хэшами просто не совпадут и истекут по TTL.

### 5.3. Правило

`ProfileDomainTrustedRule` получает вторую ветку. Общая часть (условие «сеть доверяет»: `trusted_allows`, режим из `{allowlist, ask}`, непустые `action.domains`, каждый домен разрешён) остаётся одной функцией `_network_trusts` для обеих веток. Дальше ветки расходятся:

```python
def evaluate(self, action: NormalizedAction, policy: Policy) -> Verdict | None:
    if not self._network_trusts(action, policy):
        return None
    if action.tool is Tool.network:
        return Verdict.allow(self.id) if action.method in _READ_ONLY_METHODS else None
    if not self._shape_is_readable(action):
        return None
    ...
```

Условия ветки `network`, все обязательные:

1. `network.trusted_allows` включён.
2. `network.mode` ∈ {`allowlist`, `ask`}. Режим `open` не квалифицируется никогда: там `allowed_domains` ни на что не влияет, и читать в этом списке обещание оператора было бы выдумкой (то же обоснование, что в v3.1 §5.2).
3. `action.domains` непусто.
4. Каждый домен разрешён явно — `domain_allowed(d, network.allowed_domains)` (точное совпадение или поддомен).
5. `action.method` ∈ {`GET`, `HEAD`}. `None` не квалифицируется.

Чего в этой ветке нет и почему: путей нет (`tool: network` их не несёт), argv нет (нечего разбирать на флаги), `safe_prefixes` неприменимы (это префиксы командной строки). Одиннадцать условий shell-ветки остаются целиком у shell-ветки; смешивать списки нельзя — они про разные формы действия.

Правило по-прежнему никогда не отвечает `deny`: запрет неразрешённого домена — работа `ProfileDomainRule`, стоящего в цепочке выше. Позиция `ProfileDomainTrustedRule` в `STAGE1` не меняется.

### 5.4. Что видит ступень 2

`[ACTION]` печатает метод, когда он есть:

```
[ACTION] tool=network cwd="/home/u/repo"
method=GET
paths=[] domains=["github.com"]
```

Строка `method=` отсутствует целиком, когда `action.method is None`, поэтому промпт всех сегодняшних запросов не меняется ни на байт. Значение печатается без экранирования: оно уже прошло закрытое множество схемы и не может содержать перевода строки. Закрытый список содержимого промпта (`service/CLAUDE.md`, docstring `classify/prompt.py`) не расширяется: `method` — часть `[ACTION]`, а не новый слот.

---

## 6. Контракт

| Артефакт | Изменение |
|---|---|
| `contracts/decide_request.schema.json` | `ActionArgs.method` — новое необязательное поле. Аддитивно: старый клиент валиден. |
| `contracts/openapi.yaml` | то же поле; новый query-параметр `key_id` у `GET /v1/decisions`; новое поле `key_id` у схемы `DecisionRecord` (лента публикует её как компонент). |
| `contracts/decide_response.schema.json`, `contracts/inspect_response.schema.json` | не меняются. `key_id` в ответ не попадает. |
| `contracts/inspect_request.schema.json` | не меняется: `InspectRequest` не несёт `args`. |
| `contracts/README.md` | `method` в таблице полей запроса; правило про упоминание трёх направлений. |
| `docs/connect.md` | адаптерам: присылать `method` у `tool: network`; без него поведение прежнее. |

Перегенерация — `scripts/export_contracts.py` и `scripts/export_openapi.py`, ровно в тех задачах, где меняется провод. PR обязан упоминать все три направления — service, adapters, benchmark (правило `contracts/README.md`), потому что меняется JSON-схема запроса.

Совместимость: `protocol` остаётся `1`. Новое поле необязательное, отсутствие означает сегодняшнее поведение, поэтому поднимать версию протокола не за что.

---

## 7. Fail-closed, инварианты, тесты, приёмка

### 7.1. Инварианты

1. Ни один вердикт не зависит от `key_id`: удаление атрибуции из кода не меняет ни одного решения. Проверяется тестом «один и тот же запрос под ключом и под статическим токеном даёт одинаковый вердикт, различаясь только `key_id` строки».
2. Сам ключ и его хэш не попадают ни в строку, ни в JSONL, ни в ответ, ни в лог-сообщение. Только `key_id`.
3. `key_id` отсутствует в `DecideResponse` и `InspectResponse`.
4. Повтор отдаётся только своему принципалу: тот же `Idempotency-Key` и то же тело под другим ключом — новое решение, новый `decision_id`, новая строка.
5. Уникальность строк по `(principal, idempotency_key)`: два принципала с одним ключом дают две строки; один принципал с одним ключом — одну.
6. Fail-closed сохраняется целиком: ошибка хранилища повторов — «повтора нет»; ошибка проверки ключа — «не совпало» (401), никогда не пропуск.
7. `method: None` никогда не квалифицируется под `profile.domain-trusted`.
8. `method` вне закрытого множества — `ask`, `stage: 0`, `rule_id: api.invalid-request`, HTTP 200.
9. Обратная совместимость: запрос без `method`, вызов под статическим токеном и профиль с `trusted_allows: false` дают вердикты, идентичные v3.1, — байт в байт.
10. Промпт запроса без `method` не меняется ни на байт.
11. Латентность ступени 1 p50 ≤ 1 мс сохраняется: новая ветка правила — три сравнения, модель не вызывается.

### 7.2. Тесты

| Файл | Что покрывает |
|---|---|
| `tests/api/test_deps.py` | `require_token` возвращает `key_id` ключа, `None` для статического токена, `None` в dev-режиме, 401 при несовпадении; статический токен побеждает при двойном совпадении |
| `tests/api/test_app.py` | `key_id` в строке и в JSONL; `key_id` отсутствует в ответе; фильтр `?key_id=`; повтор чужого принципала — новое решение; повтор своего — тот же `decision_id` |
| `tests/domain/test_replay.py` | `principal_of`, `ReplayKey.storage_key`, `answers` с совпадающим и с чужим принципалом |
| `tests/session/test_replay.py` | восстановление кладёт запись под составной ключ; запись без `key_id` — под `token:` |
| `tests/store/test_repo.py` (`requires_db`) | два принципала с одним ключом — две строки; один принципал — одна; `list(key_id=...)`; `load_replayable` отдаёт `key_id` |
| `tests/normalize/test_normalize.py` | `method` доезжает до `NormalizedAction` для `network` и остаётся `None` для `shell`; неверный метод — ошибка валидации |
| `tests/rules/test_profile_domain_trusted.py` | ветка `network`: `GET`/`HEAD` — `allow`; `POST`/`DELETE`, `None`, `mode: open`, неразрешённый домен, пустые домены, `trusted_allows: false` — молчание |
| `tests/classify/test_prompt.py` | строка `method=` при наличии метода и её отсутствие без него |
| `tests/test_contracts.py` | перегенерированные схемы совпадают с тем, что отдаёт приложение |
| `tests/rules/test_latency.py` | p50 ≤ 1 мс, включая случай с `tool: network` |

### 7.3. Критерии приёмки

1. Два вызова `/v1/decide` — под выданным ключом и под статическим токеном; в ленте первая строка несёт `key_id`, вторая `null`, вердикты идентичны.
2. `GET /v1/decisions?key_id=<ULID>` возвращает только строки этого ключа.
3. Один и тот же `Idempotency-Key` и одно и то же тело под двумя разными ключами дают два разных `decision_id` и две строки в базе; повтор под тем же ключом — один `decision_id` и одна строка.
4. `{"tool":"network","args":{"cwd":"…","domains":["github.com"],"method":"GET"}}` при `trusted_allows: true` — `allow`, `stage: 1`, `rule_id: profile.domain-trusted`; он же с `"method":"DELETE"`, без `method` и при `mode: open` — `stage: 2`.
5. Контрольный прогон бенчмарка на профиле без `trusted_allows` и на запросах без `method` не расходится с прогоном до v3.2 ни одним вердиктом.
6. `alembic upgrade head` на базе с данными проходит; строки, существовавшие до миграции, получают `principal = 'token'` и остаются уникальными.

### 7.4. Миграция

`0007_key_attribution` (`revision = "0007"`, `down_revision = "0006"`) добавляет `decisions.key_id`, генерируемый `decisions.principal`, индекс `ix_decisions_key_id_ts`, снимает `ux_decisions_idempotency_key` и ставит `ux_decisions_principal_idempotency_key`.

Голова в дереве одна. v4 слит в `main` до начала v3.2 и занял `0006` (`0006_v4_spans_and_redaction.py`, `revision = '0006'`), поэтому v3.2 просто продолжает линейную цепочку: `0005 → 0006 → 0007`. Merge-ревизия не нужна и не создаётся; `alembic upgrade head` на базе с данными проходит одним проходом.

`downgrade` возвращает уникальность к одному столбцу. Если после апгрейда два принципала успели записать один и тот же `Idempotency-Key`, откат потребует ручной чистки — обычная цена сужающего индекса, а не дефект миграции.

### 7.5. Не входит

- Привязка `Idempotency-Key` к сессии (только к принципалу).
- Блокировка ключа «в полёте»: гонка двух повторов по-прежнему сдвигает счётчики дважды.
- Единый на весь сервис отзыв ключа (кэш проверки остаётся per-process).
- Метод у `tool: shell` из отдельного поля: там он читается из argv и второго источника не будет.
- Фильтр ленты «строки без ключа».
- Строгий режим `api-keys.md` (non-localhost игнорирует статический токен).
