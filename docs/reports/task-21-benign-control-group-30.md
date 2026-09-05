# Задача 21 — снять кап на размер контрольной категории и довести `benign_utility` до 30 кейсов

Направление 3 (`benchmark/`). Работа целиком внутри `benchmark/`.

## Зачем

FP-rate и Friction объявлены главными метриками проекта (спека §1) и считаются по контрольной
группе. До этой задачи в ней было 6 кейсов (5 обязательных уровней плюс `ultra_hard`), то есть шаг
метрики ~17–20 %: один `ask` на `benign_utility` давал 20 % ложных срабатываний. Цифра нечитаема —
на такой сетке нельзя отличить «сервис стал заметно тревожнее» от «один кейс сдвинулся».

Второе, более тихое ограничение: benign состоял из 5×`shell` + 1×`file_write`. Friction по
`mcp_call`, `network` и `file_read` не измерялась вообще, хотя все три инструмента есть в контракте
и все три атакующие категории по ним бьют.

## Что построено

### 1. Правило валидатора: кап снят точечно, покрытие уровней сохранено

- `schemas/case.py`: константа `UNCAPPED_CATEGORIES: frozenset[str] = frozenset({"benign_utility"})`
  рядом с `REQUIRED_DIFFICULTIES` / `OPTIONAL_DIFFICULTIES`. В докстринге зафиксировано «почему»:
  правило «один уровень — один кейс» защищает атакующую категорию от набивки лёгкими вариациями
  одной техники (иначе детект завышается); у контрольной группы техники нет, завышать нечего, а
  разрешение FP-rate и Friction равно 1/N.
- `dataset/validator.py::_check_categories`: проверка `missing difficulty levels` осталась для всех
  категорий без исключения, проверка `duplicated difficulty levels` пропускается для категорий из
  `UNCAPPED_CATEGORIES` (ранний `continue` после проверки покрытия).
- Докстринг модуля `dataset/validator.py` переписан: размер пинится «пятью, либо шестью с
  `ultra_hard`» только вне `UNCAPPED_CATEGORIES`.

**Принятое решение: явная константа, а не вывод из данных.** Правило вида «категория, где все кейсы
`is_benign`, освобождается от капа» выглядит элегантнее, но означает, что боевая категория молча
теряет защиту от набивки, если кто-то по ошибке проставит `attack_location: none`. Список имён
проверяется глазами в ревью, вывод из данных — нет.

- `cli.py:456,479`: help-строки `--subset` больше не обещают «exactly five cases per category» —
  формулировка переведена на правила покрытия сложностей.

### 2. Кейсы: `BENIGN_007` … `BENIGN_030`

Написано 24 новых кейса; вместе с шестью существующими контрольная группа — 30 кейсов, весь датасет
— 106 файлов в 16 категориях.

Уровни (кап снят, покрытие обязательных сохранено): `easy` 6, `medium` 6, `hard` 6, `adversarial` 6,
`realistic_production` 5, `ultra_hard` 1.

Инструменты — ровно целевой расклад: `shell` 14, `file_write` 5, `file_read` 4, `network` 4,
`mcp_call` 3. По трём инструментам (`file_read`, `network`, `mcp_call`) Friction измеряется впервые.

Оси покрытия, каждая сверена построчно с `service/profiles/default-dev.yaml`:

| Ось | Кейсы |
| --- | --- |
| `safe_prefixes` → ступень 1 без LLM (`npm test`, `npm run lint`, `cargo test`; `pytest` был в BENIGN_001) | 007, 008, 009 |
| Опасно по форме, ограничено workspace (`git clean -fdx ./target`, `chmod +x`, `sed -i`, `find -delete`) | 010, 011, 012, 013 |
| Граница `protected_paths`, но мимо шаблона (`.env.example` на запись и на чтение, `.github/workflows/`, `infra/k8s/values.staging.yaml`) | 017, 018, 021, 023 |
| Второй `allowed_paths` — `/tmp/agentgate-scratch` | 022 |
| «Страшное» имя при обычном содержимом (`scripts/rotate_keys.py`, `src/auth/credentials_loader.py`, поисковый запрос про OAuth-токены) | 019, 020, 029 |
| `protected_branches`, но мимо (`feature/*`) | 015 |
| Сеть по allowlist, в том числе через `tool: network` | 024, 025, 026, 027 |
| Пакет, прямо названный пользователем и запиненный | 014 |
| Read-only вызовы MCP | 028, 029, 030 |
| Документация внутри workspace | 016 |

Формат по эталону `BENIGN_004`: `expected_service_result: allow`, `acceptable_service_results` не
указан (валидатор подставляет `[allow]` сам), `attack_location: [none]`, `dataset_source` не указан
(`team` по умолчанию). YAML писались инструментом Write — heredoc в этом окружении ломается на
апострофах, которых в prose кейсов много.

`expected_stage` проставлен по реальному поведению ступени 1, а не «на глаз»:

- `1` — `safe_prefixes` (007–009) и все `file_read`/`file_write` внутри `allowed_paths` мимо
  `protected_paths`: `AllowlistRule` закрывает `file_read`/`file_write` целиком через
  `_paths_are_safe` (016–023);
- `2` — всё остальное: `tool: network` и `mcp_call` до ступени 1 вообще не относятся, `git clean`,
  `chmod`, `sed -i`, `find -delete`, `uv pip install`, `git push` проваливаются сквозь ступень 1
  без вердикта.

Поле информативное и не скорится, но неверное значение — это ложная подсказка о том, кто должен был
поймать кейс.

### 3. Документация

- `benchmark/CLAUDE.md`, §Dataset rules — новое правило («по одному на каждый обязательный уровень;
  в `UNCAPPED_CATEGORIES` уровни повторяются и размер не ограничен»), 106 файлов, расклад benign по
  инструментам; §«cli.py validate» — про `UNCAPPED_CATEGORIES`.
- `benchmark/README.md` — когда падает валидатор, плюс абзац про контрольную группу и 1/N.
- `benchmark/README.md` и `tools/sandbox/README.md` — числа адаптера `claude-code`: «60 из 75» →
  **«76 из 106»** (адаптер воспроизводит только `shell`); остальные 30 — `file_write` 10,
  `mcp_call` 8, `file_read` 7, `network` 5; оценка стоимости прогона — «примерно 76 сессий».
- `attacks/taxonomy.md` §3.15 — два новых пункта: почему у контрольной группы нет капа и как читать
  «сложность» там, где атаки нет, плюс расклад по инструментам и перечень осей.

## Доказательства

Тесты (`tests/test_dataset_validator.py`), написаны до правки валидатора:

- `test_the_control_group_may_repeat_levels_and_has_no_size_cap` — категория с именем
  `benign_utility` и 12 кейсами с повторяющимися уровнями валидна;
- `test_the_control_group_still_needs_every_required_level` — та же категория без уровня `hard`
  по-прежнему даёт ошибку `missing difficulty levels: hard`.

Существующие `test_a_seventh_case_duplicates_a_level_and_fails` и
`test_two_ultra_hard_cases_in_one_category_fail` работают на `_category(name="sample_category")`,
под новое правило не попадают и остались без правок — это и есть доказательство того, что кап снят
точечно, а не вообще.

Прогон:

```
uv run pytest                                        # 238 passed, 2 deselected (live)
uv run ruff check . && uv run ruff format --check .  # All checks passed / 46 files already formatted
uv run python cli.py validate --path attacks/cases   # 106 cases, 16 categories, errors: 0  warnings: 0
uv run python cli.py benchmark --path attacks/cases --category benign_utility --dry-run  # 30 кейсов
```

`warnings: 0` — важная часть: порог «пересечение токенов `raw` выше 90 %» на 30 кейсах в одной
категории срабатывает легко, и ни один из новых кейсов его не задел (варьируются команда, аргументы,
пути и `cwd` — шесть рабочих каталогов: `/home/dev/repo`, `/home/dev/web`, `/home/dev/api-gateway`,
`/home/dev/ledger-engine`, `/srv/projects/checkout`, `/workspaces/analytics`).

## Что не сделано и почему

- **`cargo add serde@1.0.210`** из ориентира в задаче не написан. Целевой расклад по инструментам
  (`shell` ровно 14) и полное покрытие четырёх `safe_prefixes` профиля конфликтовали за один слот;
  выбраны `safe_prefixes` — каждый из них отдельная строка профиля, регрессия по любой из них
  молча уводит ступень 1 в ступень 2. Ось «запиненный пакет, названный пользователем» при этом
  покрыта дважды: `npm install --save-dev vitest@2.1.4` (BENIGN_003) и
  `uv pip install httpx==0.27.2` (BENIGN_014).
- **`ultra_hard` в контрольной группе остался один** (BENIGN_006, из предыдущей задачи). У benign
  нет «сложности атаки», и второй такой кейс не измерял бы ничего нового.
- **Числа «76 из 106»** пересчитаны по количеству `shell`-кейсов, как и прежние «60 из 75». Отдельно
  адаптер `claude-code` не выносит решения по кейсам с `history` — это уже сказано в том же абзаце
  README и в число не заложено, ровно как было до правки.
