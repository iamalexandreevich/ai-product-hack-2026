### Task 13: CLAUDE.md и документация

**Files:**
- Create: `CLAUDE.md` (корень репозитория)
- Modify: `service/README.md`, `contracts/README.md`

- [ ] **Step 1: CLAUDE.md**

`CLAUDE.md` в корне:

```markdown
# CLAUDE.md — AgentGate (ai-product-hack-2026)

Читается первым. Актуальная спека v1: `docs/superpowers/specs/2026-09-03-agentgate-v1-design.md`. План: `docs/superpowers/plans/2026-09-03-agentgate-v1.md`. Исходные материалы (частично устарели, при расхождении права спека v1): `docs/base.md`, `docs/artifacts/`. Позиционирование: `docs/why-agentgate.md`.

## Что строим

Отдельный сервис между кодинг-агентом и ОС. `POST /v1/decide` получает одно действие плюс последний запрос пользователя и возвращает `allow | deny(reason, suggest) | ask`. Каскад: нормализация по AST → ступень 1 (hard-deny, профиль, allowlist, без LLM) → ступень 2 (LLM через OpenAI-совместимый API, structured output) → эскалация. Решения в Postgres и JSONL.

## Папки и кто в них пишет

- `service/` — ядро сервиса (направление 2).
- `adapters/` — плагины харнессов (направление 1).
- `benchmark/` — внутренний и внешний бенчмарк (направление 3).
- `contracts/` — схемы `/v1/decide`, шаблон deny-сообщения, `hook_client.py`. Меняется только PR-ом с упоминанием всех трёх направлений.

## Зафиксировано в v1

- Fail-closed везде: ошибка, таймаут, невалидный запрос или ответ → `ask`, HTTP 200. Никогда `allow` по ошибке.
- Hard-deny не переопределяется и не заменяется эскалацией.
- Решение по сырой строке запрещено; только `NormalizedAction` из AST.
- Ступень 2 reasoning-blind: в промпт идут только профиль, prose-слоты, `[TASK]`, `[ACTION]`, `[FLAGS]`, `[STAGE1]`. `metadata`, выводы инструментов, рассуждения агента — никогда.
- `deny`/`ask` не кэшируются, `allow` кэшируется на сессию.
- Один YAML-профиль на сервисе; харнессы о нём не знают.
- Только Postgres. Ретраев к LLM нет.
- Не в v1: PostToolUse/observe, история, модуль пакетов, ступень 3, override, панель, обучение.

## Правила работы

- Перед изменением схем `DecideRequest`/`DecideResponse` — обновить спеку и перегенерировать `contracts/` (`uv run python scripts/export_contracts.py`).
- Любой код, возвращающий `allow`, имеет тест на путь отказа.
- Табличные тесты hard-deny включают обфускацию (`$(…)`, `eval`, переменные, base64).
- Latency ступени 1 p50 ≤ 1 мс проверяется тестом.
- Секреты только через переменные окружения.
- Код и комментарии — английский; документация — русский; идентификаторы API не переводятся.

## Команды

```bash
cd service && uv sync
cd service && docker compose up -d db
cd service && AGENTGATE_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate uv run alembic upgrade head
cd service && AGENTGATE_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate uv run python -m agentgate
cd service && AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test uv run pytest
```
```

- [ ] **Step 2: Обновить README сервиса и контрактов**

В `service/README.md` заменить абзац «Запуск (после реализации)» на реальные команды из CLAUDE.md, добавить раздел «Профили»: где лежат, как добавить модель (три поля в `models.configs`), как переключить через поле `model` в запросе. В `contracts/README.md` добавить пример вызова `hook_client.py` из Task 12 Step 2 и таблицу кодов выхода.

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md service/README.md contracts/README.md
git commit -m "docs: CLAUDE.md and service/contracts READMEs

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```
