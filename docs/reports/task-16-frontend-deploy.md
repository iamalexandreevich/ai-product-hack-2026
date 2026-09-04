# Задача 16: сайт openmagi.ru из прототипа Claude Design

Дата: 4 сентября 2026. Спека: раздел «Вечер 4 сентября» в `docs/superpowers/service/specs/deploy.md`; описание папки — `frontend/README.md`.

## Что построено

- **Папка `frontend/` в корне** — четвёртое направление монорепы. `site/` отдаётся с сервера, `handoff/` хранит дизайн-спеку, `data.js` и `tokens.css` для будущей пересборки. Исходно файлы лежали в `service/frontend/`; перенесены, чтобы rsync сервиса их не тащил.
- **Caddy отдаёт статику** для `openmagi.ru` и `www.openmagi.ru` из `/srv/site` (bind-mount `/opt/openmagi-site`). Имена задаются `AGENTGATE_SITE_HOSTS` в серверном `.env`, как и имя API. Сертификат Let's Encrypt для корня получен автоматически.
- **`make deploy`** синхронизирует `frontend/site/` в `/opt/openmagi-site` рядом с `service/`, `check-clean` проверяет обе папки, финальная проверка открывает и API, и сайт по HTTPS с ретраями.
- **Прототип выкачен как есть.** `index.html` (бывший `OPENMAGI.dc.html`) и рантайм `support.js`; React, ReactDOM и Babel грузятся с unpkg в браузере. Решение владельца: сначала показать сайт, пересобрать в статику отдельной задачей.

## Доказательства

- `caddy validate` и `caddy adapt` локально: два маршрута, `api.example.test` → `reverse_proxy`, `example.test www.example.test` → `file_server`.
- `docker compose … config` с оверлеем: `AGENTGATE_SITE_HOSTS` и монтирование `/opt/openmagi-site:/srv/site` на месте.
- Прототип проверен в живом браузере до деплоя (локальный `http.server`): рендер, скролл-терминал, консоль без ошибок.
- После деплоя: `https://openmagi.ru/` и `https://www.openmagi.ru/` отдают 200, `index.html` 50 228 байт и `support.js` 69 150 байт; порт 80 → 308 на HTTPS; текст страницы в браузере содержит хиро, терминал, бенчмарк и политику; консоль пуста. `api.openmagi.ru/healthz` отвечает `git_sha` = `f6148cc`.

## Находки

- Первая проверка сайта по HTTPS после деплоя упала, пока Caddy получал сертификат для корня; ретраи в Makefile закрыли это, как и для API.
- У прототипа пустой `<title>`: заголовок задаётся внутри `x-dc`-шаблона, а не в `<head>`. Исправлять в пересборке.
- Сертификат корня выпущен только на `openmagi.ru`; `www` получил свой отдельный сертификат (Caddy выпускает по имени). Это нормально.
- Пуш `main` отклонён из-за параллельного коммита benchmark; влит `origin/main` merge-коммитом, `service/` и `frontend/` от этого не изменились, передеплой не потребовался.

## Отложено

- Пересборка в чистую статику по `frontend/handoff/README.md` (без Babel в браузере, `<title>`, метатеги).
- Открытые места хэндоффа: домен `openmagi.dev` для install-скрипта, ссылки `github.com/agentgate`, цифры бенчмарка, пути хуков харнессов.
- CORS на `api.openmagi.ru`, если сайт когда-нибудь начнёт звать API из браузера.
