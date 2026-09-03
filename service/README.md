# service — ядро AgentGate

FastAPI-сервис: `POST /v1/decide`, `GET /v1/decisions`, `GET /v1/profiles/{id}`, `GET /healthz`. Конвейер: нормализация по AST → ступень 1 (hard-deny, профиль, allowlist) → ступень 2 (LLM через OpenAI-совместимый API) → эскалация → ответ; решения в Postgres и JSONL.

Спека: `docs/superpowers/specs/2026-09-03-agentgate-v1-design.md`. План реализации: `docs/superpowers/plans/2026-09-03-agentgate-v1.md`.

Запуск (после реализации): `docker compose up` из этой папки; переменные окружения `AGENTGATE_DB_URL`, `AGENTGATE_TOKEN`, `AGENTGATE_BIND`, `AGENTGATE_PROFILES_DIR`, `AGENTGATE_LOG_PATH` и ключи провайдеров по именам из профилей.
