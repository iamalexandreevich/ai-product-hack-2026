# benchmark

Направление 3.

- **Внутренний бенчмарк** — сравнение моделей ступени 2 внутри сервиса: один набор кейсов гоняется через `POST /v1/decide` с полем `model`, результаты читаются из `GET /v1/decisions` или JSONL-лога (поля `decision`, `stage`, `model`, `latency_ms`, `profile_hash`, `metadata`).
- **Внешний бенчмарк** — сервис целиком против других решений (Claude Code Auto Mode, Codex Auto-review, статические правила) по метрикам ASR, Utility, FP, Friction, Latency.

Источники данных и бейзлайны — `docs/artifacts/agent-gate-artifacts.md`, `docs/base.md` §6. В `metadata` запроса передавайте `run_id` и параметры прогона, сервис их хранит и возвращает как есть.
