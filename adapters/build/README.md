# Патч и сборка билдов

opencode 1.x и Kilo объявляют хук `permission.ask`, но **никогда его не вызывают**
(проверено перечислением всех `trigger(...)` в обоих бинарниках). Патч оживляет хук:
добавляет в permission-сервис reviewer, который видит эффективное решение правил
(`allow`/`ask`) и может заменить его на `allow`/`ask`/`deny`. Это даёт 1.x ту же точку
перехвата, что у opencode 2.0 через `permission.hook("evaluate")`.

Без патча плагин работает в **fallback**: `deny` через `throw`, `allow` — авто-ответом
на событие (промпт мигает), а не-тульные permission-запросы (`external_directory`,
эскалации) до нас вообще не доходят.

## Патчи — по одному на харнесс

```
patches/opencode/0001-permission-reviewer.patch
patches/kilo/0001-permission-reviewer.patch
```

Общий патч не годится: Kilo переписал `packages/opencode/src/permission/index.ts`
(557 строк против 223 у upstream — свои `ConfigProtection`, `KiloHeadless`, `drain`,
сессионные правила). Отличаются и точки врезки: `ask` возвращает `AskOutcome`, ранний
выход из авто-одобрения несёт решающее правило, а `Service.of` отдаёт три лишних метода.
Патчи ставят одинаковую семантику разными якорями.

## Сборка

```bash
./build.sh <harness> <repo-url> <tag>
./build.sh opencode https://github.com/anomalyco/opencode.git v1.17.18
./build.sh kilo     https://github.com/Kilo-Org/kilocode.git   v7.5.6
```

Клонирует тег, накладывает `patches/<harness>/*.patch`, собирает через bun, кладёт
бинарник в `~/.local/share/gate-catalog/<harness>/<tag>/` и дописывает `manifest.json`.

**Версия обязана совпадать точно.** opencode/kilo держат сессии и креды в общей SQLite;
билд другой версии прогонит миграцию схемы и сломает оригинальную установку. Поэтому
инсталлер берёт только точное совпадение `(harness, tag, platform)`, иначе — fallback.

## manifest.json не коммитится

Он машинно-зависимый: `build.sh` пишет туда локальные пути (`file:///Users/...`) и
sha256 свежего билда. В `.gitignore`. На свежем клоне манифеста нет — инсталлер это
переживает и молча уходит в fallback, поэтому **перед демо соберите билд**, иначе
получите мигающий промпт вместо чистого auto-режима.

Собирается только текущая платформа (`--single`). CI-матрицы на 4 платформы нет.
