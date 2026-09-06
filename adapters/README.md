# adapters — Gate в кодинг-агентах

Направление 1. Плагины, которые встраивают гард-сервис в open-source кодинг-агенты:
перед каждым тулколом спрашивают гард (`allow | ask | deny`), а результат каждого тула
пропускают через фильтр (`pass | mask | drop`) до попадания в модель — так prompt-инъекции
из файлов и веба не доходят до модели.

Поддержаны **шесть харнессов**. Патч нужен только opencode 1.x и Kilo — у остальных
нашлись штатные точки расширения:

| Харнесс | Точка входа | Патч | HITL для `ask` | Статус |
|---|---|---|---|---|
| opencode 1.x | плагин (`permission.ask` на патче, иначе fallback) | да, ~65 строк | нативный Run/Deny | ✅ live TUI |
| Kilo CLI | тот же плагин (форк opencode) | да, **свой** (Kilo переписал permission) | нативный Run/Deny | ✅ live TUI |
| Pi (pi.dev) | extension (`tool_call`/`tool_result`) | не нужен | `ctx.ui.confirm` | ✅ live |
| opencode 2.0 | `Plugin.define` (`ctx.tool.hook`) | нет | — | ✅ live |
| Codex CLI | плагин с `hooks.json` (Pre/Post/PermissionRequest) | нет | нативный промпт | ✅ live |
| DeepSeek Harness | cordis `tools/pre-execute` + `tools/post-execute` | нет | штатный approval-seam | ✅ live |

У Codex заметная оговорка: в 0.146 из PreToolUse работает только `deny`, а подмена
результата не поддерживается — авто-одобрение живёт в `PermissionRequest`, `mask`
вырождается в `block`. Детали по каждому харнессу — в
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

Проверено против **настоящего гард-сервиса** (`service/`, ступени 1 и 2) — все шесть харнессов,
27 решений в его ленте. Для быстрых прогонов есть моки: `packages/mock-guard` (гард на
регулярках) и `recon/mock-llm.mjs` (скриптованная модель). Как проверить руками —
[docs/MANUAL-TESTING.md](docs/MANUAL-TESTING.md). Гард — не наша зона; контракт — `../contracts/openapi.yaml` (генерируется из моделей сервиса).

## Устройство

```
packages/
  core/         @agentgate/gate-core — вся логика: HTTP-клиент, нормализатор,
                маппинг тулов, режимы, кэш, политика fail-open/closed. Ноль зависимостей от харнесса.
  plugin-v1/    @agentgate/gate-plugin — серверный плагин + TUI-плагин для opencode 1.x и Kilo
  plugin-v2/    @agentgate/gate-plugin-v2 — реэкспорт v1 (opencode 2.0 использует тот же Hooks-API)
  plugin-pi/    @agentgate/gate-plugin-pi — extension для Pi
  plugin-codex/ marketplace-плагин для Codex CLI: hooks.json + три hook-скрипта.
                sync-core.sh вендорит core внутрь — Codex копирует плагин в свой кеш.
  plugin-dsh/   @agentgate/dsh-gate — cordis-плагин для DeepSeek Harness
  installer/    @agentgate/gate — npx-инсталлер (install/status/doctor/mode/uninstall)
  mock-guard/   @agentgate/mock-guard — заглушка гарда для тестов и демо
build/          патчи по харнессам (patches/<h>/) + build.sh; см. build/README.md
recon/          логгер-плагин и скриптованная модель для live-прогонов
samples/        реальные запросы с живых сессий — для тестирования сервиса
docs/           INSTALL, CONCEPT, ARCHITECTURE, DECISIONS, MANUAL-TESTING,
                inspect-openapi.yaml — см. docs/README.md
```

Стек: TypeScript, исполняется нативно Node ≥ 22.6 (`--experimental-strip-types`) и Bun —
без сборки. Тесты — встроенный `node --test`, без зависимостей.

## Контракт

- **PreToolUse** (`out`) — `POST /v1/decide` строго по `../contracts/openapi.yaml`.
- **PostToolUse** (`in`) — `POST /v1/inspect`, по аналогии (эндпоинт ещё не реализован
  на стороне гарда; путь и форма в конфиге). Расхождения — `docs/contract-gaps.md`.
- Адаптер знает только `AGENTGATE_URL`, `AGENTGATE_TOKEN`, опц. `profile_id`.
- Недоступность гарда: fail-open по умолчанию (`on_unavailable`), `GATE_FAIL_CLOSED=1` — строгий режим.

## Режимы

`auto` (решает гард) · `ask` (всё через человека) · `allow` (без промптов, но `in`-фильтр
работает) · `off` (плагин молчит). Хранится в `~/.config/gate/state.json`, переключается
мгновенно. **Shift+Tab** циклит `auto → ask → allow`; `/gate <mode>` — везде.

## Проверка

```bash
# из adapters/
npm test                        # 112 тестов на моках, без сети наружу

# живой прогон одного харнесса:
node packages/mock-guard/src/server.ts --port 8400 &     # гард
node recon/mock-llm.mjs --port 8899 --scenario sweep &   # скриптованная модель
node packages/installer/bin/openmagi.js status --guard-url http://127.0.0.1:8400
```

Полный сквозной прогон каждого харнесса — в [docs/MANUAL-TESTING.md](docs/MANUAL-TESTING.md).

## Сборка патченного бинарника (opencode 1.x / Kilo)

```bash
build/build.sh opencode https://github.com/anomalyco/opencode.git v1.17.18
build/build.sh kilo     https://github.com/Kilo-Org/kilocode.git   v7.5.6
```

Требует `bun`. Собирает под текущую платформу, пишет в `~/.local/share/gate-catalog/<harness>/<tag>/`
и обновляет `build/manifest.json` с sha256 (файл машинно-зависимый, в `.gitignore`).
Билд пинится к точной версии оригинала (общая SQLite).

У Kilo **свой** патч: форк переписал permission-модуль, и патч opencode на него не ложится.
Подробности и приёмка — [build/README.md](build/README.md).
