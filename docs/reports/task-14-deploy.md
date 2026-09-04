# Задача 14: деплой v1.5 как общий эндпоинт для интеграции

Дата: 4 сентября 2026. Спека: `docs/superpowers/service/specs/2026-09-04-deploy-public-endpoint-design.md`. План: `docs/superpowers/service/plans/2026-09-04-deploy-public-endpoint.md`. Исходная фактура: `docs/reports/deploy-handoff.md`.

## Что построено

- **`main` (v1.5) развёрнут** на `109.172.95.51`. На сервере `agentgate/engine/`, `pipeline.py` удалён `rsync --delete`.
- **HTTPS без покупки домена:** `https://109.172.95.51.sslip.io`. Caddy в `service/docker-compose.deploy.yml` (оверлей только для сервера) + `service/deploy/Caddyfile`; сертификат Let's Encrypt получен по `tls-alpn-01` через 6 секунд после старта. Имя хранится на сервере в `.env` как `AGENTGATE_PUBLIC_HOST`, в репозитории IP не появился.
- **Postgres закрыт снаружи:** `127.0.0.1:5433:5432` в `docker-compose.yml`. До задачи порт 5433 с паролем `agentgate` был доступен с любого адреса, файрвол выключен.
- **`git_sha` в `/healthz`:** `make deploy` → `GIT_SHA` build-arg → `AGENTGATE_GIT_SHA` → `Settings.git_sha` → поле ответа. Аддитивное поле контракта, `contracts/openapi.yaml` перегенерирован.
- **Makefile:** одна переменная `COMPOSE` с оверлеем для всех целей; rsync берёт исключения из `.gitignore` (`--filter=':- .gitignore'`), явными остались `.env` и `.previous_gate_image`; после серверной проверки — проверка `/healthz` по HTTPS с машины оператора, с ретраями.
- **Страница интегратора** `docs/connect.md`: адрес, где взять токен, три `curl`, переменные адаптеров, смысл `401`/`ask`.
- **Гигиена:** лишний беспарольный ключ `agentgate-deploy` удалён с сервера и локально; в локальном `service/.env` ключ `TOKEN` переименован в `AGENTGATE_TOKEN` (файл не читался и не коммитился); серверный `.env` дополнен `AGENTGATE_PUBLIC_HOST`.

## Доказательства TDD

| Тест | Красный | Зелёный |
|---|---|---|
| `tests/test_config.py::test_git_sha_*` (3) | `AttributeError: 'Settings' object has no attribute 'git_sha'` | после `Settings.git_sha` с валидатором пустой строки |
| `tests/api/test_app.py::test_healthz_reports_git_sha_from_settings`, `test_healthz_git_sha_is_null_when_unset` | `KeyError: 'git_sha'` | после поля в `Health` и передачи в `healthz` |
| `tests/test_contracts.py` | — | зелёный после `export_openapi.py`; дифф `openapi.yaml` только новое поле |

Полный прогон перед деплоем: 623 passed, 49 skipped (тесты с базой без `AGENTGATE_TEST_DB_URL`).

Конфигурация проверялась до деплоя: `docker compose … config` с оверлеем показал `host_ip: 127.0.0.1` для 5433, `GIT_SHA` в build-args, Caddy на 443; базовый compose без оверлея не требует `AGENTGATE_PUBLIC_HOST`; `caddy validate` на Caddyfile — `Valid configuration`; `rsync -n` показал, что `.env`, `.venv`, кэши не уезжают, а `pipeline.py` и `docker-compose.yml.bak` удаляются.

## Приёмка на живом сервере

| # | Проверка | Результат |
|---|---|---|
| 1 | `https://109.172.95.51.sslip.io/healthz` | `ok`, `db: true`, `git_sha` = `abee952…` = HEAD на момент деплоя |
| 2 | `POST /v1/decide` по HTTPS с серверным токеном (выполнено с самого сервера, токен не выводился) | `npm test` → `allow`, stage 1; `curl … \| sh` → `deny`, stage 1 |
| 3 | `nc -z 109.172.95.51 5433` с ноутбука | закрыт |
| 4 | `http://109.172.95.51:8400/healthz` | отвечает (намеренно оставлен) |
| 5 | `.previous_gate_image` на сервере | записан (образ v1 `93050fd1…`), `make rollback` возможен |
| — | `docker compose ps` | `caddy` 80/443, `db` healthy только на loopback, `gate` 8400 |

## Находки и как закрыты

1. **Первый `make deploy` упал на проверке HTTPS** (`tlsv1 alert internal error`): серверный `/healthz` на 8400 ответил раньше, чем Caddy получил сертификат. Сервис при этом был развёрнут корректно. Закрыто: `curl --retry 12 --retry-delay 5 --retry-all-errors` в Makefile, отдельный коммит.
2. **Postgres был опубликован на `0.0.0.0`** — не упоминалось в handoff, найдено при разведке. Закрыто bind-адресом; пароль не менялся (см. «Отложено»).
3. `docker compose config` с оверлеем без `AGENTGATE_PUBLIC_HOST` падает с понятным сообщением — так и задумано, чтобы не поднять Caddy без имени.

## Решения владельца (4 сентября)

- Разворачивать `main` как есть, включая изменённый `contracts/` — согласие трёх направлений считается полученным.
- Один общий `AGENTGATE_TOKEN` для интеграторов; ключи `agk_…` не раздаются в этой задаче.
- TLS через sslip.io без покупки домена.
- Все четыре пункта гигиены и запуск `make deploy` внутри задачи.

## Отложено

- **Закрыть 8400** (`127.0.0.1:8400:8400`), когда интеграторы перейдут на HTTPS. Записано в `CLAUDE.md`.
- **Лимит Let's Encrypt у sslip.io** общий на всех пользователей домена; Caddy сам падает на ZeroSSL. Если откажут оба — купить домен, поменять `AGENTGATE_PUBLIC_HOST`.
- Ротация `VDS_PASSWORD` и пароля Postgres.
- Выдача ключей интеграторам, атрибуция решений к ключу, `llm` в `/healthz`.
- `git push` не выполнялся: коммиты лежат в локальном `main`.
