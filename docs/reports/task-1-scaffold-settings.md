# Task 1 — Каркас `service/` и настройки

**Статус:** закрыт. **Ветка:** `feat/agentgate-task-1`. **Коммиты:** `a2e0979`, `2215d6a`, `593a461`, `90b8339`.

## Что построено

| Файл | Содержимое |
|---|---|
| `service/pyproject.toml` | FastAPI, pydantic v2, SQLAlchemy 2 async + asyncpg, Alembic, bashlex, httpx, python-ulid, PyYAML; dev-группа pytest + pytest-asyncio; `asyncio_mode = "auto"`, `testpaths = ["tests"]` |
| `service/agentgate/config.py` | `Settings` с префиксом `AGENTGATE_`: `db_url`, `token`, `bind`, `profiles_dir`, `log_path`, `default_profile`. Свойства `bind_host`, `bind_port`, `bind_is_localhost`; `validate_token_for_bind()`; `get_settings()` под `lru_cache` |
| `service/tests/test_config.py` | 18 тестов: дефолты, требование токена при не-localhost bind, параметризованные таблицы валидных и невалидных `bind`, отсутствие `db_url`, обе ветки `validate_token_for_bind()` |
| `service/tests/conftest.py` | autouse-фикстура, вычищающая `AGENTGATE_*` из окружения перед каждым тестом |
| `service/CLAUDE.md` | границы зоны работы, технические правила проекта, раздел «Отступления от плана» |
| `service/.gitignore` | `.venv/`, `logs/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.env`, `.env.*` |

Плюс `.python-version` (3.12), `uv.lock` (40 пакетов, все с `files.pythonhosted.org`), пустые `__init__.py`.

## TDD

- **RED (реализация):** `uv run pytest tests/test_config.py -v` → `ModuleNotFoundError: No module named 'agentgate.config'` на этапе сбора, до того как `config.py` существовал.
- **GREEN (реализация):** `2 passed`, без варнингов.
- **RED (фикс):** 8 тестов невалидных `bind` упали с `DID NOT RAISE ValueError` против неисправленного кода.
- **GREEN (фикс):** `uv run pytest -q` → `18 passed`, вывод чистый.

Проверка контроллера поверх: `uv run python -c "import sys; print(sys.version)"` → `3.12.11` при системном `python3` 3.11.6 — пин версии сработал.

## Ревью

Внутренний task-review (sonnet) вернул ноль находок. Внешнее ревью в отдельной сессии на диапазоне `a9a0edd..2215d6a` нашло **три Important**:

1. **`service/.env` не игнорировался.** Файл содержит боевые креды VDS (`VDS_PASSWORD`, `VDS_IP`, `VDS_LOGIN=root`), а корневой `.gitignore` в HEAD — пустой блоб; защищала только незакоммиченная строка в рабочем дереве. Закрыто: `.env` и `.env.*` в `service/.gitignore`.
2. **`bind_port` падал на живых конфигах.** `AGENTGATE_BIND` в значениях `localhost`, `0.0.0.0`, `127.0.0.1:` давал `ValueError`, а голый `::1` молча парсился как `host=':'`, `port=1`. Задача 13 вызывает `uvicorn.run(host=settings.bind_host, port=settings.bind_port)` — баг всплыл бы крашем старта. Закрыто строгой валидацией при конструировании.
3. **Тесты покрывали security-свойство по одному значению в каждую сторону.** Закрыто параметризованными таблицами.

Scoped re-review диапазона `2215d6a..593a461`: все три ADDRESSED, нового слома нет.

## Решения, принятые за пользователя

- **`conftest.py`** получил фикстуру очистки `AGENTGATE_*` — бриф требовал создать файл, но не описывал содержимое, а без фикстуры `assert s.token is None` зависел бы от окружения машины.
- **Трейлер коммитов — `Claude Opus 5`**, а не `Claude Fable 5.1` из плана: инструкция сессии замещает прежние указания об атрибуции.
- **Фикс `bind` — строгая валидация, а не терпимый парсинг.** Кривой конфиг обязан ронять старт с внятным сообщением, а не молча биндить порт 1. Это тот же fail-closed, что записан в глобальных ограничениях сервиса.
- **Расхождение с планом зафиксировано в `service/CLAUDE.md`**, а не правкой плана: `docs/` вне зоны, но нерасписанное расхождение спровоцировало бы реализатора задачи 13 «восстановить» багованную версию из текста плана.

## Отложено (в финальное ревью)

`LOCALHOST:8400` считается не-localhost (безопасное направление — требует токен); `127.0.0.0/8` не распознаётся как loopback; токен из пробелов проходит `validate_token_for_bind()`; `lru_cache` на `get_settings` не сбрасывается между тестами; если в `SettingsConfigDict` появится `env_file=".env"`, фикстура перестанет изолировать тесты.

## Замечание по процессу

Внутренний task-ревьюер вернул ноль находок на том же коде, где внешнее ревью нашло три Important. Он честно сверил дифф с брифом — и именно поэтому промахнулся: бриф предписывал этот код дословно, а ревьюер, которому сказано «сверь с брифом», не оспаривает бриф. Узкий task-gate не заменяет широкое ревью.
