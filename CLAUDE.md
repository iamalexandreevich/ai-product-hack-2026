# CLAUDE.md — AgentGate (ai-product-hack-2026)

Читается первым. Актуальная спека v1: `docs/superpowers/service/specs/2026-09-03-agentgate-v1-design.md`. План: `docs/superpowers/service/plans/2026-09-03-agentgate-v1.md`. Исходные материалы (частично устарели, при расхождении права спека v1): `docs/base.md`, `docs/artifacts/`. Позиционирование: `docs/why-agentgate.md`.

Дорожная карта версий (v1→v5: контекст на v1–v4, независимость от провайдера модели на v5), принятая владельцем продукта: `docs/superpowers/service/specs/context-versions-roadmap.md`. Спека API-ключей: `docs/superpowers/service/specs/api-keys.md`. Спека деплоя: `docs/superpowers/service/specs/deploy.md`. Сверка с контрактом адаптера (Gate↔Guard) и расхождение по fail-open/fail-closed: `docs/superpowers/service/specs/adapter-contract-gap-analysis.md`.

## Что построено (v1, завершён)

Отдельный сервис между кодинг-агентом и ОС. `POST /v1/decide` получает одно действие плюс последний запрос пользователя и возвращает `allow | deny(reason, suggest) | ask`. Каскад: нормализация по AST → ступень 1 (hard-deny, профиль, allowlist, без LLM) → ступень 2 (LLM через OpenAI-совместимый API, structured output) → эскалация. Решения пишутся в Postgres и в JSONL.

Реализованные HTTP-эндпоинты: `POST /v1/decide`, `GET /v1/decisions`, `GET /v1/profiles/{profile_id}`, `GET /healthz` (см. `service/agentgate/api/app.py`). Аутентификация: статический токен (`AGENTGATE_TOKEN`) и/или выданные API-ключи — см. «API-ключи» ниже.

## Папки и кто в них пишет

- `service/` — ядро сервиса (направление 2). Подробные правила — `service/CLAUDE.md`.
- `adapters/` — плагины харнессов (направление 1).
- `benchmark/` — внутренний и внешний бенчмарк (направление 3).
- `contracts/` — схемы `/v1/decide`, `openapi.yaml`, шаблон deny-сообщения, `hook_client.py`. Меняется только PR-ом с упоминанием всех трёх направлений — см. `contracts/README.md`.

## Зафиксировано в v1

- Fail-closed везде: ошибка, таймаут, невалидный запрос или ответ → `ask`, HTTP 200. Никогда `allow` по ошибке.
- Hard-deny не переопределяется и не заменяется эскалацией.
- Решение по сырой строке запроса запрещено; только по `NormalizedAction` из AST.
- Ступень 2 reasoning-blind: в промпт идут только профиль, prose-слоты, `[TASK]`, `[ACTION]`, `[FLAGS]`, `[STAGE1]`. `metadata`, выводы инструментов, рассуждения агента — никогда.
- `deny`/`ask` не кэшируются, `allow` кэшируется на сессию (allow-only cache).
- Один YAML-профиль на сервисе; харнессы о нём не знают.
- Workspace привязан к сессии: `${WORKSPACE}` берётся из `cwd` первого запроса сессии и дальше не меняется (без `session_id` — из `cwd` текущего запроса). `Profile` — конфигурация оператора, `Policy` — профиль, привязанный к одному workspace; правила и промпт видят только `Policy`.
- Только Postgres (asyncpg). SQLite не поддерживается.
- Ретраев к LLM нет: один вызов, один таймаут.
- Не в v1: PostToolUse/observe, история диалога, модуль пакетов, ступень 3, override, панель, обучение. Дорожная карта по истории — `context-versions-roadmap.md`.

## API-ключи

Ключи выдаются только из CLI (`python -m agentgate keys create|list|revoke`), эндпоинта для выпуска нет — см. `service/README.md`. Проверка на горячем пути дополняет статический токен: bearer проходит, если совпадает с `AGENTGATE_TOKEN` **или** с действующим выданным ключом (аддитивно, не замена). Результат проверки ключа кэшируется в памяти процесса на короткий TTL.

## Правила работы

- Перед изменением схем `DecideRequest`/`DecideResponse` — обновить спеку и перегенерировать `contracts/` (`cd service && uv run python scripts/export_contracts.py && uv run python scripts/export_openapi.py`).
- Любой код, возвращающий `allow`, имеет тест на путь отказа.
- Табличные тесты hard-deny включают обфускацию (`$(…)`, `eval`, переменные, base64).
- Latency ступени 1 p50 ≤ 1 мс проверяется тестом.
- Секреты только через переменные окружения; `service/.env` в гите нет и не будет — не читать, не коммитить.
- Код и комментарии — английский; документация — русский; идентификаторы API не переводятся.

## Известные ограничения / roadmap

- **Атрибуция решения к ключу не подключена.** По ключу пишется только `last_used_at` (после ответа, best-effort). `key_id` в `DecisionRow`/JSONL не попадает — спека `api-keys.md` этого хочет, код пока нет (см. docstring `ApiKeyRow` в `service/agentgate/store/models.py`).
- **Кэш проверки ключа — per-process.** В многопроцессном деплое (несколько воркеров uvicorn) отзыв ключа доходит до каждого воркера независимо, в пределах TTL каждого — задержка отзыва не единая на весь сервис, а по худшему из воркеров.
- **v2→v4 (история диалога, оценка tool-result, Context Guard)** — не реализованы, порядок и обоснование зафиксированы в `context-versions-roadmap.md`.
- **Fail-open vs fail-closed конфликт с контрактом адаптера** — контракт Gate↔Guard по умолчанию fail-open на клиенте, наш сервис жёстко fail-closed изнутри; разногласие и три варианта решения — в `adapter-contract-gap-analysis.md`. Не решено владельцем продукта на момент написания.
- **`AGENTGATE_TOKEN` на non-localhost bind** по-прежнему обязателен и принимается наравне с ключами; более строгий вариант спеки api-keys.md («non-localhost принимает только ключи, токен игнорируется») в v1 сознательно не реализован — см. docstring `agentgate/api/deps.py`.

## Отчёты

После каждой завершённой задачи — отчёт в `reports/task-<N>-<slug>.md` на русском: что построено, доказательства TDD, находки ревью и как закрыты, принятые решения, что отложено.

## Коммиты

Коммит после каждой задачи, только явные пути. Сообщение заканчивается строкой:

```
Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
```
