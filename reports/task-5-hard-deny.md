# Task 5 — Ступень 1: hard-deny

**Статус:** закрыт. **Ветка:** `feat/agentgate-task-1`. **Базовый коммит:** `7995f10` (merge task 4, после исправления базы worktree — см. «Решения» ниже).

## Что построено

| Файл | Содержимое |
|---|---|
| `service/agentgate/stage1/types.py` | `Stage1Decision` (frozen dataclass): `decision`, `rule_id`, `reason`, `suggest=""`, `hard=False` (`True` только у hard-deny — не переопределяется и не заменяется эскалацией). `Check = Callable[[NormalizedAction, Profile], Stage1Decision \| None]` |
| `service/agentgate/stage1/hard_deny.py` | `check_hard_deny(action, profile) -> Stage1Decision \| None`: шесть правил в фиксированном порядке — `exfil`, `pipe-exec`, `destructive`, `protected-write`, `privilege`, `git-force`. Константы `SECRET_PATTERNS`, `NETWORK_COMMANDS`, `SHELLS`, `DOWNLOADERS`, `WRITE_COMMANDS` |
| `service/agentgate/stage1/__init__.py` | пустой |

Реализация выполнена по коду из брифа `docs/superpowers/service/sdd/task-5-brief.md` практически дословно (датаклассы, сигнатуры, константы, порядок правил — как предписано), с одним осознанным отступлением от буквального кода правила `destructive` (см. «Решения» ниже).

## TDD

- **RED:** `uv run pytest tests/test_stage1_hard_deny.py -v` → ошибка сбора, `ModuleNotFoundError: No module named 'agentgate.stage1.hard_deny'` — до создания пакета `stage1`. Совпадает с ожиданием брифа (Step 2) дословно.
- **GREEN:** тот же запуск после реализации → `42 passed` (28 DENY_CASES × проверка `rule_id`/`hard=True`/`reason`, 13 PASS_CASES, `test_file_write_protected`).
- Полный набор: `uv run pytest -q -W error` → `154 passed` (112 унаследованных из задач 1–4 + 42 новых), варнингов нет.

## Решения, принятые за пользователя

### Исправление базового коммита worktree

Worktree был создан харнессом от `a9a0edd` (docs: план на 13 задач), а не от требуемого `7995f10` (merge task 4). Проверка `git merge-base --is-ancestor HEAD 7995f10` показала чистый fast-forward, рабочее дерево было чистым — выполнен `git reset --hard 7995f10`. После сброса подтверждено: `service/agentgate/normalize/shell.py` и `service/agentgate/profiles/schema.py` существуют, `uv run pytest -q` даёт `112 passed` до начала работы над задачей.

### Отступление от буквального кода правила `_rule_destructive`

Бриф применяет одну и ту же проверку «цель совпадает с корнем workspace» ко всем трём деструктивным командам (`rm`, `find -delete`, `shred`):

```python
if not is_within(t, allowed) or (ws and os.path.normpath(t) == ws):
    return _deny(...)
```

Для `find <root> -delete` `t` — это корень поиска (первый позиционный аргумент), а не то, что удаляется безусловно: `-delete` стирает только найденные по предикату записи, а не сам корень. Применённая буквально, эта проверка запрещает `find . -name '*.pyc' -delete` — ровно тот кейс, который сам же бриф перечисляет в `PASS_CASES`. Я проверил это конкретно, до написания реализации: вычислил предикат из брифа на этом входе и получил `True` (запрет) — внутреннее противоречие в тексте брифа между кодом правила и его же таблицей тестов.

**Исправление:** проверка «равно корню workspace» ограничена командами `rm` и `shred` — обе безусловно уничтожают ровно тот путь, который им передан (значит, цель, совпадающая с корнем workspace, действительно означает «стереть всё»). Для `find` остаётся только `not is_within(t, allowed)` — корень поиска **за пределами** разрешённых путей (`find /`) по‑прежнему запрещён, а корень **внутри или равный** workspace (`find .`) — нет, что соответствует фактической семантике `-delete`.

Отступление не расширено на `rm`/`shred` — там ни один DENY_CASE/PASS_CASE не показывает противоречия, буквальный код брифа сохранён.

## Соответствие глобальным ограничениям

- **Hard-deny не переопределяется:** каждое правило возвращает `Stage1Decision` с `hard=True`; поле не варьируется — все шесть правил в `hard_deny.py` жёсткие по построению.
- **На каждый путь отказа есть тест:** для каждого из шести `rule_id` в таблице `DENY_CASES` есть минимум один срабатывающий кейс, а в `PASS_CASES`/остальных `DENY_CASES` — не срабатывающие соседние кейсы (например, `git push --force origin feature/x` проходит, а `git push --force origin main` — нет; `rm -rf /home/u/repo/build` проходит, а `rm -rf /home/u/repo` — нет).
- **Не решаем по сырой строке:** `check_hard_deny` принимает только `NormalizedAction` (из задачи 4) и `Profile` (из задачи 3); `action.raw` нигде не читается.
- **`has_unresolved_expansion`/`has_heredoc` не используются как основания для hard-deny.** Ни одно правило не проверяет эти флаги напрямую. Токен с нераспознанным `$VAR`/`~user`/brace-expansion уже отсутствует в `action.paths` и в целях редиректов (это гарантирует нормализатор из задачи 4) — затронутые правила просто видят меньше кандидатов и проваливаются в `None`, а не делают вывод из отсутствующих данных. Это осознанно, а не побочный эффект: в брифе эти флаги не упомянуты, а инструкция к задаче отдельно предупреждает не запрещать по ним (эскалация — материал задачи 6).
- **Пустые коллекции не означают «безопасно».** При `flags.unparseable=True` (`commands=[]`) ни одно правило не может сработать по своему позитивному условию — `check_hard_deny` возвращает `None`, но это не «проверено и чисто», а «hard-deny здесь ничего не может сказать»; неразбираемое действие — материал не-жёсткой эскалации ступени 2 (вне scope этой задачи).

## Дисциплина по scope

Не реализовано: сопоставление с профильным allowlist/package-slot-проверки — это задача 6, не эта. Не создано ничего вне `service/agentgate/stage1/`, `service/tests/test_stage1_hard_deny.py` и этого отчёта. `service/.env` не читался, не печатался, не перемещался.

## Отложено (в следующие задачи)

- Комбинация hard-deny с ask-эскалацией ступени 2 и цепочками действий — задача 6+.
- Сопоставление с allowed_paths/package allowlist в контексте не-hard решений — задача 6.
