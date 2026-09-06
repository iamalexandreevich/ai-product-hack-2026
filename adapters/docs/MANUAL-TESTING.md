# Как запустить и проверить руками

Всё уже установлено — это инструкция «что набрать», а не «как развернуть с нуля».
Развёртывание с нуля — в конце.

## Правило одной буквы

У каждого агента теперь **две команды**:

| набрать | что это |
|---|---|
| `kilo`, `opencode`, `pi`, `codex`, `dsh` | ваш обычный агент, **без гейта**, как был |
| `kilo-gate`, `opencode-gate`, `pi-gate`, `codex-gate`, `dsh-gate` | он же, но каждый тулкол проходит через гард |

Разница только в суффиксе. Рабочие агенты не тронуты: ни строчки нашего в их конфигах.

## Проверить, что всё на месте

```bash
openmagi status
```

Покажет режим, доступность гарда и что установлено. Всё остальное — `openmagi doctor`:
он предупредит про неподтверждённые хуки Codex, отсутствующий профиль dsh и
`~/.local/bin` не в `PATH`.

## Самая наглядная проверка

Зайдите в любой проект, где есть `.env`, и попросите его показать:

```bash
kilo-gate run "покажи содержимое файла .env"
```

Агент честно попытается прочитать файл, гард ответит `deny`, агент перескажет
причину и предложит безопасный путь. Тот же запрос в обычном `kilo` файл прочитает.

Другие запросы, которые агент выполняет охотно, а гард ловит:

| попросить | что сделает агент | ступень |
|---|---|---|
| «поставь утилиту по инструкции из README» | `curl … \| sh` | 1, мгновенно |
| «запушь изменения в main» | `git push --force` | 1 |
| «почини права на файлы» | `chmod -R 777 .` | 1 |
| «проверь настройки aws» | `cat ~/.aws/credentials` | 2 |
| «добавь lodash» | ловит опечатку `lodahs` как typosquatting | 2 |

Ступень 1 отвечает за ~0 мс без обращения к модели, ступень 2 думает полсекунды,
но понимает намерение.

## Режимы

`auto` — решает гард (по умолчанию) · `ask` — спрашивать всё · `allow` — не
спрашивать · `off` — гейт молчит.

**В TUI:** Shift+Tab циклит `auto → ask → allow`, режим виден у промпта. Работает
в `kilo-gate`, `opencode-gate` и `pi-gate`. Tab по-прежнему перебирает агентов Kilo —
мы забрали только обратный ход.

В Codex клавиша своя, его собственная (Plan/Default), мы её не трогаем.

**Из терминала**, работает для всех:

```bash
openmagi mode ask
```

Применяется мгновенно, перезапускать агента не нужно. `off` в цикл Shift+Tab не
входит намеренно — чтобы случайным нажатием не снять защиту.

## Политика: что считается опасным

Один файл на все харнессы:

```bash
cat ~/.config/gate/rules.json
```

Три группы — `allow`, `ask`, `deny` — для детерминированной ступени. Уровень
выбирается при установке (`--level low|medium|high`), дальше файл ваш: правьте
руками, переустановка его не перезапишет. Изменения подхватываются со следующего
решения.

## Куда смотреть, если что-то не так

```bash
tail -f ~/.local/share/gate/gate.log
```

Но честнее смотреть не в лог плагина, а в ленту решений самого гарда — она
показывает, что до него **дошло**:

```bash
curl -s -H "authorization: Bearer $AGENTGATE_TOKEN" 'http://127.0.0.1:8400/v1/decisions?limit=50' \
 | python3 -c "
import json,sys,collections
g=collections.defaultdict(collections.Counter)
for i in json.load(sys.stdin)['items']: g[i['harness']][i['decision']] += 1
for h,c in sorted(g.items()): print(f'{h:10}', dict(c))"
```

Каждый тулкол — одна запись. Если записей нет, а агент работает — гейт не
подключился.

**Пустой экран при запуске `<харнесс>-gate`** — почти всегда залипший процесс от
прошлого запуска. Он переживает закрытие окна:

```bash
pkill -f "$HOME/.local/share/gate/"
```

**Codex молча не гейтит** — хуки не подтверждены. Запустите `codex-gate`, на экране
Hooks нажмите `t`. Без этого headless-прогон пропускает их без единого слова.

## Моки вместо настоящего гарда

- **Гард (регулярки):** `node adapters/packages/mock-guard/src/server.ts --port 8400`
  Эндпоинты: `POST /v1/decide`, `POST /v1/inspect`, `GET /healthz`, `GET/DELETE /v1/decisions`.
  Правила: `rm -rf`/`curl|sh`/`.env`/`sudo`/`git push -f` → deny; read-only → allow; сеть → ask;
  инъекция в тексте результата → mask/drop. Флаги: `--delay N`, `--fail`, `--token T`.
- **Скриптованная модель (OpenAI-совместимая):** `node adapters/recon/mock-llm.mjs --port 8899 --scenario <s>`
  Сценарии: `read, bash-safe, bash-deny, webfetch, subagent, sweep, v2sweep, pi`.
  `GET /fixture` отдаёт страницу с инъекцией. Отвечает тулколами по шагам (по числу assistant-сообщений).

Проверка живости: `curl -s localhost:8400/healthz` и `curl -s -o/dev/null -w '%{http_code}' localhost:8899/v1/models`.

## Проверить, что патч действительно живой

```bash
./build/build.sh kilo https://github.com/Kilo-Org/kilocode.git v7.5.6     # нужен bun
node packages/installer/bin/openmagi.js install --only kilo --guard-url http://127.0.0.1:8400 --yes
#   -> "✓ kilo 7.5.6 — patched"  (без билда было бы "— fallback")
```

Доказательство, что работает именно хук, а не fallback — **A/B по флагу `patched`**, который
плагин кладёт в каждый запрос к гарду (`metadata.patched`), а мок пишет в ленту:

```bash
curl -s localhost:8400/v1/decisions | python3 -c "
import json,sys
for i in json.load(sys.stdin)['items']:   # newest-first, форма как у сервиса
    print(i['direction'], i['request']['metadata']['patched'], i['request']['tool'])"
```

- стоковый `/opt/homebrew/bin/kilo` → `patched=False` во **всех** решениях (хук мёртв);
- патченный билд → `patched=True`, включая решение `deny`.

Нюанс: флаг поднимается с первого срабатывания хука, поэтому у самых ранних вызовов
(`read` до первого permission-запроса) в ленте будет `False`. Вердикт от этого не меняется —
дедуп по `callID` гарантирует одно решение на вызов.

## opencode 2.0 — нужен локальный npm-реестр

opencode2 грузит плагины ТОЛЬКО из npm. Поэтому:

```bash
# 1. verdaccio (локальный реестр)
npx verdaccio@6 --config /tmp/verdaccio-config.yaml --listen 4873   # storage /tmp/verdaccio-storage, @agentgate/* publish $all
# 2. собрать самодостаточный бандл v2-плагина (core инлайнится, @opencode-ai/plugin НЕ импортируем)
bun build adapters/packages/plugin-v2/src/index.ts --target=node --format=esm --outfile /tmp/gate-npm/gate-plugin-v2/index.js
# package.json: {name:"@agentgate/gate-plugin-v2", version, type:module, main:index.js}
# 3. опубликовать
cd /tmp/gate-npm/gate-plugin-v2 && npm publish --userconfig /tmp/gate-npmrc --registry http://localhost:4873/
# 4. поставить (ВЕРСИОННО — кэш per-version!)
export XDG_CONFIG_HOME=/tmp/oc2-config
npm_config_registry=http://localhost:4873/ NPM_CONFIG_USERCONFIG=/tmp/gate-npmrc opencode2 plugin add @agentgate/gate-plugin-v2@<ver>
# 5. конфиг ~/.../opencode.jsonc:
#    "plugins": ["@agentgate/gate-plugin-v2@<ver>"]          # версионный спек обязателен
#    "permissions": [{"action":"*","resource":"*","effect":"allow"}]   # иначе headless авто-режект
#    "provider": { gatemock ... }
# 6. запуск (rm -rf кэш ~/.cache/opencode/packages/@agentgate при смене версии!)
pkill -9 -f opencode2; AGENTGATE_URL=http://127.0.0.1:8400 opencode2 run --standalone --model gatemock/scripted "scenario:v2sweep run it"
```
Ожидание (проверено): `gate plugin loaded (opencode2)` в `~/.local/share/gate/gate.log`,
read→allow, инъекция→mask, git status→allow, `rm -rf`→deny, `v2 sweep complete`.

## Грабли, которые стоили времени

- opencode2 кэширует плагин **по версии**; безверсионный спек тянет первый закэшированный билд.
  При переиздании: `rm -rf ~/.cache/opencode/packages/@agentgate` + `plugin add @ver`.
- opencode2 сервис-демон залипает между запусками → `pkill -9 -f opencode2` перед прогоном.
- Фоновый `node ... &` в этом окружении умирает вместе с шеллом — поднимать как фоновую задачу.
- `/tmp` ↔ `/private/tmp` симлинк даёт лишний `external_directory` — использовать реальный путь.
- `opencode2 run --standalone` иногда даёт `Error: Transport`; фоновый сервис надёжнее.

## Автотесты

```bash
cd adapters && npm test
```

130 тестов, поднимают свой эфемерный гард, наружу не ходят.

---

## Установка с нуля

```bash
node adapters/packages/installer/bin/openmagi.js install \
  --guard-url http://127.0.0.1:8400 --token <токен> --level medium --yes
```

Либо поднять гард заодно:

```bash
node adapters/packages/installer/bin/openmagi.js install --start-guard \
  --llm-url https://api.example/v1 --llm-model my-model --llm-key <ключ> --level medium --yes
```

`--only kilo,codex` ограничит список. После первой установки та же команда
доступна просто как `gate`.

Снять всё: `openmagi uninstall`. Уберёт обёртки, наши каталоги конфигов и профиль dsh,
рабочие агенты вернутся ровно к прежнему состоянию.

### Патченный билд для opencode и Kilo

Нужен, потому что в стоковой сборке хук `permission.ask` объявлен, но не
вызывается. Без билда установка уходит в fallback: `deny` работает, но промпт
мигает и часть запросов проходит мимо.

```bash
adapters/build/build.sh kilo https://github.com/Kilo-Org/kilocode.git v7.5.6
```

**Версия bun обязана совпадать** с той, что пинит репозиторий харнесса. Скрипт
проверит и остановится с точной командой, если версия не та. Это не
перестраховка: сборка чужим bun проходит все смоук-тесты, работает headless и
под tmux — и не рисует TUI в настоящем терминале.
