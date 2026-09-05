# Task 9: ключ кэша с дайджестами задачи и истории

STATUS: DONE_WITH_CONCERNS

Commit: `a43b718` в worktree `v4-t9` (ветка `v4/task-9`, база `e2c9627`).

## Падающие тесты (до реализации)

```
tests/session/test_cache_key.py::test_inspect_cache_key_depends_on_task_and_history_too
  TypeError: inspect_cache_key() takes 3 positional arguments but 5 were given
tests/engine/test_inspector.py::test_cache_key_distinguishes_two_tasks_with_one_output
  assert 1 == 2  (cache.puts)
tests/engine/test_inspector.py::test_cache_key_distinguishes_two_histories_with_one_output
  assert 1 == 2  (cache.puts)
```

## Что сделано

Точно по брифу:
- `agentgate/session/cache_key.py`: `inspect_cache_key` берёт `task_digest`/`history_digest`, docstring обновлён.
- `agentgate/engine/inspector.py`: `_Context` несёт `dialogue`; `_resolve` строит `Dialogue.of(request.history)`, берёт `intent = request.user_request or dialogue.last_human_request() or ""`, кладёт оба дайджеста в ключ; `inspect` распаковывает `resolved.dialogue` и передаёт его в `_run_stage2` → `_classify`; `_classify` больше не строит `Dialogue.of(request.history)` сам, берёт переданный объект. Остальной код `inspector.py` не тронут.
- Тесты: три новых в `tests/engine/test_inspector.py`, один новый + переименование в `tests/session/test_cache_key.py` (`test_cache_key_is_content_plus_profile_plus_provenance_kind` → `test_cache_key_depends_on_content_profile_and_provenance_kind`).

Отклонений от брифа нет.

## Прогон

`uv run pytest tests/session tests/engine tests/api -q` — 194 passed, 1 skipped.

`AGENTGATE_TEST_DB_URL=...agentgate_test_v4 uv run pytest -q` (полный прогон) — нестабилен: три последовательных запуска дали три разных набора провалов (3 failed / 16 failed+3 errors / 2 failed+3 errors), при этом падающие тесты каждый раз проходят в изоляции (`pytest <конкретный тест>` — зелено). Похоже на конкуренцию за общую БД `agentgate_test_v4` с другой задачей волны v4, выполняющейся параллельно в соседнем worktree по тем же brief-констрейнтам (все task-N воркеры используют одно и то же имя базы). Ни один из падающих тестов не относится к изменённым файлам (`store/`, `test_bootstrap.py`, `test_cli_keys.py`, `e2e/`) — inspect/session/engine/api сьюты, которые правит эта задача, зелёные при каждом прогоне.

## Concerns

- Полный прогон с `agentgate_test_v4` не даёт стабильного зелёного результата из-за параллельной нагрузки на общую тестовую БД со стороны другой задачи волны v4 — не связано с диффом этой задачи. Рекомендация: перепрогнать полный сьют, когда соседние task-воркеры не используют `agentgate_test_v4` одновременно.
