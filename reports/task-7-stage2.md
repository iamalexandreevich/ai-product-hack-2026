# Task 7 — Ступень 2 (LLM-классификатор)

**Статус:** закрыт. **Ветка:** `feat/agentgate-task-1`. **Базовый коммит:** `7995f10` (merge task 4).

Ворктри был создан харнессом от `a9a0edd` (документ с планом), а не от требуемого `7995f10`.
Проверено `git merge-base --is-ancestor HEAD 7995f10` — чистый fast-forward, рабочее дерево
чистое; выполнен `git reset --hard 7995f10`. После сброса `service/agentgate/normalize/shell.py`
и `service/agentgate/profiles/schema.py` на месте, `uv run pytest -q` — `112 passed`.

## Что построено

| Файл | Содержимое |
|---|---|
| `service/agentgate/stage2/schema.py` | `ClassifierOutput` (pydantic, `extra="forbid"`): `decision: Literal["A","D","U"]`, `risk: Literal[...] = "none"`, `reason: str = ""`, `suggest: str = ""`; `RESPONSE_JSON_SCHEMA` — `model_json_schema()` с принудительным `additionalProperties: false` и явным `required` |
| `service/agentgate/stage2/prompt.py` | `build_system_prompt(profile) -> str`, `build_user_message(action, user_request, stage1_note) -> str` |
| `service/agentgate/stage2/client.py` | `Stage2Error(Exception)` с полем `kind` (`timeout \| http \| invalid_json \| invalid_schema \| empty`); `LLMClient` — один HTTP-запрос к OpenAI-совместимому `chat/completions`, без ретраев, таймаут из `config.timeout_ms` |
| `service/agentgate/stage2/run.py` | `Stage2Result` (dataclass) и `run_stage2(...)`: `A/D/U → allow/deny/ask`, любая ошибка → `ask` с `reason = "classifier unavailable: <kind>"` |

Реализация выполнена по коду из брифа `docs/superpowers/service/sdd/task-7-brief.md` дословно
(сигнатуры, промпт, клиент, маппинг решений — как предписано), с двумя осознанными добавлениями
(см. «Решения» ниже). Подтверждено чтением кода, что `model_config_for` — метод `ModelsConfig`,
достижимый как `profile.models.model_config_for(...)` (не метод `Profile`, вопреки прозе плана) —
именно так его использует и бриф, и тесты Task 3; расхождения с реализацией нет.

## Закрытый список содержимого промпта

В модель попадает только:

- **Системный промпт:** фиксированный текст роли/инструкций + `RESPONSE_JSON_SCHEMA` (JSON) +
  `[PROFILE] workspace=... network=...(...) protected=...` (из `Profile.workspace`,
  `Profile.network.mode/allowed_domains`, `Profile.protected_paths`) + опционально
  `[ENVIRONMENT]`/`[ALLOWED BY USER]`/`[AVOID]` из `Profile.prose.environment/allow/soft_deny`.
- **Пользовательское сообщение:** `[TASK] <user_request>`, `[ACTION] tool=... cwd=...` и
  `argv=[...]` (из `NormalizedAction.commands[].argv`), либо, только при `flags.unparseable`,
  `raw=<action.raw>` (единственное намеренное исключение, см. ниже); опционально
  `mcp=<NormalizedAction.mcp>` (через `McpArgs.model_dump()` — там нет места для `metadata`
  вызывающего); `paths=[...] domains=[...]` (из `NormalizedAction.paths/domains`);
  `[FLAGS] ...` (из `NormalizedAction.flags`); `[STAGE1] <stage1_note>` — строка, которую строит
  вызывающий код ступени 1.

Никогда не попадают: `DecideRequest.metadata` (задаётся вызывающей стороной, не проверяется),
`raw` в общем случае, вывод инструментов, рассуждения агента. Проверено
`test_user_message_layout_and_blindness`: в запрос передан `metadata={"secret": "LEAK"}`, тест
утверждает `"LEAK" not in m`.

## Что бриф не знал (Task 4 review): `has_heredoc`, `has_unresolved_expansion`

К моменту написания брифа `Flags` описывался только четырьмя полями
(`unparseable/has_eval/has_subst/has_env_assign`). Ревью Task 4 добавило ещё два:
`has_heredoc` и `has_unresolved_expansion` — оба означают «что-то релевантное присутствовало, но
не полностью видно в структурированном действии». Строка `[FLAGS]` в `prompt.py` расширена, чтобы
рендерить оба поля — иначе модель судила бы о неполной картине как о полной. Тест из брифа
проверяет только подстроку (`"[FLAGS] unparseable=false has_eval=false has_subst=false" in m`),
поэтому расширение строки его не ломает.

Также учтено требование не давать «безобидное пустое действие» при `flags.unparseable`: когда
`bashlex` не смог разобрать команду, `commands`/`paths`/`domains` пусты по построению
(`agentgate/normalize/shell.py`), и `argv=[]` читалось бы как безопасный no-op. `build_user_message`
в этом случае подставляет `raw=<action.raw>` вместо `argv=[...]` — единственное место, где `raw`
попадает в промпт, и оно попадает намеренно как fail-closed мера, а не как побочный эффект.
Прочитан `agentgate/normalize/__init__.py`: `flags.unparseable` выставляется только в
`normalize_shell`, для остальных инструментов (`file_read`/`file_write`/`network`/`mcp_call`) веток,
устанавливающих этот флаг, нет — поэтому ветвление `if action.tool.value == "shell"` в
`build_user_message` сегодня корректно покрывает все случаи, когда флаг вообще может быть
выставлен.

## Fail-closed и отсутствие ретраев

`LLMClient.classify` делает ровно один `await self._http.post(...)` с
`timeout=config.timeout_ms / 1000`; ни цикла, ни ретраев, ни запасной модели нет нигде в
`client.py`/`run.py`. У каждого из пяти значений `Stage2Error.kind` — отдельный тест в
`test_stage2_client.py`, управляемый реальным транспортом (`httpx.MockTransport`), а не подменой
методов самого клиента:

- `timeout` — обработчик кидает `httpx.ReadTimeout`;
- `http` — обработчик реально возвращает статусы 400/401/429/500/503 (параметризовано);
- `invalid_json` — тело ответа `"not json"`;
- `invalid_schema` — валидный JSON, не проходящий валидацию `ClassifierOutput` (два случая:
  недопустимое значение `decision` и отсутствие поля `decision`);
- `empty` — пустая строка `content`, и отдельно — ответ вовсе без `choices`.

`run_stage2` протестирован на превращение `Stage2Error` (`http`, реальный 500) в
`DecisionKind.ask` с `error="http"` и `reason`, начинающимся с `"classifier unavailable"`, а также
на превращение неожиданного исключения (`RuntimeError` из обработчика) в `DecisionKind.ask` с
`error="unexpected"`. `allow` недостижим ни с одного пути ошибки — единственный путь к
`DecisionKind.allow` — успешный, прошедший валидацию схемы ответ модели с `decision == "A"`.

## TDD

**RED:** `cd service && uv run pytest tests/test_stage2_*.py -v`, до создания `agentgate/stage2/*`:

```
collecting ... collected 0 items / 3 errors
ModuleNotFoundError: No module named 'agentgate.stage2.client'
ModuleNotFoundError: No module named 'agentgate.stage2.prompt'
Interrupted: 3 errors during collection
```

Ожидаемо: три тестовых файла импортируют из `agentgate.stage2.*`, которого ещё не существовало.

**GREEN:** тот же запуск после реализации всех пяти файлов — `19 passed in 0.23s`.

**Полный набор, под `-W error`:** `uv run pytest -q -W error` → `131 passed in 0.32s`
(112 унаследованных + 19 новых), варнингов нет.

## Ручная проверка

`normalize()` на примере из брифа (`"npm install lodahs && rm -rf ./dist"` при
`cwd=/home/u/repo`) даёт `commands` с двумя `SimpleCommand`, `paths=['/home/u/repo/dist']`,
`domains=[]`, все флаги `False` — прогнано напрямую через `uv run python -c "..."` перед
написанием тестов, чтобы подтвердить точные строки, которые ожидает `test_user_message_layout_and_blindness`
(`argv=[["npm","install","lodahs"],["rm","-rf","./dist"]]`, `paths=[/home/u/repo/dist]`).

## Решения, принятые за пользователя

- **Расширена строка `[FLAGS]`** двумя полями (`has_heredoc`, `has_unresolved_expansion`),
  которых не было в коде брифа, но которые реально существуют в `Flags` после ревью Task 4 —
  см. раздел выше. Тесту из брифа это не противоречит (проверка по подстроке).
- **Добавлены докстринги** во все пять файлов `agentgate/stage2/*`, явно фиксирующие два
  глобальных инварианта (закрытый список содержимого промпта; fail-closed без ретраев) — чтобы
  будущее изменение, расширяющее промпт или добавляющее ретрай, спорило с явным комментарием, а
  не с отсутствием такового.
- Никаких других отступлений от кода брифа нет — `schema.py`, `prompt.py`, `client.py`, `run.py`
  реализованы дословно по предписанным сигнатурам и телам функций.

## Соответствие глобальным ограничениям

- **Закрытый список содержимого промпта:** подтверждён построчно в разделе выше;
  `DecideRequest.metadata` и `raw` (кроме единственного `unparseable`-исключения) в промпт не
  попадают — проверено тестом на «слепоту» к `metadata`.
- **Fail-closed, без ретраев:** один HTTP-вызов, один таймаут, ни одного цикла ретраев; каждый
  из пяти видов `Stage2Error` — отдельный тест на реальном транспортном уровне; `allow` по ошибке
  недостижим по построению кода `run_stage2`.
- **Решение только по `NormalizedAction`:** `build_user_message` строит текст из полей
  `NormalizedAction` (`commands`, `paths`, `domains`, `flags`, `mcp`, `tool`, `cwd`) и `action.raw`
  только в fail-closed ветке `unparseable`; сырая командная строка как основание решения нигде не
  используется.

## Отложено / не в рамках задачи

Не проверялось на живом LLM-эндпоинте — по требованию брифа клиент тестируется только против
`httpx.MockTransport`. Интеграция ступени 2 в `POST /v1/decide` (вызов `run_stage2` из
обработчика запроса, выбор модели по `DecideRequest.model`) — задача следующих шагов плана, не
входит в Task 7.
