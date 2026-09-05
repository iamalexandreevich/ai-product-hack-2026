# AgentGate v3.3 — форма `key_id` как инвариант базы, `Idempotency-Key` в границах сессии. План реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** свойство «`key_id` никогда не равен статическому принципалу» перестаёт быть договорённостью и становится ограничением базы, а повтор по `Idempotency-Key` перестаёт быть общим для всех сессий одного предъявителя.

**Architecture:** одна константа `KEY_ID_PATTERN` в `domain/principal.py` кормит три места — `CheckConstraint` на `api_keys.id`, `CheckConstraint` на `decisions.key_id` и охрану `ensure_key_id_shape` в точке выпуска id. Ключ хранилища повторов вырастает с пары до тройки внутри `ReplayKey`, поэтому протокол `ReplayStore` остаётся односоставным, а `Replay` не получает ни одного нового поля: `session_id` входит в тело запроса и уже покрыт `request_digest`. В базе уникальный частичный индекс переезжает с `(principal, idempotency_key)` на `(principal, session_id, idempotency_key)` с `NULLS NOT DISTINCT` — без второго генерируемого столбца, потому что SQLAlchemy 2.0.52 и Postgres 16 этот флаг знают.

**Tech Stack:** Python 3.12, uv, FastAPI, pydantic v2, SQLAlchemy 2.0.52 (async) + asyncpg, Alembic 1.19, pytest + pytest-asyncio, Postgres 16.

**Spec:** `docs/superpowers/service/specs/2026-09-05-agentgate-v3.3-key-check-and-session-idempotency-design.md`. Номера разделов ниже — оттуда. Спека закрыта, открытых вопросов владельцу нет.

**Сопутствующие документы:** спека v3.2 `2026-09-05-agentgate-v3.2-key-attribution-and-network-method-design.md` §4 — что именно доделывается; план v3.2 `2026-09-05-agentgate-v3.2-key-attribution-and-network-method.md` — образец формата; отчёт `docs/reports/task-25-v3.2-key-attribution-and-network-method.md`, раздел «Отложено» — источник обоих пунктов; `service/CLAUDE.md` — карта модулей и инварианты; корневой `CLAUDE.md`, «Известные ограничения» — один пункт закрывается, один переформулируется.

**Ветка:** `feat/v3.3-key-check-session-idempotency` от `main` (`4abf185`). Исполнителю в worktree: перед началом `git rev-parse HEAD` должен показать `4abf185`; если база не та — остановиться и сообщить.

---

## Global Constraints

Действуют в каждой задаче, в шагах не повторяются.

**Поведение**

- Ни один вердикт не меняется. Ни одно существующее ожидание в `tests/rules/`, `tests/engine/`, `tests/classify/`, `tests/inspect/` не меняется по смыслу.
- Fail-closed: ошибка, таймаут, невалидный запрос → `ask`, HTTP 200. Отказ хранилища повторов — «повтора нет», никогда 5xx. Ошибка проверки ключа — 401, никогда пропуск.
- `key_id` не попадает ни в `DecideResponse`, ни в `InspectResponse`. Сам ключ и его хэш не попадают никуда, включая логи.
- Бюджет ступени 1: p50 ≤ 1 мс, `tests/rules/test_latency.py` остаётся зелёным. Ни `CHECK`, ни новый индекс не лежат на пути вердикта — только на пути записи, идущей после ответа.
- Только Postgres; тесты с БД — под `requires_db`.

**Контракт**

- **Провод не меняется ни в одной задаче.** `contracts/` не перегенерируются никогда; в каждой задаче перед коммитом:
  ```bash
  cd service && git diff --exit-code ../contracts
  ```
  Единственное исключение — задача 4, где меняется **проза** `contracts/README.md`, но не схемы: там `git diff --stat ../contracts` должен показать один изменённый файл — `contracts/README.md`.
- Изменение текста контракта — PR обязан упоминать три направления: service, adapters, benchmark (правило `contracts/README.md`).

**Процесс**

- TDD: сначала падающий тест, потом минимальная реализация. Тест, который не падал до реализации, не считается тестом.
- Код и комментарии — английский; документация — русский; идентификаторы API не переводятся. Комментарий — только неочевидное «почему»; ссылок на задачи, PR, даты в коде нет.
- Все команды — из `service/`, через `uv run`. Полный прогон перед каждым коммитом:
  ```bash
  cd service && AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test uv run pytest -q
  ```
  Ниже эта строка сокращается до `<FULL>`. Базовая сборка на `4abf185` — **1509 тестов** (`uv run pytest -q --collect-only | tail -1`); полный прогон в любой задаче должен собирать не меньше. Падение числа означает потерянный файл, а не «оптимизацию».
- Коммит только явных путей: `git commit --only <пути> -m "…"`, никогда `git add … && git commit`. Сообщение заканчивается строкой `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`.
- Зона записи: `service/`, `docs/`, корневой `CLAUDE.md`, и в задаче 4 — `contracts/README.md`. Ничего в `adapters/`, `benchmark/`, `frontend/` не меняется.
- Отчёт по завершении: `docs/reports/task-26-v3.3-key-check-and-session-idempotency.md`.

**Миграции**

- Голова на базе — `0007`. Цепочка релиза линейна: `0007 → 0008 → 0009`, две головы недопустимы.
- Два файла, а не один, потому что задачи 1 и 3 могут идти разными исполнителями: `0008_key_id_shape.py` принадлежит задаче 1 и никем больше не редактируется, `0009_session_idempotency.py` — задаче 3. Ни один из файлов не читает содержимое другого.
- Round-trip на отдельной чистовой базе (не на `agentgate_test`, которую пересоздают фикстуры) — обязательный шаг в задачах 1 и 3.

---

## Карта файлов

| Файл | Действие | Ответственность | Задача |
|---|---|---|---|
| `agentgate/domain/principal.py` | изменить | `KEY_ID_PATTERN`, `ensure_key_id_shape` | 1 |
| `agentgate/store/models.py` | изменить | два `CheckConstraint` (1); индекс по тройке (3) | 1 / 3 |
| `agentgate/store/keys.py` | изменить | охрана формы id при выпуске | 1 |
| `migrations/versions/0008_key_id_shape.py` | создать | два `CHECK`, `down_revision = "0007"` | 1 |
| `agentgate/domain/replay.py` | изменить | `ReplayKey.session`, `NO_SESSION`, `of(key_id, session_id, key)` | 2 |
| `agentgate/session/replay.py` | изменить | тройной ключ при восстановлении | 2 |
| `agentgate/api/app.py` | изменить | `ReplayKey.of(key_id, parsed.session_id, key)` | 2 |
| `agentgate/store/repo.py` | изменить | цель `ON CONFLICT` — тройка | 3 |
| `migrations/versions/0009_session_idempotency.py` | создать | перестановка уникального индекса, `down_revision = "0008"` | 3 |
| `tests/domain/test_principal.py` | создать | паттерн, охрана, невозможность коллизии с литералом | 1 |
| `tests/store/test_keys.py` | изменить | форма выпущенного id | 1 |
| `tests/store/test_repo.py` | изменить | `CHECK` отвергает форму, `NULL` проходит (1); тройка и `NULLS NOT DISTINCT` (3); настоящие ULID вместо `01HZKEYA` (1) | 1 / 3 |
| `tests/domain/test_replay.py` | изменить | тройной `storage_key`, сентинел, чужая сессия | 2 |
| `tests/session/test_replay.py` | изменить | восстановление под тройным ключом | 2 |
| `tests/api/test_app.py` | изменить | другая сессия — другое решение и вторая запись | 2 |
| `contracts/README.md`, `docs/connect.md`, `service/README.md`, `service/CLAUDE.md`, `CLAUDE.md` | изменить | проза | 4 |
| `docs/reports/task-26-v3.3-key-check-and-session-idempotency.md` | создать | отчёт | 4 |

---

## Волна 1 — независимые половины

### Task 1: форма `key_id` — константа, охрана, два `CHECK`, миграция `0008`

Закрывает §3 спеки. Базы данных касается, но с задачей 2 не пересекается ни одним файлом.

**Files:**
- Modify: `service/agentgate/domain/principal.py`, `service/agentgate/store/models.py`, `service/agentgate/store/keys.py`
- Create: `service/migrations/versions/0008_key_id_shape.py`, `service/tests/domain/test_principal.py`
- Test: `service/tests/store/test_keys.py`, `service/tests/store/test_repo.py`

- [ ] **Step 1: Падающий тест на паттерн и охрану**

Создать `service/tests/domain/test_principal.py`:

```python
import re

import pytest
from ulid import ULID

from agentgate.domain.principal import (
    KEY_ID_PATTERN,
    STATIC_PRINCIPAL,
    ensure_key_id_shape,
    principal_of,
)


def test_the_static_principal_can_never_be_mistaken_for_a_key_id():
    # The whole `principal = coalesce(key_id, 'token')` scheme rests on this
    # one fact; before v3.3 it rested on a docstring.
    assert re.fullmatch(KEY_ID_PATTERN, STATIC_PRINCIPAL) is None


def test_a_freshly_minted_ulid_matches_the_pattern():
    assert re.fullmatch(KEY_ID_PATTERN, str(ULID())) is not None


def test_ensure_key_id_shape_returns_the_id_it_accepted():
    key_id = str(ULID())
    assert ensure_key_id_shape(key_id) == key_id


@pytest.mark.parametrize(
    "bad",
    ["token", "", "01hzkeya" + "0" * 18, "0" * 25, "0" * 27, "0IL" + "0" * 23, "0" * 25 + "!"],
    ids=["static_principal", "empty", "lowercase", "too_short", "too_long",
         "excluded_letters", "punctuation"],
)
def test_ensure_key_id_shape_rejects_anything_but_a_ulid(bad):
    with pytest.raises(ValueError):
        ensure_key_id_shape(bad)


def test_principal_of_still_answers_the_static_token():
    assert principal_of(None) == STATIC_PRINCIPAL
```

- [ ] **Step 2: Прогнать, убедиться, что падает**

```bash
cd service && uv run pytest -q tests/domain/test_principal.py
```
Expected: `ImportError: cannot import name 'KEY_ID_PATTERN' from 'agentgate.domain.principal'`.

- [ ] **Step 3: Реализация — константа и охрана**

`service/agentgate/domain/principal.py` целиком (файл короткий, приводится полностью, чтобы было видно, что докстринг модуля перестаёт обещать то, чего не проверяет):

```python
"""Who a decision belongs to, as the store and the replay cache name it.

A principal is the issued API key's id, or the literal below for the static
`AGENTGATE_TOKEN`. The SQL of the generated `decisions.principal` column and
the replay cache's key namespace both read this constant, so the two never
drift apart.

The literal is safe as a namespace only because no key id can equal it.
That is not a convention: `KEY_ID_PATTERN` is the shape a key id must have,
`ensure_key_id_shape` refuses to mint anything else, and the database says
the same thing in `ck_api_keys_id_ulid` and `ck_decisions_key_id_ulid`. All
three read the constant below.
"""

import re

STATIC_PRINCIPAL = "token"

# Crockford base32, upper case, 26 characters: the shape of a ULID. Shape,
# not validity -- a timestamp beyond the year 10889 would pass. What is
# needed here is a set that cannot contain STATIC_PRINCIPAL and that reads
# the same in Python and in a Postgres CHECK.
KEY_ID_PATTERN = r"^[0-9A-HJKMNP-TV-Z]{26}$"

_KEY_ID_RE = re.compile(KEY_ID_PATTERN)


def principal_of(key_id: str | None) -> str:
    """The issued key's id, or the static token."""
    return key_id or STATIC_PRINCIPAL


def ensure_key_id_shape(key_id: str) -> str:
    """The id a key may be minted with, or ValueError.

    Called where an id is created, not where one is read: inside the service
    an id has already passed this check and the database's own CHECK.
    """
    if _KEY_ID_RE.fullmatch(key_id) is None:
        raise ValueError(f"key id is not a ULID: {key_id!r}")
    return key_id
```

- [ ] **Step 4: Прогнать — тесты паттерна зелёные**

```bash
cd service && uv run pytest -q tests/domain/test_principal.py tests/domain/test_replay.py
```
Expected: всё зелёное, `test_replay.py` не затронут.

- [ ] **Step 5: Падающий тест на охрану в точке выпуска**

Дописать в `service/tests/store/test_keys.py`, в секцию генерации (тест базы не требует — проверяется чистая функция и то, что репозиторий её зовёт):

```python
# --- v3.3: an issued id has the shape the principal namespace depends on ----


def test_a_minted_key_id_has_the_shape_the_principal_namespace_needs():
    from agentgate.domain.principal import KEY_ID_PATTERN
    from agentgate.store.keys import mint_key_id

    assert re.fullmatch(KEY_ID_PATTERN, mint_key_id()) is not None


def test_a_minted_key_id_passes_the_guard_the_database_repeats():
    # Minting and the CHECK must agree, or a key issued by the CLI would be
    # refused the first time a decision under it is written.
    from agentgate.domain.principal import ensure_key_id_shape
    from agentgate.store.keys import mint_key_id

    key_id = mint_key_id()

    assert ensure_key_id_shape(key_id) == key_id


def test_minted_ids_are_distinct():
    from agentgate.store.keys import mint_key_id

    assert len({mint_key_id() for _ in range(20)}) == 20
```

Добавить `import re` в шапку файла, если его там нет (в текущем файле его нет — есть `base64`, `hashlib`, `datetime`).

- [ ] **Step 6: Прогнать, убедиться, что падает**

```bash
cd service && uv run pytest -q tests/store/test_keys.py
```
Expected: `ImportError: cannot import name 'mint_key_id' from 'agentgate.store.keys'`.

- [ ] **Step 7: Реализация — `mint_key_id` в `store/keys.py`**

В `service/agentgate/store/keys.py` добавить импорт и функцию рядом с `generate_key`/`hash_key`:

```python
from agentgate.domain.principal import ensure_key_id_shape
```

```python
def mint_key_id() -> str:
    """The public id of a new key: a ULID, checked before it leaves here.

    The id is the principal a decision is attributed to, and the static
    token's principal is a literal that no id may equal -- so the shape is
    checked where the id is born, not where it is read.
    """
    return ensure_key_id_shape(str(ULID()))
```

и в `ApiKeyRepo.create` заменить `id=str(ULID())` на `id=mint_key_id()`:

```python
        row = ApiKeyRow(
            id=mint_key_id(), key_hash=key_hash, label=label, created_at=now,
            expires_at=expires_at, revoked_at=None, last_used_at=None,
        )
```

- [ ] **Step 8: Падающий тест на `CHECK` в базе**

Дописать в `service/tests/store/test_repo.py`, в секцию v3.2 (файл целиком под `requires_db`, схема поднимается фикстурой `session_factory` из `Base.metadata.create_all`, поэтому `CheckConstraint` попадает в тестовую схему без Alembic):

```python
# --- v3.3: the shape of key_id is the database's business --------------------


async def test_a_key_id_outside_the_ulid_shape_is_refused_by_the_database(session_factory):
    await _seed_session(session_factory)
    repo = DecisionRepo(session_factory)

    with pytest.raises(IntegrityError):
        await repo.insert(replace(rec(), key_id=STATIC_PRINCIPAL))


async def test_a_null_key_id_is_still_allowed(session_factory):
    await _seed_session(session_factory)
    repo = DecisionRepo(session_factory)

    assert await repo.insert(replace(rec(), key_id=None)) is True
```

Импорт в шапке файла: `from agentgate.domain.principal import STATIC_PRINCIPAL`. `pytest` и `IntegrityError` там уже импортированы.

- [ ] **Step 9: Прогнать, убедиться, что падает**

```bash
cd service && AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test uv run pytest -q tests/store/test_repo.py -k ulid_shape
```
Expected: `DID NOT RAISE <class 'sqlalchemy.exc.IntegrityError'>` — строка с `key_id='token'` вставляется.

- [ ] **Step 10: Реализация — два `CheckConstraint`**

В `service/agentgate/store/models.py` расширить импорты:

```python
from sqlalchemy import (
    Boolean, CheckConstraint, Computed, DateTime, ForeignKey, Index, Integer, String, Text, text,
)
from agentgate.domain.principal import KEY_ID_PATTERN, STATIC_PRINCIPAL
```

В `DecisionRow.__table_args__` дописать последним элементом:

```python
        # The generated `principal` above is only a namespace while no key id
        # can spell STATIC_PRINCIPAL. This is where that stops being a habit.
        CheckConstraint(
            f"key_id IS NULL OR key_id ~ '{KEY_ID_PATTERN}'", name="ck_decisions_key_id_ulid"
        ),
```

В `ApiKeyRow` добавить `__table_args__` (у класса их сейчас нет):

```python
    __table_args__ = (
        CheckConstraint(f"id ~ '{KEY_ID_PATTERN}'", name="ck_api_keys_id_ulid"),
    )
```

- [ ] **Step 11: Прогнать — и починить шесть тестов, которые новый `CHECK` заслуженно роняет**

```bash
cd service && AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test uv run pytest -q tests/store/test_repo.py
```
Expected до правки: падают тесты, использующие восьмисимвольные заглушки `"01HZKEYA"`/`"01HZKEYB"` — `test_load_replayable_carries_the_key_id_back`, `test_two_principals_may_share_one_idempotency_key`, `test_one_principal_may_not_use_one_idempotency_key_twice`, `test_list_filters_by_key_id`. Это не регресс, а первый случай, когда фикция «id ключа» перестала быть принимаемой базой.

Завести рядом с `rec()` в `service/tests/store/test_repo.py` два модульных значения и подставить их вместо литералов:

```python
KEY_A, KEY_B = str(ULID()), str(ULID())
```

Замены (по одной на вхождение):

```python
    await repo.insert(replace(rec(), idempotency_key="k", key_id=KEY_A))
    ...
    assert [r.key_id for r in records] == [KEY_A]
```
```python
    first = replace(rec(), idempotency_key="same", key_id=KEY_A)
    second = replace(rec(), idempotency_key="same", key_id=KEY_B)
```
```python
    assert await repo.insert(replace(rec(), idempotency_key="same", key_id=KEY_A)) is True
    assert await repo.insert(replace(rec(), idempotency_key="same", key_id=KEY_A)) is False
```
```python
    await repo.insert(replace(rec(), key_id=KEY_A))
    await repo.insert(replace(rec(), key_id=KEY_B))

    rows = await repo.list(session_id=None, model=None, limit=100, before=None, key_id=KEY_A)

    assert [r.key_id for r in rows] == [KEY_A]
```

Тесты вне базы (`tests/domain/test_replay.py`, `tests/engine/test_decision.py`, `tests/engine/test_inspection.py`, `tests/api/test_app.py`) короткие заглушки сохраняют: там `CHECK` не участвует, а читаемость важнее единообразия.

- [ ] **Step 12: Миграция `0008`**

Создать `service/migrations/versions/0008_key_id_shape.py`:

```python
"""v3.3: the shape of a key id is a constraint, not a convention

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-05 00:00:00.000000
"""
from alembic import op

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None

# Kept literal on purpose: a migration is a frozen snapshot of the schema at
# one moment, and importing agentgate.domain.principal would let a later edit
# of that constant silently rewrite history. tests/store/test_repo.py checks
# that the running schema and this text still say the same thing.
_ULID = "^[0-9A-HJKMNP-TV-Z]{26}$"


def upgrade() -> None:
    # Fails, deliberately, if any historical row is outside the shape: a
    # key id that the service never minted is an incident, and silently
    # rewriting it would destroy the trace of one.
    op.create_check_constraint('ck_api_keys_id_ulid', 'api_keys', f"id ~ '{_ULID}'")
    op.create_check_constraint(
        'ck_decisions_key_id_ulid', 'decisions', f"key_id IS NULL OR key_id ~ '{_ULID}'"
    )


def downgrade() -> None:
    op.drop_constraint('ck_decisions_key_id_ulid', 'decisions', type_='check')
    op.drop_constraint('ck_api_keys_id_ulid', 'api_keys', type_='check')
```

- [ ] **Step 13: Round-trip миграции на отдельной базе**

```bash
psql "postgresql://agentgate:agentgate@localhost:5433/postgres" -c "DROP DATABASE IF EXISTS agentgate_mig; CREATE DATABASE agentgate_mig OWNER agentgate;"
cd service && AGENTGATE_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_mig uv run alembic upgrade head
psql "postgresql://agentgate:agentgate@localhost:5433/agentgate_mig" -c "\d+ decisions" | grep ck_decisions_key_id_ulid
psql "postgresql://agentgate:agentgate@localhost:5433/agentgate_mig" -c "\d+ api_keys" | grep ck_api_keys_id_ulid
psql "postgresql://agentgate:agentgate@localhost:5433/agentgate_mig" -c \
  "insert into decisions (id, ts, harness, tool, raw, normalized, user_request, profile_id, profile_hash, decision, stage, latency_total_ms, key_id) values ('01J0000000000000000000000A', now(), 't', 'shell', 'ls', '{}', 'x', 'default', 'h', 'allow', 1, 1, 'token');"
```
Expected: обе `grep`-строки находятся; `INSERT` отвергается с `new row ... violates check constraint "ck_decisions_key_id_ulid"`.

```bash
cd service && AGENTGATE_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_mig uv run alembic downgrade 0007
cd service && AGENTGATE_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_mig uv run alembic upgrade head
```
Expected: обе команды проходят без ошибок. Если имя переменной окружения для URL в этом дереве другое — взять то, которое читает `migrations/env.py`, и записать фактическую строку в отчёт.

- [ ] **Step 14: Полный прогон и коммит**

```bash
cd service && <FULL> && git diff --exit-code ../contracts
```
Expected: зелёный, собрано ≥ 1509 + 9 новых тестов; контракты без diff.

```bash
git commit --only service/agentgate/domain/principal.py service/agentgate/store/models.py service/agentgate/store/keys.py service/migrations/versions/0008_key_id_shape.py service/tests/domain/test_principal.py service/tests/store/test_keys.py service/tests/store/test_repo.py -m "feat(store): a key id has a shape, and the database is the one that says so

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: сессия входит в ключ повтора

Закрывает §4.1–§4.4 спеки. Базы не касается вовсе — уникальность переезжает в задаче 3. С задачей 1 не пересекается ни одним файлом.

**Files:**
- Modify: `service/agentgate/domain/replay.py`, `service/agentgate/session/replay.py`, `service/agentgate/api/app.py`
- Test: `service/tests/domain/test_replay.py`, `service/tests/session/test_replay.py`, `service/tests/api/test_app.py`

- [ ] **Step 1: Падающий тест на тройной ключ**

Заменить в `service/tests/domain/test_replay.py` два теста ключа и дописать три новых. Локальный хелпер `record()` в файле уже есть, `decide_request` из `tests.factories` принимает `session_id`:

```python
from agentgate.domain.replay import NO_SESSION, STATIC_PRINCIPAL, Replay, ReplayKey, principal_of
```

```python
def test_the_storage_key_joins_principal_session_and_key():
    assert ReplayKey.of("01HZKEYA", "s1", "abc").storage_key() == "01HZKEYA:s1:abc"


def test_a_call_without_a_session_gets_its_own_namespace():
    assert ReplayKey.of(None, None, "abc").storage_key() == f"token:{NO_SESSION}:abc"
    assert NO_SESSION == "-"


def test_two_sessions_of_one_principal_do_not_share_a_slot():
    a = ReplayKey.of("01HZKEYA", "s1", "abc").storage_key()
    b = ReplayKey.of("01HZKEYA", "s2", "abc").storage_key()

    assert a != b


def test_a_replay_does_not_answer_a_call_from_another_session():
    # No second field on Replay carries the session: the identity digest is
    # the whole request body, and session_id is part of it. This test is what
    # keeps that argument honest.
    stored = Replay.of(decision(idempotency_key="k", request=decide_request("ls -la", session_id="s1")).to_record())

    assert stored.answers(decide_request("ls -la", session_id="s1"), STATIC_PRINCIPAL) is True
    assert stored.answers(decide_request("ls -la", session_id="s2"), STATIC_PRINCIPAL) is False
```

Импорт `decision` в файле уже есть через локальный `record`; добавить `from tests.factories import decide_request, decision`, если `decision` в шапке отсутствует (сейчас импортируются `decide_request` и `decision`).

Старые `test_the_storage_key_joins_principal_and_key` и `test_the_storage_key_of_the_static_token_is_namespaced_too` удаляются — их утверждение целиком поглощено новыми двумя.

- [ ] **Step 2: Прогнать, убедиться, что падает**

```bash
cd service && uv run pytest -q tests/domain/test_replay.py
```
Expected: `ImportError: cannot import name 'NO_SESSION'`.

- [ ] **Step 3: Реализация — `ReplayKey`**

В `service/agentgate/domain/replay.py` заменить `ReplayKey` и дополнить докстринг модуля третьим абзацем:

```python
NO_SESSION = "-"


@dataclass(frozen=True)
class ReplayKey:
    """The caller-supplied key, namespaced by whoever supplied it and where.

    The key is chosen by the caller and was global to the service until this
    type existed: two integrators picking the same string shared one entry,
    and one of them lost the audit row to the other's unique index. The
    session is the second half of that story -- one integrator running two
    sessions under one key had the two entries evict each other, so neither
    was ever replayed.

    A session literally named "-" shares a slot with sessionless calls, and a
    session id containing ":" can collide with another (session, key) pair.
    Both cost a missed replay and nothing more: `Replay.answers` compares the
    digest of the whole request, so a shared slot can never hand back another
    call's verdict.
    """

    principal: str
    session: str
    key: str

    @classmethod
    def of(cls, key_id: str | None, session_id: str | None, key: str) -> "ReplayKey":
        return cls(principal_of(key_id), session_id or NO_SESSION, key)

    def storage_key(self) -> str:
        return f"{self.principal}:{self.session}:{self.key}"
```

`Replay` не меняется: `session_id` входит в `request.identity_digest()`, и второе хранение того же знания разошлось бы с ним при первой правке `identity_digest`.

- [ ] **Step 4: Прогнать — падает вызывающий код**

```bash
cd service && uv run pytest -q tests/domain/test_replay.py tests/session/test_replay.py tests/api/test_app.py
```
Expected: `tests/domain/test_replay.py` зелёный; `TypeError: ReplayKey.of() missing 1 required positional argument: 'key'` в `session/replay.py` и `api/app.py`.

- [ ] **Step 5: Падающий тест на восстановление**

В `service/tests/session/test_replay.py` восстановительные тесты сравнивают ключ. Найти те, что ждут `"token:<k>"` (появились в v3.2), и переписать под тройку. Локальный `record()` строит запрос через `decision(...)`, у которого `session_id` по умолчанию `"s1"` (см. `tests/factories.decide_request`), поэтому ожидание:

```python
async def test_restore_puts_entries_under_the_principal_and_session(session_factory=None):
    inner = InMemoryReplayStore()
    store = PersistentReplayStore(inner, FakeReplayRecords([record("k")]), ttl_seconds=60)

    await store.restore()

    assert await inner.get("token:s1:k") is not None
    assert await inner.get("token:k") is None
```

Если фактический `session_id` записи в этом файле иной — взять его из `record()`, а не подгонять фабрику.

- [ ] **Step 6: Прогнать, убедиться, что падает**

```bash
cd service && uv run pytest -q tests/session/test_replay.py
```
Expected: `TypeError` из `ReplayKey.of` в `PersistentReplayStore.restore`.

- [ ] **Step 7: Реализация — восстановление**

В `service/agentgate/session/replay.py`, в `PersistentReplayStore.restore`:

```python
            await self._inner.put(
                ReplayKey.of(
                    record.key_id, record.session_id, record.idempotency_key
                ).storage_key(),
                replay, remaining,
            )
```

- [ ] **Step 8: Падающий тест на маршрут**

Дописать в `service/tests/api/test_app.py`, в секцию `# --- replay namespaced by principal ---` (её заголовок обновить на `# --- replay namespaced by principal and session ---`):

```python
async def test_the_same_key_in_another_session_is_decided_afresh(tmp_path):
    app, drepo, _, _ = build(tmp_path)
    headers = {"idempotency-key": "shared"}

    first = await call(app, "POST", "/v1/decide", json=body(session_id="s1"), headers=headers)
    second = await call(app, "POST", "/v1/decide", json=body(session_id="s2"), headers=headers)

    assert first.json()["decision_id"] != second.json()["decision_id"]
    assert len(drepo.rows) == 2


async def test_the_same_key_in_the_same_session_is_still_replayed(tmp_path):
    app, drepo, _, _ = build(tmp_path)
    headers = {"idempotency-key": "shared"}

    first = await call(app, "POST", "/v1/decide", json=body(session_id="s1"), headers=headers)
    second = await call(app, "POST", "/v1/decide", json=body(session_id="s1"), headers=headers)

    assert first.json()["decision_id"] == second.json()["decision_id"]
    assert len(drepo.rows) == 1


async def test_two_sessionless_calls_share_one_slot(tmp_path):
    # `-` is a namespace, not an exemption: a caller without a session gets
    # replays, it simply shares them with every other sessionless call of the
    # same principal -- and the digest keeps that from being a wrong answer.
    app, drepo, _, _ = build(tmp_path)
    headers = {"idempotency-key": "shared"}

    first = await call(app, "POST", "/v1/decide", json=body(session_id=None), headers=headers)
    second = await call(app, "POST", "/v1/decide", json=body(session_id=None), headers=headers)

    assert first.json()["decision_id"] == second.json()["decision_id"]
    assert len(drepo.rows) == 1
```

- [ ] **Step 9: Прогнать, убедиться, что падает**

```bash
cd service && uv run pytest -q tests/api/test_app.py -k another_session
```
Expected: `TypeError` из `ReplayKey.of` в `_answer` — маршрут ещё зовёт его с двумя аргументами.

- [ ] **Step 10: Реализация — маршрут**

В `service/agentgate/api/app.py::_answer` одна строка. Сессия берётся из **разобранного** запроса, а не из сырого JSON: тело уже провалидировано двумя строками выше, и `parsed.session_id` — единственная форма, в которой сессия существует после разбора:

```python
    key = _replay_key(request)
    replay_key = ReplayKey.of(key_id, parsed.session_id, key) if key is not None else None
```

`DecideRequest.session_id` и `InspectRequest.session_id` — оба `str | None`, поэтому `RouteSpec` не растёт ни на поле.

- [ ] **Step 11: Полный прогон и коммит**

```bash
cd service && <FULL> && git diff --exit-code ../contracts
```
Expected: зелёный; собрано ≥ 1509 + 6 − 2 (два удалённых теста ключа заменены четырьмя).

```bash
git commit --only service/agentgate/domain/replay.py service/agentgate/session/replay.py service/agentgate/api/app.py service/tests/domain/test_replay.py service/tests/session/test_replay.py service/tests/api/test_app.py -m "feat(replay): a replay belongs to a session, not only to a principal

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Волна 2 — уникальность в базе

### Task 3: тройка в уникальном индексе, миграция `0009`

Закрывает §4.5–§4.6 спеки. Требует задачи 1 (файл `0008` существует, `down_revision = "0008"` разрешается) и задачи 2 (иначе хранилище и база расходятся по границе на один коммит).

**Files:**
- Modify: `service/agentgate/store/models.py`, `service/agentgate/store/repo.py`
- Create: `service/migrations/versions/0009_session_idempotency.py`
- Test: `service/tests/store/test_repo.py`

- [ ] **Step 0: Проверить, что флаг вообще доступен**

```bash
cd service && uv run python -c "import sqlalchemy; print(sqlalchemy.__version__)"
psql "postgresql://agentgate:agentgate@localhost:5433/agentgate" -c "show server_version;"
```
Expected: SQLAlchemy `2.0.52` (флаг `postgresql_nulls_not_distinct` есть с 2.0.31), Postgres `16.x` (`NULLS NOT DISTINCT` есть с 15). Если что-то из двух ниже — остановиться и перейти на фолбэк спеки (генерируемый `session_scope = coalesce(session_id, '-')` по образцу `principal`), записав это в отчёт как вынужденное отступление.

- [ ] **Step 1: Падающий тест на уникальность по тройке**

Дописать в `service/tests/store/test_repo.py`, в новую секцию:

```python
# --- v3.3: one idempotency key per principal *and* session -------------------


async def test_one_key_in_two_sessions_of_one_principal_writes_two_rows(session_factory):
    await _seed_session(session_factory, "s1")
    await _seed_session(session_factory, "s2")
    repo = DecisionRepo(session_factory)

    first = replace(rec(session_id="s1"), idempotency_key="same", key_id=KEY_A)
    second = replace(rec(session_id="s2"), idempotency_key="same", key_id=KEY_A)

    assert await repo.insert(first) is True
    assert await repo.insert(second) is True


async def test_one_key_twice_in_one_session_still_writes_one_row(session_factory):
    await _seed_session(session_factory, "s1")
    repo = DecisionRepo(session_factory)

    assert await repo.insert(replace(rec(session_id="s1"), idempotency_key="dup3", key_id=KEY_A)) is True
    assert await repo.insert(replace(rec(session_id="s1"), idempotency_key="dup3", key_id=KEY_A)) is False


async def test_two_sessionless_calls_still_compete_for_the_key(session_factory):
    # NULL <> NULL in Postgres, so without NULLS NOT DISTINCT this pair would
    # both land and the uniqueness we are here to keep would be gone.
    repo = DecisionRepo(session_factory)

    assert await repo.insert(replace(rec(session_id=None), idempotency_key="dup4", key_id=KEY_A)) is True
    assert await repo.insert(replace(rec(session_id=None), idempotency_key="dup4", key_id=KEY_A)) is False
```

`rec()` уже принимает `session_id` первым параметром; `_seed_session` создаёт строку сессии, без которой падает FK.

- [ ] **Step 2: Прогнать, убедиться, что падает**

```bash
cd service && AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test uv run pytest -q tests/store/test_repo.py -k "two_sessions_of_one_principal or sessionless_calls_still_compete"
```
Expected: `test_one_key_in_two_sessions_of_one_principal_writes_two_rows` падает на `assert False is True` (вторая вставка съедена старым индексом по паре); `test_two_sessionless_calls_still_compete_for_the_key` **проходит** уже сейчас — старый индекс по паре его и держит. Оба нужны: первый — новое поведение, второй — регресс на то, что новое поведение его не сломало.

- [ ] **Step 3: Реализация — индекс в модели**

В `service/agentgate/store/models.py`, в `DecisionRow.__table_args__`, заменить индекс v3.2:

```python
        # The triple, not the pair: one integrator running two sessions under
        # one key lost the second session's audit row to the pair. NULLS NOT
        # DISTINCT because session_id is nullable and a sessionless call must
        # still compete for its key -- in Postgres NULL <> NULL otherwise.
        Index(
            "ux_decisions_principal_session_idempotency_key",
            "principal", "session_id", "idempotency_key", unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
            postgresql_nulls_not_distinct=True,
        ),
```

- [ ] **Step 4: Реализация — цель `ON CONFLICT`**

В `service/agentgate/store/repo.py::DecisionRepo.insert`:

```python
        stmt = pg_insert(table).values(**values).on_conflict_do_nothing(
            index_elements=[table.c.principal, table.c.session_id, table.c.idempotency_key],
            index_where=table.c.idempotency_key.isnot(None),
        )
```

и в докстринге метода заменить «с этим ключом идемпотентности» на формулировку по тройке:

```python
        """Insert one row (a decide or an inspect outcome); ``False`` when a
        row with this idempotency key already existed **for this principal and
        this session** and nothing was inserted.
```

- [ ] **Step 5: Прогнать три теста и весь файл**

```bash
cd service && AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test uv run pytest -q tests/store/test_repo.py
```
Expected: все зелёные, включая унаследованные `test_two_principals_may_share_one_idempotency_key` и `test_the_static_token_is_one_principal_too`.

- [ ] **Step 6: Миграция `0009`**

Создать `service/migrations/versions/0009_session_idempotency.py`:

```python
"""v3.3: an idempotency key belongs to a session, not to a principal at large

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-05 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

revision = '0009'
down_revision = '0008'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index('ux_decisions_principal_idempotency_key', table_name='decisions')
    op.create_index(
        'ux_decisions_principal_session_idempotency_key', 'decisions',
        ['principal', 'session_id', 'idempotency_key'], unique=True,
        postgresql_where=sa.text('idempotency_key IS NOT NULL'),
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    op.drop_index('ux_decisions_principal_session_idempotency_key', table_name='decisions')
    # Narrowing: fails if one principal wrote the same idempotency_key in two
    # sessions after the upgrade. Those rows must be de-duplicated by hand
    # before this downgrade runs -- the same hazard 0007's downgrade carries.
    op.create_index(
        'ux_decisions_principal_idempotency_key', 'decisions',
        ['principal', 'idempotency_key'], unique=True,
        postgresql_where=sa.text('idempotency_key IS NOT NULL'),
    )
```

- [ ] **Step 7: Round-trip и проверка формы индекса**

```bash
psql "postgresql://agentgate:agentgate@localhost:5433/postgres" -c "DROP DATABASE IF EXISTS agentgate_mig; CREATE DATABASE agentgate_mig OWNER agentgate;"
cd service && AGENTGATE_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_mig uv run alembic upgrade head
psql "postgresql://agentgate:agentgate@localhost:5433/agentgate_mig" -c \
  "select indexdef from pg_indexes where indexname = 'ux_decisions_principal_session_idempotency_key';"
```
Expected: определение содержит `(principal, session_id, idempotency_key) NULLS NOT DISTINCT WHERE (idempotency_key IS NOT NULL)`. Точный порядок слов записать в отчёт — он и есть доказательство, что флаг доехал.

```bash
cd service && AGENTGATE_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_mig uv run alembic downgrade 0007
cd service && AGENTGATE_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_mig uv run alembic upgrade head
psql "postgresql://agentgate:agentgate@localhost:5433/postgres" -c "DROP DATABASE agentgate_mig;"
```
Expected: обе стороны проходят; голова одна (`uv run alembic heads` — ровно одна строка, `0009`).

- [ ] **Step 8: Полный прогон и коммит**

```bash
cd service && <FULL> && git diff --exit-code ../contracts && uv run alembic heads
```
Expected: зелёный, `alembic heads` — одна строка `0009 (head)`.

```bash
git commit --only service/agentgate/store/models.py service/agentgate/store/repo.py service/migrations/versions/0009_session_idempotency.py service/tests/store/test_repo.py -m "feat(store): uniqueness of an idempotency key is per principal and per session

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Волна 3 — документация и приёмка

### Task 4: документация, приёмка, отчёт

Закрывает §5 и §7.2 спеки. Схемы контрактов не трогаются — меняется только проза.

**Files:**
- Modify: `contracts/README.md`, `docs/connect.md`, `service/README.md`, `service/CLAUDE.md`, `CLAUDE.md`
- Create: `docs/reports/task-26-v3.3-key-check-and-session-idempotency.md`

- [ ] **Step 1: `contracts/README.md`**

В разделе про `Idempotency-Key` (строка ~14) требование «Ключ должен быть уникален на вызов инструмента» дополнить: «…в границах предъявителя **и сессии**: одна и та же строка, присланная из двух разных сессий, — это два независимых вызова, два `decision_id` и две строки в ленте». В разделе `## v3.2` добавить абзац-ссылку на v3.3 с той же формулировкой, чтобы читающий сверху вниз не остановился на устаревшем утверждении.

- [ ] **Step 2: `docs/connect.md`**

Абзац на строке ~72 («`Idempotency-Key` живёт в границах вашего API-ключа») дополнить сессией и объяснить, что это чинит для интегратора: одна строка ключа на две сессии больше не съедает ни аудит, ни повтор.

- [ ] **Step 3: `service/README.md`**

В раздел про миграции (после абзаца про `0007`) добавить два: `0008_key_id_shape` (два `CHECK`, `upgrade` падает на исторической строке вне формы, `downgrade` безопасен, потому что только ослабляет) и `0009_session_idempotency` (тройка, `NULLS NOT DISTINCT`, `downgrade` сужающий — та же опасность, что у `0007`, с тем же рецептом ручной де-дупликации).

- [ ] **Step 4: `service/CLAUDE.md`**

Три места: строка про `domain/` (у `principal.py` появляются `KEY_ID_PATTERN` и `ensure_key_id_shape` — «единственное место, знающее и строку `token`, и форму, которая с ней не пересекается»; у `replay.py` — `ReplayKey` по тройке и явное «`Replay` поля сессии не имеет, потому что она внутри дайджеста»); строка про `store/` (`0008`, `0009`, имена ограничений и индекса); строка инвариантов (~82) — повтор живёт в границах принципала **и** сессии.

- [ ] **Step 5: корневой `CLAUDE.md`**

- Абзац «Что построено»: после блока про v3.2 — одно предложение про v3.3.
- Ссылки на документы в шапке: спека и план v3.3.
- «Известные ограничения»: пункт «**`Idempotency-Key` по-прежнему не привязан к сессии**» **удалить**. Пункт про откат `0007` дополнить упоминанием `0009` (та же природа). Пункт про повтор после TTL переформулировать на тройку. Добавить два новых, честных:
  - сессия, названная `-`, делит пространство повторов с бессессионными вызовами, а `session_id` с `:` может дать ту же склейку ключа, что другая пара; цена — пропущенный повтор, не чужой вердикт;
  - `0008` не чинит данные: строка с `key_id` вне формы ULID останавливает миграцию, и это сознательный выбор.

- [ ] **Step 6: Приёмка**

Выполнить шесть критериев §7.2 спеки, записав фактические значения:

```bash
cd service && uv run python -m agentgate keys create --label acceptance-v33
cd service && uv run python -m agentgate      # порт 8400
```

```bash
BODY_S1='{"session_id":"s1","harness":"t","tool":"shell","raw":"git status","args":{"cwd":"/home/u/repo"},"user_request":"check"}'
BODY_S2='{"session_id":"s2","harness":"t","tool":"shell","raw":"git status","args":{"cwd":"/home/u/repo"},"user_request":"check"}'
for B in "$BODY_S1" "$BODY_S2" "$BODY_S1"; do
  curl -s localhost:8400/v1/decide -H "Authorization: Bearer $AGK" -H 'Idempotency-Key: v33' \
    -H 'Content-Type: application/json' -d "$B" | jq -r .decision_id
done
psql "postgresql://agentgate:agentgate@localhost:5433/agentgate" -c \
  "select principal, session_id, count(*) from decisions where idempotency_key='v33' group by 1,2 order by 2;"
```
Expected: первый и третий `decision_id` совпадают, второй отличается; `group by` — две строки, у каждой `count = 1`.

```bash
psql "postgresql://agentgate:agentgate@localhost:5433/agentgate" -c \
  "update decisions set key_id = 'token' where idempotency_key = 'v33';"
```
Expected: `ERROR: ... violates check constraint "ck_decisions_key_id_ulid"`.

- [ ] **Step 7: Отчёт**

Создать `docs/reports/task-26-v3.3-key-check-and-session-idempotency.md` на русском, по образцу `docs/reports/task-25-v3.2-key-attribution-and-network-method.md`. Обязательные разделы:

1. **Что построено** — по задачам 1–3, с полными путями файлов.
2. **Доказательства TDD** — по задаче: какой тест падал первым и с какой ошибкой, что сделало его зелёным.
3. **Таблица волн** — волна, задачи, что кому принадлежало, какие файлы делили (ни одного общего файла между 1 и 2 — это и было условием параллельности).
4. **Числа приёмки** — три `decision_id`, вывод `group by principal, session_id`, текст ошибки `CHECK`, точный `indexdef` нового индекса, вывод `alembic heads`.
5. **Что код заставил сделать** — как минимум: шесть DB-тестов на восьмисимвольных заглушках `01HZKEYA` перестали проходить `CHECK` и переведены на настоящие ULID; `Replay` не получил поля сессии, потому что `identity_digest` её уже несёт; две миграции вместо одной ради параллельных исполнителей; фактическое имя переменной окружения для URL миграций.
6. **Что изменилось для интегратора** — граница `Idempotency-Key`, отсутствие изменений на проводе.
7. **Отложено** — всё из §7.3 спеки.
8. **Находки ревью и как закрыты** — по факту.

- [ ] **Step 8: Финальная проверка и коммит**

```bash
cd service && <FULL> && <FULL>
cd service && git diff --stat ../contracts
cd service && uv run alembic heads
```
Expected: полный прогон зелёный дважды подряд; в `contracts/` изменён ровно один файл — `README.md`; `alembic heads` — одна строка.

```bash
git commit --only contracts/README.md docs/connect.md service/README.md service/CLAUDE.md CLAUDE.md -m "docs: an idempotency key is scoped to a session, and a key id has a checked shape

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
git commit --only docs/reports/task-26-v3.3-key-check-and-session-idempotency.md -m "docs(report): v3.3 — key id shape as a database constraint, replays per session

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

## Порядок и волны

| Волна | Задачи | Почему вместе / почему после |
|---|---|---|
| 1 | 1 ‖ 2 | пересечения файлов нет ни одного: 1 — `domain/principal.py`, `store/models.py`, `store/keys.py`, `migrations/0008`; 2 — `domain/replay.py`, `session/replay.py`, `api/app.py`. Тестовые файлы тоже разные, кроме `tests/store/test_repo.py`, который принадлежит только задаче 1 |
| 2 | 3 | требует 1 (файл `0008` должен существовать, иначе `down_revision = "0008"` — висячая ссылка) и 2 (иначе хранилище повторов и база на один коммит расходятся по границе). Трогает `store/models.py`, который правила задача 1, — потому и следующей волной, а не параллельно |
| 3 | 4 | документация и приёмка по слитому коду обеих предыдущих волн |

Ревью — соответствие спеке и качество — по слитому коду каждой волны; финальное ревью всей ветки перед слиянием. Параллельных веток, которых нужно уведомлять, нет.

## Что считать готовым

- Полный прогон зелёный дважды, собрано ≥ 1509 тестов (база `4abf185`), `git diff --exit-code ../contracts` пустой во всех задачах, кроме 4, где изменён ровно `contracts/README.md`.
- `re.fullmatch(KEY_ID_PATTERN, STATIC_PRINCIPAL) is None` — тест, а не рассуждение.
- Выпуск ключа проходит через `ensure_key_id_shape`; `mint_key_id` — единственное место, где рождается id.
- База отвергает `decisions.key_id = 'token'` и `api_keys.id` вне формы; `key_id IS NULL` проходит.
- Один `Idempotency-Key` + одно действие + один ключ + сессии `s1`/`s2` → два `decision_id` и две строки; та же сессия дважды → один `decision_id` и одна строка; два бессессионных вызова → один `decision_id` и одна строка.
- `alembic heads` — одна голова `0009`; `upgrade head` и `downgrade 0007` проходят на чистовой базе; `indexdef` содержит `NULLS NOT DISTINCT`.
- `tests/rules/test_latency.py` зелёный.
- Из корневого `CLAUDE.md` исчез пункт «`Idempotency-Key` по-прежнему не привязан к сессии»; появились два новых честных ограничения.
- Отчёт `docs/reports/task-26-v3.3-key-check-and-session-idempotency.md` написан с фактическими числами; PR упоминает service, adapters, benchmark.

---

## Self-review

**1. Покрытие спеки.**

| Раздел спеки | Задача |
|---|---|
| §1.1 дыра 1 (принципал держится на договорённости) | 1 |
| §1.1 дыра 2 (повтор не привязан к сессии) | 2 (ключ и маршрут), 3 (уникальность) |
| §2 решение 1 (паттерн живёт один раз) | 1, Step 3; второй экземпляр — только в миграции, и это названо в её комментарии |
| §2 решения 2–3 (`CHECK`, а не FK; оба столбца) | 1, Steps 10, 12 |
| §2 решение 4 (охрана в точке выпуска) | 1, Steps 5–7 |
| §2 решения 5–6 (тройной ключ, сентинел `-`) | 2, Steps 1, 3 |
| §2 решение 7 (`Replay` без поля сессии) | 2, Step 1 — тест `test_a_replay_does_not_answer_a_call_from_another_session` доказывает инвариант без поля |
| §2 решение 8 (`NULLS NOT DISTINCT`) | 3, Steps 0, 3; фолбэк описан в Step 0 и запускается только при провале проверки версий |
| §2 решение 9 (две миграции) | Global Constraints, «Миграции»; владение файлами — «Карта файлов» |
| §2 решение 10 (`0008` падает, а не чинит) | 1, Step 12, комментарий в `upgrade` |
| §3.1–§3.3 паттерн, `CHECK`, охрана | 1 |
| §3.4 миграция `0008` | 1, Steps 12–13 |
| §4.1 ключ хранилища и две названные коллизии | 2, Step 3 (докстринг) + `CLAUDE.md` (4, Step 5) |
| §4.2 вызов из API | 2, Step 10 |
| §4.3 `Replay` не меняется | 2 — в задаче нет ни одного шага, правящего `Replay`; это намеренно |
| §4.4 восстановление | 2, Steps 5–7 |
| §4.5 уникальность и `ON CONFLICT` | 3, Steps 3–4 |
| §4.6 миграция `0009` и опасность отката | 3, Step 6 + `service/README.md` (4, Step 3) |
| §4.7 что остаётся как было | регресс держат существующие тесты `tests/session/test_replay.py`, `tests/api/test_app.py` плюс новый «та же сессия — тот же `decision_id`» (2, Step 8) |
| §5 контракт (провод не меняется, проза меняется) | Global Constraints + 4, Steps 1–3 |
| §6 инварианты 1–10 | 1: не затронуто, вердиктов не касаемся; 2: 1, Step 1; 3: 1, Steps 5–7; 4: 1, Step 8; 5: 3, Step 1 и приёмка 4, Step 6; 6: 2, Step 8 и 3, Step 1; 7: 3, Step 1 (третий тест); 8: существующие тесты v3.2 не трогаются; 9: 3, Steps 7–8 (`alembic heads`); 10: `tests/rules/test_latency.py` в «Что считать готовым» |
| §7.1 таблица тестов | все восемь файлов заведены: `tests/domain/test_principal.py` (1, создаётся), `tests/store/test_keys.py` (1), `tests/store/test_repo.py` (1, 3), `tests/domain/test_replay.py` (2), `tests/session/test_replay.py` (2), `tests/api/test_app.py` (2), `tests/test_contracts.py` (регресс, `git diff --exit-code`), `tests/rules/test_latency.py` (регресс) |
| §7.2 шесть критериев приёмки | 4, Step 6 (1–4), 1/3 Steps 13/7 (5), Global Constraints (6) |
| §7.3 не входит | ничего из списка не запланировано |

**2. Плейсхолдеры.** «TBD», «similar to Task N», «add error handling» в плане нет. Два места заполняются по факту и названы прямо: отчёт (задача 4, разделы поимённо) и точный `indexdef`. Один артефакт формы найден при самопроверке и удалён: в задаче 1, Step 5 первая редакция теста была написана как `assert … if False else True` — утверждение, которое не может упасть ни при какой реализации; на его месте стоят три настоящих теста (форма, согласие с охраной, различимость). Одна условная инструкция сохранена сознательно: в задаче 1, Step 5 — «добавить `import re`, если его нет», потому что это факт о шапке файла, проверяемый за секунду.

**3. Сверка с деревом на `4abf185`.** Перечитаны и приведены по реальному коду: `domain/principal.py` (целиком, 19 строк — потому и приведён полностью), `domain/replay.py` (`ReplayKey` с двумя полями, `Replay.answers(request, principal)`), `session/replay.py` (`restore`, вызов `ReplayKey.of(record.key_id, record.idempotency_key)`), `api/app.py::_answer` (две строки `key`/`replay_key`, `parsed` доступен выше по функции), `store/models.py` (`DecisionRow.__table_args__` из девяти элементов, `ApiKeyRow` без `__table_args__`), `store/repo.py::insert` (`index_elements=[table.c.principal, table.c.idempotency_key]`), `store/keys.py` (`ApiKeyRepo.create` с `id=str(ULID())`), `store/mapper.py` (`_GENERATED = {"principal"}` — не меняется), `migrations/versions/0007_key_attribution.py` (`revision = '0007'` → наши `0008`, `0009`), `tests/conftest.py` (схема поднимается `Base.metadata.create_all`, поэтому `CheckConstraint` доезжает до тестовой базы без Alembic), `tests/store/test_repo.py` (`rec(session_id=…)`, `_seed_session`, `replace`, `IntegrityError` уже импортированы). Одно место, где дерево заставило добавить работу, выписано отдельным шагом: 1, Step 11 — шесть DB-тестов с восьмисимвольными `01HZKEYA`/`01HZKEYB` перестают проходить новый `CHECK`, и это не регресс, а первое срабатывание ограничения.

**4. Согласованность имён и типов.** `KEY_ID_PATTERN: str`, `ensure_key_id_shape(key_id: str) -> str`, `mint_key_id() -> str`, `NO_SESSION: str` — по одному определению на имя, все в задачах 1 и 2, все используются под теми же именами в задачах 1, 2 и 4. `ReplayKey.of(key_id, session_id, key)` — три аргумента в трёх вызовах (`api/app.py`, `session/replay.py`, тесты), нигде не осталось двухаргументной формы: это проверяет Step 4 задачи 2, где падение вызывающего кода — ожидаемый результат. Имена ограничений и индекса — `ck_api_keys_id_ulid`, `ck_decisions_key_id_ulid`, `ux_decisions_principal_session_idempotency_key` — совпадают в модели, в миграции, в приёмке и в документации. `down_revision` образует ровно одну цепочку `0007 → 0008 → 0009`, что проверяется `alembic heads` в задачах 3 и 4.
