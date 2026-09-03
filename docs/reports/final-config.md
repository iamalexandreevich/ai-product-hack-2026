# Финальный конфиг перед деплоем: три правки

## Проверка базового коммита

STEP ZERO: HEAD оказался на `8e2cb5f` в ветке `worktree-agent-a5245a93fd55ef445` — не предок
`9a3889b` и не потомок (несвязанная линия истории с revert'ом бенчмарка). По протоколу это
NEEDS_CONTEXT, а не безопасный reset — так и было доложено координатору без единой правки.

Координатор прислал явную поправку с перепроверкой из основного чекаута. Повторная проверка
из фактического cwd вызова (`/Users/alexander/Основное/ПРОЕКТЫ/ai-product-hack-2026`, основной
чекаут репозитория — `git rev-parse --git-dir` == `.git`, не linked worktree) показала: там
**уже** ветка `feat/agentgate-task-1`, `HEAD == 9a3889b`, дерево чистое. `git reset --hard`
не понадобился и не запускался — нужное состояние уже было на месте после перехода в правильную
директорию. Файлы-ориентиры (`agentgate/profiles/loader.py`, `profiles/default-dev.yaml`,
`docker-compose.yml`, `agentgate/profiles/schema.py`) на месте.

Базовый прогон `uv run pytest -q` (без БД): **446 passed, 43 skipped** — итог (489) совпадает с
заявленным в задаче, но раскладка отличается от указанной 466/23 (все 43 skip — по причине
`AGENTGATE_TEST_DB_URL not set`, других причин skip нет, падений нет). Принято как допустимое
расхождение базовой линии: итог и причины skip согласованы, ничего не сломано. С поднятой
локальной Postgres на `5433` (`AGENTGATE_TEST_DB_URL` выставлена): **507 passed, 0 skipped,
0 failed** под `-W error`.

## Изменение 1 (must-fix) — сузить glob `.env*` в protected_paths

`service/profiles/default-dev.yaml`, `protected_paths`:

```diff
-  - ".env*"
+  - ".env"
+  - ".env.local"
+  - ".env.*.local"
```

Старый glob `.env*` ловил и `.env.example`, `.env.sample`, `.env.tmp` — обычные шаблонные/tmp-файлы
без секретов, из-за чего `cp .env.example .env.sample` и `cat .env | grep -v SECRET > .env.tmp`
безэскалационно попадали под `hard-deny.protected-write`. Матчинг — `agentgate/normalize/paths.py:
matches_any`, паттерн без `/` сравнивается с basename через регистронезависимый `fnmatch`. `.env` и
`.env.local` — точные совпадения; `.env.*.local` ловит, например, `.env.production.local`, но не
`.env.local` (для него отдельная запись) и не `.env.example`/`.env.sample`/`.env.tmp`.

Тесты в `service/tests/test_stage1_chain.py` (на `DEFAULT` — уже существующий в файле шипованный
`default-dev.yaml`, загруженный через `load_profiles`):

- `test_dotenv_still_hard_denied_by_shipped_profile` — `cp x .env`, `echo x > .env` по-прежнему
  `hard-deny.protected-write`, `hard=True`.
- `test_dotenv_templates_examples_and_tmp_not_hard_denied_by_shipped_profile` — `cp x .env.example`,
  `cp .env.example .env.sample`, `cat .env | grep -v SECRET > .env.tmp` НЕ `hard-deny.protected-write`.
- `test_dotenv_local_variants_still_hard_denied_by_shipped_profile` /
  `test_dotenv_template_variants_not_hard_denied_by_shipped_profile` — то же через `file_write`,
  покрывает `.env.local`, `.env.production.local` (deny) vs `.env.example`, `.env.sample`,
  `.env.tmp`, `.env.template`, `.env.dist` (не deny).

RED подтверждён вручную: временно вернул YAML к `.env*`, прогнал `pytest -k dotenv` — три теста
«не должно денаиться» упали именно так, как ожидалось (`.env.sample`, `.env.tmp`, `.env.example`
ловились старым glob'ом), затем откатил к исправлению и перепроверил GREEN.

## Изменение 2 (харденинг) — fail-fast на отсутствующий токен в compose

`service/docker-compose.yml`:

```diff
-      AGENTGATE_TOKEN: ${AGENTGATE_TOKEN:-dev-token}
+      AGENTGATE_TOKEN: ${AGENTGATE_TOKEN:?set AGENTGATE_TOKEN before docker compose up}
```

`OPENROUTER_API_KEY: ${OPENROUTER_API_KEY:-}` оставлен как есть.

Проверено выполнением (`docker compose config`, в окружении есть Docker 29.4.3 / Compose v5.1.3):

- без `AGENTGATE_TOKEN`: код возврата 1, `error while interpolating
  services.gate.environment.AGENTGATE_TOKEN: required variable AGENTGATE_TOKEN is missing a value:
  set AGENTGATE_TOKEN before docker compose up`.
- с `AGENTGATE_TOKEN=realtoken123 OPENROUTER_API_KEY=k`: код возврата 0, конфиг рендерится с
  реальным токеном.

Отдельного pytest нет (это fail-fast на уровне Compose, не код приложения) — проверка выполнением
`docker compose config` в обе стороны.

## Изменение 3 — Gemini через OpenRouter, переопределяемая `OPENROUTER_MODEL_NAME`

### (a) Подстановка переменных окружения в загрузчике профиля

`service/agentgate/profiles/loader.py` — добавлена `interpolate_env(value)`, небольшой рекурсивный
проход (dict/list/строковые листья; int/bool/None не трогаются) по распарсенному YAML-словарю перед
`Profile.model_validate`. Поддерживает ровно две формы: `${NAME}` (пустая строка, если не задана) и
`${NAME:-default}` (default, если не задана). Никакого другого shell-расширения. Только stdlib
(`os`, `re`), без новой зависимости.

**Осознанное решение — `${WORKSPACE}` зарезервирован и не трогается.** В шипованном профиле
`${WORKSPACE}` в `allowed_paths` — это ДРУГОЙ, уже существующий механизм подстановки
(`Profile._expand`, резолвится на каждый запрос из обнаруженного workspace, не из `os.environ`).
Если бы подстановка окружения применялась единообразно, `${WORKSPACE}` съедался бы первым (при
отсутствии одноимённой переменной окружения — заменялся на `""`), молча ломая `allowed_paths` ещё
до того, как отработает `_expand`. Обнаружено через `grep -rn '\${' profiles/` до написания кода,
поэтому `WORKSPACE` явно исключён по имени в `_interpolate_string` и остаётся литеральной строкой
`${WORKSPACE}` — подтверждено тестом
`test_interpolate_env_workspace_placeholder_left_untouched` (выставляет реальную переменную
`WORKSPACE`, чтобы доказать, что она игнорируется), а также тем, что
`test_shipped_default_profile_loads` / `test_resolved_allowed_paths*` продолжают проходить
без изменений.

Тесты (`service/tests/test_profiles.py`):

- `test_interpolate_env_default_used_when_unset` / `test_interpolate_env_value_used_when_set` —
  ровно строка `${OPENROUTER_MODEL_NAME:-google/gemini-3.8-flash}`: без переменной → default, с
  переменной → её значение.
- `test_interpolate_env_bare_var_empty_when_unset` / `..._used_when_set` — голая форма `${X}`.
- `test_interpolate_env_literal_string_unchanged` — без `${`, строка не тронута.
- `test_interpolate_env_workspace_placeholder_left_untouched` — см. выше.
- `test_interpolate_env_recurses_through_dicts_and_lists` — вложенные dict/list, нестроковые
  значения не трогаются.
- `test_load_profiles_applies_env_interpolation` / `..._applies_env_interpolation_default` —
  сквозной прогон через `load_profiles` на временном файле профиля.
- `test_shipped_default_profile_default_model_is_gemini` — грузит реальный `default-dev.yaml`,
  проверяет `models.default == "gemini"`, без переменной резолвится в `google/gemini-3.8-flash`.
- `test_shipped_default_profile_model_override_via_env` — то же, с `OPENROUTER_MODEL_NAME` в
  окружении — переопределение побеждает.

RED подтверждён: до добавления `interpolate_env` `pytest tests/test_profiles.py` падал на этапе
сборки (`ImportError: cannot import name 'interpolate_env'`); после реализации все 26 тестов файла
проходят под `-W error`.

### (b) Модельный конфиг в шипованном профиле

`service/profiles/default-dev.yaml`, `models`:

```yaml
models:
  default: gemini
  configs:
    gemini:
      base_url: "https://openrouter.ai/api/v1"
      model: "${OPENROUTER_MODEL_NAME:-google/gemini-3.8-flash}"
      api_key_env: OPENROUTER_API_KEY
      timeout_ms: 3000
      structured_output: true
    sonnet: {...}   # без изменений
    local: {...}    # без изменений
```

`sonnet` и `local` оставлены байт-в-байт как были (только переупорядочены — `gemini` первым).
Слаг `google/gemini-3.8-flash` использован дословно, как задал продакт-оунер — не «исправлен» на
другой номер поколения. Неверный/несуществующий слаг закрывается fail-closed в `ask` на этапе
запроса силами уже существующей обработки ошибок ступени 2 (код ступени 2/конвейера не трогался).

## README

`service/README.md` — один короткий абзац после существующего раздела «как переключить модель»:
`default-dev.yaml` теперь по умолчанию использует Gemini через OpenRouter для ступени 2, строки в
YAML-профиле поддерживают подстановку `${VAR}` / `${VAR:-default}`, `OPENROUTER_MODEL_NAME`
переопределяет слаг без правки YAML, ключ — `OPENROUTER_API_KEY` (уже есть в таблице переменных).

## Результат прогона тестов

Из `service/`:

- Без БД: `uv run pytest -q -W error` → **464 passed, 43 skipped** (база 446 + 18 новых тестов:
  11 в `test_profiles.py`, 7 параметризованных случаев в 4 новых функциях в
  `test_stage1_chain.py`). Число skip не изменилось (43, все по БД). Без предупреждений, без
  падений.
- С `AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test`:
  **507 passed, 0 skipped, 0 failed** под `-W error` (база 489 + 18 = 507 — подтверждает, что
  и БД-часть ничего не сломала).

## `git status --short` (корень репозитория)

```
 M docs/superpowers/service/sdd/progress.md   <- не моё; меняется параллельно координатором в том же чекауте, не тронуто, не в staging
 M service/agentgate/profiles/loader.py
 M service/docker-compose.yml
 M service/profiles/default-dev.yaml
 M service/README.md
 M service/tests/test_profiles.py
 M service/tests/test_stage1_chain.py
```

В коммит попали только явные пути под `service/` из списка выше. `docs/superpowers/service/sdd/
progress.md` вне зоны (docs/ прямо запрещён `service/CLAUDE.md`) и менялся параллельно из другой
сессии — не тронут вообще.

## Наблюдение про окружение (для сведения)

В начале задачи cwd Bash-вызовов был нестабилен между вызовами: первые команды попадали в
изолированный worktree на несвязанной ветке (`worktree-agent-a5245a93fd55ef445` @ `8e2cb5f`), а
последующие (без явного `cd`) — уже в основной чекаут репозитория на `feat/agentgate-task-1` @
`9a3889b`, который к тому же несёт живые, незакоммиченные, параллельно меняющиеся изменения
(похоже, от родительской сессии — правка `docs/superpowers/service/sdd/progress.md` выше). С
момента, как это было замечено, все команды выполнялись с явным абсолютным `cd` в
`.../ai-product-hack-2026/service`, чтобы не потерять контекст снова. Отмечаю отдельно: `git reset
--hard`, отданный из нестабильного cwd, — ровно тот случай, когда можно молча попасть не в тот
чекаут; стоит внимания владельца харнеса, вне зависимости от исхода этой задачи.
