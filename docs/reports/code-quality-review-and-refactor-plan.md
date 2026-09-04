# Ревью качества кода AgentGate v1 и план переезда на OOP + SOLID + DRY

Дата: 3 сентября 2026. Статус: ревью + предложение архитектуры v1.5, сверенное с персональным стайл-гайдом владельца (раздел 10). Ждёт решения владельца продукта по трём открытым вопросам (раздел 8).

Что ревьюилось: `service/agentgate/` целиком (≈5100 строк без тестов, включая `scripts/`), `service/tests/`, спеки `docs/superpowers/service/specs/` (v1, v2–v4, api-keys, deploy, gap-analysis). Все 466 юнит-тестов зелёные, 43 пропущены без Postgres.

---

## 0. Вердикт коротко

**Это не лапша.** Границы пакетов совпадают со спекой, fail-closed выдержан на каждом пути и покрыт тестами, ступень 1 покрыта табличными тестами с обфускацией. Это лучшее, что есть в проекте, и переезд обязан это сохранить.

**Но это и не архитектура, которую можно показать как OOP + SOLID.** Код процедурный: одно понятие «решение» существует в четырёх формах, оркестратор `Gate.decide()` держит десять локальных переменных вместо доменного типа, знание о командах shell размазано по одиннадцати множествам в пяти файлах, а самый важный файл (`hard_deny.py`, 922 строки) на 43% состоит из истории ревью. Есть и одно расхождение со спекой с последствиями для безопасности (F4).

Главная мысль плана: **не переписывать, а свернуть.** Почти каждая находка ниже закрывается введением одного отсутствующего типа (`Verdict`, `Rule`, `Policy`, `ParsedArgv`, `CommandSpec`), после чего целые ветки, обёртки и дубли исчезают сами. Каждый шаг плана сохраняет поведение и держит 466 тестов зелёными.

| Метрика | Сейчас | После шагов 0–4 (оценка) |
|---|---|---|
| Представлений одного решения | 4 (`Stage1Decision`, `Stage2Result`, `DecideResponse`, `DecisionRecord`) | 1 (`Verdict`) + 2 проекции |
| Циклов «пройти по правилам» | 2 (`run_stage1`, `check_hard_deny`) + спецслучай | 1 (`RuleChain`) |
| Путей записи решения | 2 (один мёртв в проде) | 1 (`DecisionWriter`) |
| Самый большой файл | 922 строки | ≤ 300 |
| Ручных циклов `while i < len(argv)` | 4 | 0 |
| Множеств «какие команды…» | 11 в 5 файлах | 1 таблица |

---

## 1. Что уже хорошо и что нельзя потерять

- **Разбиение по ступеням** `normalize/ → stage1/ → stage2/ → session/ → store/ → api/` совпадает со спекой §3. Переезд меняет содержимое пакетов, не их смысл.
- **Fail-closed как дисциплина.** Каждый `except` в коде знает, во что превращается ошибка, и на каждый путь есть тест. Это и есть страховочная сетка для рефакторинга.
- **Табличные тесты ступени 1** (`test_stage1_hard_deny.py` 546 строк, `test_normalize_shell.py` 323) проверяют вход → вердикт, а не внутренности. Их можно не трогать ни на одном шаге, кроме переименования импортов.
- **Точки расширения уже есть**: `CHECKS`, `RULES`, `SessionStateStore` (Protocol), `Check` (Callable). Они правильные по замыслу, но недоделаны по форме (см. F5).
- **Бюджет latency не проблема**: измерено на реальном профиле, p50 всего пути (workspace + normalize + stage 1) = 0.21 мс при бюджете 1 мс. Ни одна находка ниже не про скорость.

---

## 2. Находки. Приоритет 1: структурные

### F1. `Gate.decide()` — оркестратор без доменного типа решения

`service/agentgate/pipeline.py:81-162`. Метод на 80 строк с кортежем из десяти локальных:

```python
decision, reason, suggest, stage, rule_id, model_used, raw_resp, error, stage2_ms, hard = (
    None, "", "", 1, None, None, None, None, None, False,
)
```

Дальше он вручную распаковывает `Stage1Decision` в эти переменные, потом `Stage2Result` в те же, потом мутирует их эскалацией, потом собирает из них `DecideResponse` и отдельно `DecisionRecord` через `_record()` с одиннадцатью позиционными аргументами. Возвращает кортеж `(resp, rec, state)`.

Почему это проблема: понятие «вердикт» не существует как тип, поэтому каждая ступень изобретает свой (`Stage1Decision`, `Stage2Result`), а оркестратор становится конвертером между ними. Любое новое поле (например, `key_id` для атрибуции или идемпотентность из gap-analysis) — это правка в шести местах.

Judo-ход: один `Verdict` (раздел 4), который возвращают и правила, и классификатор, и кэш, и ранний отказ API. Эскалация — функция `Verdict → Verdict`. `DecideResponse.from_decision()` и `DecisionRecord.from_decision()` — две тонкие проекции. Кортеж локальных и `_record()` исчезают целиком, `_finish_early` и `_ask()` из `app.py` (они строят один и тот же stage-0 ask) сливаются в `Verdict.ask()`.

### F2. Два пути персистентности, один из которых мёртв в проде

`pipeline.py:195-201` (`Gate._do_persist`) и `api/app.py:80-99` (замыкание `persist`). Оба ловят и глотают исключения, оба знают порядок записи. Но `__main__.build_app` создаёт `Gate(...)` **без** `persist`, а запись делает `app.py` через `BackgroundTasks`. Значит:

- `Gate.persist` используется только тестами `test_pipeline.py`. Тест проверяет путь, которого в проде нет.
- Продовый путь проверяется только фейками в `test_api.py`.
- TTL кэша `86400` объявлен дважды: `_CACHE_TTL_SECONDS` в `app.py:57` и `cache_ttl_seconds` в `Gate.__init__`, с комментарием, признающим дубль.
- Ключ кэша провозится контрабандой внутри JSONB-колонки: `normalized = dict(action.to_dict(), cache_key=cache_key)` в `pipeline.py:185`, а `app.py:93` вычитывает его обратно `rec.normalized.get("cache_key") or rec.id`. Это скрытый контракт через ключ словаря.

Judo-ход: один протокол `DecisionWriter` с реализациями `PostgresDecisionWriter` (порядок FK живёт только здесь), `JsonlDecisionWriter`, `CompositeWriter`. `Gate.persist` удаляется. `cache_key` становится явным полем. TTL — одно поле в `Settings`.

### F3. `SessionStateStore` разорван на две несвязанные половины

Спека §5.4: «счётчики и кэш живут в памяти за интерфейсом `SessionStateStore` и после каждого решения пишутся в Postgres; при старте восстанавливаются». В коде: `InMemorySessionStateStore` (память) и `SessionRepo` (Postgres) друг о друге не знают. Склейка write-through лежит в `app.py:88-97`, склейка restore — в `__main__.py:66-70`. Спека §10 обещает «Redis-реализацию того же интерфейса» при масштабировании, но сейчас для Redis пришлось бы править и `app.py`, и `__main__.py`.

Judo-ход: `PersistentSessionStateStore(SessionStateStore)` — память + write-through в `SessionRepo` + `restore()`. Оба куска склейки удаляются. Redis — один новый класс, как и обещано.

### F4. Workspace определяется на каждый запрос из `cwd` харнесса. Расхождение со спекой с последствиями для безопасности

Спека §6: «`${WORKSPACE}` подставляется из `args.cwd` **первого запроса сессии** … без `session_id` — из `cwd` текущего запроса». Код (`pipeline.py:98`): `with_workspace(base_profile, req.args.cwd)` на каждый запрос. `SessionState.workspace` заполняется при создании сессии и хранится в Postgres, но политикой не используется.

Воспроизведено на шаблонном профиле:

| `raw` | `cwd` | `allowed_paths` | ступень 1 |
|---|---|---|---|
| `rm -rf /home/u/other-project` | `/home/u/repo` | `[/home/u/repo, /tmp/agentgate-scratch]` | **deny** `hard-deny.destructive` |
| `rm -rf /home/u/other-project` | `/` | `[/, /tmp/agentgate-scratch]` | None → LLM |
| `rm -rf /etc` | `/` | `[/, …]` | None → LLM |
| `cp x /etc/cron.d/job` | `/` | `[/, …]` | None → LLM |

Агент, которому харнесс отдаёт текущий `cwd` после `cd /`, расширяет собственную песочницу до корня. Hard-deny не обходится полностью (`rm -rf /` по-прежнему deny, потому что равен workspace), но всё «вне workspace» перестаёт существовать как понятие. Спека это предусмотрела, код нет.

Дополнительно: `detect_workspace` делает `os.path.isdir` вверх по дереву на каждом запросе (I/O на бюджетном пути), `with_workspace` делает `model_copy` профиля на каждый запрос, `profile_hash()` считается на каждый запрос, `resolved_allowed_paths()` вызывается 4 раза и `resolved_protected_paths()` 2 раза за один серый запрос. Сегодня это 0.02 мс, но это форма, а не бюджет.

Judo-ход: разделить `Profile` (иммутабельный конфиг, хэш при загрузке) и `Policy` (профиль ⊗ workspace, пути разрешены один раз). `Policy` привязывается к сессии при её создании и берётся из `SessionState`. Регрессионный тест — таблица выше.

---

## 3. Находки. Приоритет 2: code-judo, дубли, границы

### F5. Две цепочки одной формы и спецслучай в хвосте

`stage1/chain.py` крутит `CHECKS`, `stage1/hard_deny.py:891-922` крутит `RULES`, а после них отдельным блоком проверяет «wrapper-chain unresolved» как fallback. Это один и тот же цикл «первый не-None побеждает», написанный дважды, плюс правило, которое не оформлено как правило.

Жёсткость вердикта закодирована дважды: поле `hard: bool` и префикс `rule_id` (`hard-deny.*` против `ambiguous.*`), через фабрики `_deny()` / `_ask()`.

Judo-ход: `Rule` как объект с `id`, `hard`, `evaluate()`. Одна `RuleChain`. `WrapperUnresolvedRule` — обычное правило после шести hard-deny. `_deny`/`_ask`, `Stage1Decision`, `types.Check` удаляются. `GET /v1/rules` поверх такой цепочки — одна функция, но в объём рефакторинга не входит (стайл-гайд 1.2, см. раздел 10).

### F6. Три места знают про `unparseable`

`pipeline.py:121` пропускает ступень 1, `pipeline.py:130` выбирает текст заметки, `stage2/run.py:47-55` отказывает до вызова LLM. Это одно детерминированное решение («не разобрали → ask, LLM не вызывать»), размазанное по двум ступеням.

Judo-ход: `UnparseableRule` первым правилом цепочки. Ступень 1 больше не пропускается, ступень 2 не достигается, `_NOTE_SKIPPED` исчезает. **Видимое изменение контракта**: для этого случая `stage` станет `1` вместо `2`, `model` — `null`. Спека §5.1 и так расходится с кодом здесь (она отправляет unparseable в LLM, что ревью Task 7 запретило). Нужно решение владельца (вопрос 1).

### F7. `Gate` знает, как построить `LLMClient`

`pipeline.py:131-133`: конструирование клиента и вызов `run_stage2` с шестью аргументами внутри оркестратора. Проверка «unknown model» — `try/except KeyError` вокруг `model_config_for`.

Judo-ход: протокол `Classifier` и реестр `{model_name: Classifier}`, собранный при старте из профиля. Unknown model = отсутствие ключа. Фейки в тестах реализуют протокол, а не `httpx.MockTransport` (тесты `LLMClient` на транспорте остаются как есть).

### F8. Ручной разбор argv, четыре копии

`hard_deny.py`: `_sent_secret_paths`, `_excluded_read_paths`, `_consumes_piped_stdin`, `_positional_args` — четыре цикла вида `while i < len(argv): … i += 2 if consumed_next else 1`. Комprehension `[a for a in argv[1:] if not a.startswith("-")]` встречается шесть раз в `stage1/`. Всего в одном файле 18 проверок `startswith("-")`.

Judo-ход: `ParsedArgv` (exe, positionals, options с именем/значением/inline) строится один раз на `SimpleCommand` по спецификации value-флагов команды. Все четыре цикла становятся обращениями `.option("-T")`, `.positionals`.

### F9. Три ответа на «какие пути трогает команда» и два списка секретов

- `action.paths` — из `PATH_COMMANDS` + `looks_like_path` (`normalize/shell.py:428-465`).
- `command_argv_paths()` — все non-flag токены (`stage1/argv_paths.py`).
- `_cmd_paths()` — `looks_like_path` + редиректы + stdin (`hard_deny.py:231-250`).

Docstring `argv_paths.py` прямо говорит, что расхождение первых двух уже было багом (task 6, round 2). Третий ответ живёт рядом и ждёт своей очереди.

`_SENSITIVE_BASENAMES` (`normalize/paths.py:24-35`) и `SECRET_PATTERNS` (`hard_deny.py:94-98`) — два пересекающихся, но неравных списка секретных файлов. Добавить `.npmrc` в один и забыть во втором — это дыра в exfil.

### F10. Одиннадцать множеств «какие команды…» в пяти файлах

`PATH_COMMANDS`, `MUTATING`, `WRITE_COMMANDS`, `READONLY`, `GIT_READONLY`, `NETWORK_COMMANDS`, `DOWNLOADERS`, `INTERPRETERS`, `SHELLS`, `FIREWALL`, `_WRAPPER_CMDS` плюс производные `_EFFECTIVE_WRAPPERS`, `_WRAPPER_VALUE_FLAGS`, `_SCP_RSYNC_VALUE_FLAGS`, `_IGNORE_VALUE_FLAGS`, `_UPLOAD_FLAGS`. Одна команда (`tee`, `sed`, `find`, `curl`) описана в трёх-четырёх местах разными свойствами.

Judo-ход для F8–F10: одна таблица `CommandSpec` (имя, класс, какие аргументы пути, какие флаги берут значение, какие флаги отправляют наружу, что является целью записи). Все множества становятся запросами к таблице. «Добавить команду» — одна строка. Это самый большой шаг плана и единственный, который трогает ядро безопасности, поэтому он последний и идёт с корпусом эквивалентности (раздел 6, шаг 5).

### F11. Импорт приватных имён через границу пакета

`hard_deny.py:90`: `from agentgate.normalize.shell import _ENV_ASSIGNMENT, _WRAPPER_CMDS, _WRAPPER_VALUE_FLAGS, resolve_effective_argv`. Знание «что делает wrapper-команда с argv» нужно и нормализатору (heredoc → shell), и ступени 1 (effective command). Это отдельное понятие, а не деталь нормализатора.

Judo-ход: публичный модуль `shell/wrappers.py`. Подчёркивания исчезают, `hard_deny` перестаёт зависеть от `normalize`.

### F12. Нетипизированные границы API

`create_app(settings, gate, decision_repo, session_repo, profiles, jsonl, db_probe=None, key_repo=None)` — три репозитория без типов, восемь параметров. `/v1/decisions` возвращает `dict` без `response_model`, из-за чего его схема в `openapi.yaml` написана руками (см. F14). `make_require_token` принимает `background: BackgroundTasks = None  # type: ignore`.

### F13. `DecisionRecord` — ручной маппинг в три стороны

`store/repo.py:20-65`: `to_row()` через `self.__dict__.copy()` с переименованием `metadata → metadata_`, `from_row()` — ручная копия 20 полей, `to_dict()` с форматированием `ts`. Затем `app.py` дважды делает `dict(r.to_dict(), decision_id=r.id)`, потому что поле называется `id`, а контракт хочет `decision_id`.

Judo-ход: `DecisionRecord` как pydantic-модель с полем `decision_id` и `from_attributes=True`. Три маппера и два переименования исчезают.

---

## 4. Находки. Приоритет 3: размер файлов, модульность, читаемость

### F14. Три файла, которые надо разобрать до того, как их увидят судьи

| Файл | Строк | Что не так |
|---|---|---|
| `stage1/hard_deny.py` | 922 | 43% строк — комментарии и docstring; 26 упоминаний «fix round», 22 — «Important N». Это история ревью, а не инварианты. Судья прочитает changelog вместо правила. |
| `normalize/shell.py` | 500 | 12 упоминаний «fix round». `_Walker` в порядке, но `_collect_paths` несёт find-специфику, которая должна быть в таблице команд. |
| `scripts/export_openapi.py` | 886 | ≈600 строк прозы в строковых константах и `_paths()`, собранный руками. `contracts/README.md` признаёт, что генератор до сих пор помечает реализованные эндпоинты как `provisional`. |

Judo-ход для `hard_deny.py`: одно правило — один модуль в `rules/hard_deny/`, история ревью переезжает в `docs/reports/task-5-hard-deny.md` (она там уже есть), в коде остаётся инвариант в одну-две фразы. Для `export_openapi.py`: response-модели на все маршруты + `description=` на полях, затем `app.openapi()` даёт документ, а скрипт становится ≈60 строками (шаг 6, нужен PR с упоминанием трёх направлений по правилу `contracts/README.md`).

### F15. Два composition root

`__main__.build_app()` собирает engine, репозитории, store, gate, app. `cli._build_repo()` собирает engine и репозиторий заново. Тесты собирают третий вариант в `test_api.build()`.

Judo-ход: `bootstrap.py` с одной функцией сборки, которую используют `__main__`, `cli` и тестовые фабрики.

### F16. Тесты импортируют фикстуры друг из друга

`from tests.test_pipeline import FakeLLM, profile`, `from tests.test_stage2_prompt import P, WS`, `from tests.test_stage1_chain import P, WS`. Переименование одного тестового модуля ломает три других.

Judo-ход: `tests/factories.py` (профили, запросы, фейковый классификатор, фейковый writer). Делается на шаге 0, потому что все последующие шаги через него проходят.

### L1–L3. Мелкие логические замечания

- **L1.** Эскалация лезет во внутренности состояния из пайплайна: `state.deny_consecutive = 0; state.recent.clear()` (`pipeline.py:146-147`). Должно быть `SessionState.reset_after_escalation()`.
- **L2.** Идемпотентности нет (gap-analysis): повторный `decide` дважды сдвигает счётчики. В `Gate` нужен слот под ключ идемпотентности; `Verdict` + `DecisionWriter` делают его добавление локальным.
- **L3.** `_finish_early` для unknown profile всё равно нормализует запрос, чтобы было что записать. Не баг, но показывает, что запись сцеплена с нормализацией. С `Verdict` запись принимает `action | None`.

---

## 5. Целевая архитектура v1.5

Поведение то же, что в v1. Меняется форма.

```
service/agentgate/
  domain/                 # чистые типы, без I/O
    action.py             #   NormalizedAction, SimpleCommand, Flags   (из normalize/model.py)
    verdict.py            #   Verdict — единственный тип решения
    policy.py             #   Profile (конфиг, хэш при загрузке) + Policy (профиль ⊗ workspace)
    session.py            #   SessionState + EscalationPolicy
  shell/                  # синтаксис и семантика shell, без политики
    parse.py              #   bashlex-walker                         (из normalize/shell.py)
    wrappers.py           #   resolve_effective_argv + таблицы wrapper'ов, публично
    argv.py               #   ParsedArgv: exe / positionals / options
    commands.py           #   CommandSpec — одна таблица знаний о командах
    paths.py, domains.py
  normalize/              # DecideRequest → NormalizedAction, по одному нормализатору на Tool
  rules/                  # ступень 1
    base.py               #   Rule (Protocol), RuleChain
    hard_deny/            #   exfil.py, pipe_exec.py, destructive.py, protected_write.py,
                          #   privilege.py, git_force.py
    unparseable.py, wrapper_unresolved.py, profile_paths.py, profile_domains.py,
    allowlist.py, packages.py
  classify/               # ступень 2
    base.py               #   Classifier (Protocol)
    llm.py                #   LLMClassifier = prompt + OpenAI-клиент + schema
    prompt.py, schema.py, openai_client.py
  engine/
    gate.py               #   Gate: только оркестрация, ~70 строк
    timings.py
  session/
    base.py               #   SessionStateStore (Protocol)
    memory.py             #   InMemorySessionStateStore
    persistent.py         #   память + write-through в SessionRepo + restore()
  store/
    models.py, repo.py, keys.py
    writer.py             #   DecisionWriter (Protocol), Postgres/Jsonl/Composite
  api/
    app.py, auth.py, schemas.py   # + DecisionsPage, ProfilePublic, Health
  bootstrap.py            # единственный composition root
  cli.py, __main__.py
```

### Ключевые типы

```python
# domain/verdict.py
@dataclass(frozen=True)
class Verdict:
    decision: DecisionKind
    stage: int                        # 0 api/cache, 1 rules, 2 classifier
    rule_id: str | None = None
    reason: str = ""
    suggest: str = ""
    hard: bool = False                # выставляют только hard-deny правила
    model: str | None = None
    raw_response: dict | None = None
    error: str | None = None

    @classmethod
    def ask(cls, rule_id: str, reason: str, stage: int = 0) -> "Verdict": ...
    def escalated(self, hits: int) -> "Verdict":        # -> ask, rule_id="escalation"
        ...
```

```python
# rules/base.py
class Rule(Protocol):
    id: str                           # "hard-deny.exfil", "profile.path", "allowlist.readonly"
    hard: bool
    def evaluate(self, action: NormalizedAction, policy: Policy) -> Verdict | None: ...

class RuleChain:
    def __init__(self, rules: Sequence[Rule]) -> None: ...
    def evaluate(self, action, policy) -> Verdict | None:
        for rule in self._rules:                       # единственный цикл по правилам
            if (v := rule.evaluate(action, policy)) is not None:
                return v
        return None

STAGE1 = RuleChain([
    UnparseableRule(),
    ExfilRule(), PipeExecRule(), DestructiveRule(), ProtectedWriteRule(), PrivilegeRule(), GitForceRule(),
    WrapperUnresolvedRule(),
    ProfilePathRule(), ProfileDomainRule(),
    AllowlistRule(),
    PackagesRule(),                                    # слот slopsquatting, как в спеке
])
```

```python
# domain/policy.py
class Profile(BaseModel):             # как сейчас, без поля workspace; hash считается один раз в loader
    ...

@dataclass(frozen=True)
class Policy:                         # то, что видят правила и промпт
    profile: Profile
    workspace: str
    allowed_paths: tuple[str, ...]    # разрешены один раз
    protected_paths: tuple[str, ...]

    @classmethod
    def bind(cls, profile: Profile, workspace: str) -> "Policy": ...
```

```python
# classify/base.py
class Classifier(Protocol):
    name: str
    async def classify(self, action: NormalizedAction, user_request: str,
                       policy: Policy, stage1_note: str) -> Verdict: ...
```

```python
# store/writer.py
class DecisionWriter(Protocol):
    async def write(self, decision: Decision) -> None: ...   # никогда не бросает

class PostgresDecisionWriter: ...     # session upsert -> decision insert -> allow_cache; порядок FK живёт только здесь
class JsonlDecisionWriter: ...
class CompositeWriter: ...            # каждый writer глотает и логирует свою ошибку
```

```python
# engine/gate.py
class Gate:
    def __init__(self, profiles: Mapping[str, Profile], classifiers: Mapping[str, Classifier],
                 rules: RuleChain, sessions: SessionStateStore, cache: AllowCache) -> None: ...

    async def decide(self, req: DecideRequest) -> Decision:
        t = Timings()
        ctx = await self._resolve(req)                          # Policy, SessionState, Classifier — или ранний Verdict
        if isinstance(ctx, Verdict):
            return self._finish(req, ctx, t)
        action = normalize(req)
        if (hit := await self._cache.lookup(ctx, action, req)) is not None:
            return self._finish(req, hit, t, action, ctx)
        with t.stage(1):
            verdict = self._rules.evaluate(action, ctx.policy)
        if verdict is None:
            with t.stage(2):
                verdict = await ctx.classifier.classify(action, req.user_request, ctx.policy, STAGE1_PASSED)
        verdict = await self._settle_session(ctx, verdict, action)   # эскалация, счётчики, cache put
        return self._finish(req, verdict, t, action, ctx)
```

`Decision` — иммутабельный результат (`verdict`, `request`, `action | None`, `timings`, `decision_id`, `policy`, `state | None`). `DecideResponse.from_decision()` живёт в `api/`, `DecisionRecord.from_decision()` в `store/`. `Gate` не знает ни о HTTP, ни о таблицах.

### Где здесь SOLID, если спросят

- **S**: `Gate` только оркестрирует; правило знает одно правило; writer знает одно хранилище.
- **O**: новое правило, новый классификатор, новый writer — новый класс в списке, `Gate` не меняется. Слот `PackagesRule` и роадмап-правила Test → Protect встают в ту же цепочку.
- **L**: фейки в тестах реализуют те же протоколы, что прод.
- **I**: `Rule`, `Classifier`, `DecisionWriter`, `SessionStateStore` — по одному-двум методам.
- **D**: всё собирается в `bootstrap.py`; ядро зависит от протоколов, не от Postgres/httpx.

---

## 6. План переезда: шесть шагов, каждый зелёный

Ветка `refactor/solid-v1.5`, один PR на шаг, только явные пути в `git add`. Порядок выбран по соотношению «видно судьям / риск»: сначала оркестратор и правила, затем спековое исправление F4, затем глубокий DRY в ядре безопасности с корпусом эквивалентности.

| Шаг | Что делаем | Что удаляется | Закрывает | Риск | Страховка |
|---|---|---|---|---|---|
| **0** | `Verdict`, `Timings`, `Decision`; `Gate.decide()` разбивается на `_resolve / _finish / _settle_session`; `DecisionWriter` (Postgres + JSONL + Composite); `cache_key` явным полем; `tests/factories.py`; лог в двух молчаливых `except` (гайд 7.2); `tests/engine/`, `tests/store/` зеркально | `Gate.persist`, `_do_persist`, `_record()`, `_finish_early`, `_ask()` в app.py, `_CACHE_TTL_SECONDS`, `Stage2Result` | F1, F2, F16, L1, L3, G1 | низкий | `test_pipeline`, `test_api` (адаптируются под `Decision`/writer-фейк), остальные без изменений |
| **1** | `Rule` + `RuleChain`; `hard_deny.py` → `rules/hard_deny/*.py`; `WrapperUnresolvedRule`, `UnparseableRule`; история ревью из комментариев → `docs/reports/task-5-hard-deny.md` (гайд 5.3); `tests/rules/hard_deny/` зеркально (гайд 6.3) | `run_stage1`, `check_hard_deny`, `_deny/_ask`, `Stage1Decision`, `types.Check`, `_NOTE_SKIPPED` | F5, F6, F14 (hard_deny) | низкий, механический | 546 табличных тестов hard-deny + 214 chain: меняются только импорты и расположение. F6 — согласовать `stage` |
| **2** | `shell/wrappers.py` публично; `ParsedArgv`; один `SECRET_PATTERNS`; `NormalizedAction`/`SimpleCommand`/`Flags` frozen, `_Walker` собирает их один раз в конце (гайд 3.5); `tests/shell/` зеркально | приватный кросс-импорт, четыре while-цикла, `_SENSITIVE_BASENAMES`, мутация `action.*` после создания | F8, F9 (секреты), F11, G2 | средний: трогает exfil | временный тест эквивалентности old-vs-new на всех табличных входах + корпус из `logs/decisions.jsonl`; удаляется после шага |
| **3** | `Profile` / `Policy`; workspace привязывается к сессии; хэш при загрузке; `resolved_*` один раз | `with_workspace`, `Profile.workspace`, `Profile._expand` на каждый вызов | **F4** | средний: меняет семантику по спеке | регрессионный тест — таблица из F4; `test_profiles`, `test_stage1_*` |
| **4** | `Classifier` + реестр; `PersistentSessionStateStore`; `bootstrap.py` принимает store, часы и writer параметрами; `DecisionRecord` как pydantic; типизированные deps; response-модели `DecisionsPage`, `Health`, `ProfilePublic`; `FakeClassifier`/`FakeClock` вместо `MockTransport` и `monkeypatch.setattr` (гайд 6.1) | склейка в `app.py`/`__main__.py`, `cli._build_repo`, `to_row/from_row/to_dict`, `dict(…, decision_id=…)` ×2, три `monkeypatch.setattr` в тестах | F3, F7, F12, F13, F15, G3 | низкий | `test_main`, `test_api`, `test_store`, `test_cli_keys` |
| **5** | `CommandSpec` таблица; `_collect_paths`, `MUTATING`, `READONLY`, `NETWORK_COMMANDS`… становятся запросами к ней | 11 множеств, `_cmd_paths`, `command_argv_paths`, find-спецслучаи в `_collect_paths` | F9, F10 | средне-высокий | тот же корпус эквивалентности, что на шаге 2; `test_normalize_shell` + все stage-1 таблицы |
| **6** | OpenAPI из `app.openapi()` + описания на моделях | ≈800 строк `export_openapi.py`, отметки `provisional` | F14 (openapi) | низкий технически; процессно — PR с тремя направлениями | `test_contracts` |

Шаги 0–1 можно сделать за один рабочий день и уже показывать. Шаг 3 обязателен до любого внешнего запуска: это единственная находка с последствиями для безопасности. Шаг 5 — единственный, который стоит отложить, если сроки поджимают: он даёт самый чистый код, но требует корпуса эквивалентности.

Правило для каждого шага: **ни одно табличное ожидание в `test_stage1_hard_deny.py`, `test_stage1_chain.py`, `test_normalize_shell.py` не меняется.** Если шаг требует поменять ожидание, это не рефакторинг, а изменение поведения, и оно идёт отдельным PR со своей строкой в спеке.

---

## 7. Как v1.5 масштабируется на v2–v4 и контракт адаптера

| Что приходит | Куда встаёт в v1.5 | Что не меняется |
|---|---|---|
| **v2** история диалога | `DecideRequest.history`; `PromptBuilder` в `classify/` рендерит её между `[TASK]` и `[ACTION]` через `_j()`; дайджест истории входит в ключ через один `AllowCache.key_for()` (спека v2 требует «тем же коммитом») | `RuleChain` не видит историю: ступень 1 по-прежнему только по `NormalizedAction` |
| **v3** оценка tool-result | новый эндпоинт `/v1/observe` → `ObserveRequest`; `Verdict` получает вариант `mark` (taint) или отдельный `Mark`; `SessionState.taint` + `ProvenanceRule` в цепочке | `Gate` не меняется: `Observe` — второй оркестратор поверх тех же протоколов |
| **v4** Context Guard | `ContextGuard` (Protocol) в `classify/`, применяется `PromptBuilder` к каждому `toolresult` до рендера | закрытый список содержимого промпта остаётся закрытым |
| Контракт адаптера | `protocol: 1` — поле на `DecideRequest`/`DecideResponse`; `author: human/agent/system` — поле в `history`; идемпотентность — `DecisionKey` в `Gate._resolve` + уникальный индекс в `DecisionWriter` | `Verdict` один, `reason` без механики правил — это правка `Rule.description`, а не оркестратора |
| Несколько инстансов | `RedisSessionStateStore(SessionStateStore)` — один класс, подключается в `bootstrap.py` | ничего |
| Локальная модель / другой провайдер | `Classifier` — второй класс или тот же `LLMClassifier` с другим `base_url` | ничего |

---

## 8. Открытые вопросы владельцу продукта

1. **F6, `stage` для unparseable.** Сделать `UnparseableRule` ступенью 1 (`stage: 1`, `model: null`) вместо текущего `stage: 2`? Контрактно видно, спека §5.1 требует правки в одну строку.
2. **F4, workspace на сессию.** Подтвердить, что §6 спеки («из первого запроса сессии») — намерение. Для харнесса, который меняет `cwd` между вызовами, это изменение поведения: workspace фиксируется первым запросом.
3. **Шаг 6** трогает `contracts/openapi.yaml` и по правилу `contracts/README.md` требует PR с упоминанием трёх направлений.
4. ~~История ревью в комментариях: переносить или оставить?~~ **Закрыт стайл-гайдом, п. 5.3** («не ссылайся в комментариях на задачи/PR/авторов/даты»): переносим в `docs/reports/`, в коде остаётся инвариант.

---

## 9. Что показывать судьям после шагов 0–4

- **Один экран**: `Gate.decide()` читается как диаграмма из спеки §5 — resolve → normalize → cache → rules → classifier → session.
- **Список правил как код**: `STAGE1 = RuleChain([...])` в одном месте — слот `packages` и место для Test → Protect видны как строки списка. Эндпоинт `GET /v1/rules` поверх него — опция для демо, не часть рефакторинга.
- **Один тип `Verdict`** от правила до ответа API и строки в Postgres.
- **Четыре протокола** (`Rule`, `Classifier`, `DecisionWriter`, `SessionStateStore`) и `bootstrap.py`, где всё собирается. Redis, другой LLM, другое хранилище — по одному классу.
- **Fail-closed как свойство типов**: `Classifier.classify` возвращает `Verdict`, а не бросает; `DecisionWriter.write` не бросает; `allow` по ошибке невыразим.
- После шага 5 — **таблица `CommandSpec`**: «добавить команду = одна строка».

---

## 10. Сверка со стайл-гайдом владельца

Гайд (SOLID через Protocol, value objects, неизменяемость по умолчанию, комментарии только для «почему», fakes вместо monkeypatch, зеркальные тесты) в основном подтверждает план и меняет его в четырёх местах.

### Что гайд подтверждает

| Пункт гайда | Что в коде | Находка |
|---|---|---|
| 1.3, 3.4, 3.5 — знание в одном месте, value objects, frozen | четыре типа одного решения; `_record()` с 13 параметрами; `Gate.__init__` 7, `create_app` 8, `_finish_early` 8 — сигнал «params ≥ 4» из раздела 8 гайда | F1, F5, F8 → `Verdict`, `Policy`, `ParsedArgv`, `Decision` как frozen dataclass |
| 2.1, 2.2 — Protocol в сигнатурах, малые интерфейсы | `create_app(decision_repo, session_repo, key_repo=None)` без типов; `Gate` строит `LLMClient` сам | F3, F7, F12 → `Rule`, `Classifier`, `DecisionWriter`, `SessionStateStore` по одному-двум методам |
| 2.3 — одна причина для изменений | `Gate.decide()` меняется при любой правке кэша, эскалации, записи, промпта | F1, F2 |
| 3.1, 3.3 — один уровень абстракции, 5–15 строк | 22 функции длиннее 30 строк (приложение Б); `_rule_git_force` 73, `_command` 76, `decide` 83, `create_app` 87 | F1, F8, F14 |
| 5.1, 5.3 — без комментариев по умолчанию, **без ссылок на задачи/PR/даты** | 45 упоминаний «fix round», 22 «Important N» | F14; вопрос 4 закрыт |
| 5.4 — docstring описывает контракт, не реализацию | docstrings `hard_deny.py`, `shell.py`, `allowlist.py` пересказывают, как правило чинилось | F14 |
| 7.3 — явные типы на границах | `normalized: dict`, `rules: list[dict[str, Any]]`, нетипизированные репозитории | F12, F13 |

### Что гайд меняет в плане

1. **1.2, минимум необходимого.** `GET /v1/rules` и `RuleChain.describe()` убраны из объёма рефакторинга: это фича «на будущее». Раздел 5 и 9 поправлены. Появится, когда потребуется панели.
2. **6.3, зеркальная структура.** `tests/` плоский: `tests/test_stage1_hard_deny.py` при `agentgate/stage1/hard_deny.py`. Каждый шаг переносит тесты затронутых модулей в зеркальные каталоги (`tests/rules/hard_deny/test_exfil.py`, `tests/engine/test_gate.py`, `tests/shell/test_wrappers.py`). Табличные ожидания не меняются, меняется только расположение. Внесено в таблицу шагов.
3. **6.1, fakes вместо monkeypatch.** Три теста подменяют зависимости по имени атрибута: `test_main.py:77` подменяет `InMemorySessionStateStore`, `test_cli_keys.py:176-179` подменяет `build_app`, `uvicorn.run` и `sys.argv`, `test_session.py:61-74` подменяет `time.monotonic`. Это сигнал из раздела 8 гайда: зависимость прибита вместо инъекции. На шаге 4 `bootstrap.py` принимает store, часы и writer параметрами, а `FakeClassifier` заменяет `httpx.MockTransport` в тестах пайплайна. `monkeypatch.setenv` для `Settings` и `interpolate_env` остаётся: окружение — граница системы (7.1), а не зависимость.
4. **3.5, неизменяемость.** `NormalizedAction`, `SimpleCommand`, `Flags` — обычные dataclass, которые мутируются после создания (`action.flags.has_unresolved_expansion = True`, `action.paths = paths`), и `action_hash()` считается с мутабельного объекта. На шаге 2 они становятся frozen, `_Walker` копит и собирает результат один раз в конце. `SessionState` остаётся мутабельным осознанно (счётчики), но только через свои методы (L1).

### Что гайд добавляет как находки

- **G1. 7.2, не глуши исключения.** Два `except Exception` без логирования: `normalize/shell.py:483` превращает любую ошибку walker'а в `unparseable`, `stage2/run.py:63` возвращает `ask` с `error="unexpected"`. Fail-closed сохраняется, но баг в собственном коде неотличим от мусорного ввода. `log.warning(..., exc_info=True)` в оба места, шаг 0, по одной строке.
- **G2. 4.2, без аббревиатур.** `sf`, `srepo`, `drepo`, `rec`, `resp`, `effs`, `ea`, `pid`, `cfg`, `tok`, `s1`, `s2`, `t0`–`t2`: 23 присваивания в `hard_deny.py`, 11 в `pipeline.py`. Не отдельный шаг: имена чинятся в том модуле, который шаг и так переписывает.
- **G3. 6.5, один тест — одно утверждение.** `test_unknown_profile_and_model_are_ask` (два факта), `test_token_required_when_set` (шесть проверок), `test_file_tools` (пять) проверяют по несколько фактов. По 1.2 массово не переписываем: разделяются те тесты, которые шаг и так переносит.
- **2.4, открытость/закрытость.** `normalize()` — цепочка `if req.tool is Tool.shell … elif …` на четыре инструмента. Пока `Tool` — закрытый enum из пяти значений, по 1.2 это терпимо. Контракт адаптера делает `tool` свободной строкой (`mcp__server__tool`), и тогда это реестр `{Tool: Normalizer}`. Отмечено, заранее не делаем.
- **6.4** — `ids=` в `parametrize` не используется, идентификаторы генерируются из значений (ASCII-команды). Кириллица в тестах только в данных (`test_log.py`, `test_store.py`), не в id. Нарушений нет.

### Что гайд не меняет

- Шаг 5 (`CommandSpec`) — не преждевременная абстракция по 1.2: одиннадцать множеств — это одиннадцатое повторение, а не первое.
- `Classifier` с единственной реализацией оправдан 2.1 (инъекция) и 6.1 (фейк в тестах), а не повторением.
- Раздел 9 гайда про горячий путь: замер сделан (0.21 мс из 1 мс), ни одно решение плана не мотивировано скоростью.

---

## Приложение А. Измерения

| Что | Значение |
|---|---|
| Тесты | 466 passed, 43 skipped (без Postgres), 2.5 с |
| Код без тестов | 5129 строк, из них `export_openapi.py` 886 |
| `hard_deny.py` | 922 строки: 434 кода, 404 комментариев/docstring (43%), 84 пустых |
| «fix round» в коде | hard_deny 26, shell 12, allowlist 3, paths 2, argv_paths 1, model 1 |
| p50 workspace + profile_hash | 0.019 мс |
| p50 normalize | 0.087 мс |
| p50 stage 1 | 0.107 мс |
| p50 всего (бюджет 1 мс) | 0.206 мс |
| `resolved_allowed_paths()` за серый запрос | 4 вызова; `resolved_protected_paths()` — 2 |
| `startswith("-")` в `hard_deny.py` | 18 |

## Приложение Б. Функции длиннее 30 строк или с ≥ 5 параметрами

Ориентир гайда — 5–15 строк на функцию (3.3) и ≤ 3 параметров (раздел 8). Полный список из 31 позиции получен обходом AST; ниже верхняя часть.

| Строк | Параметров | Функция |
|---|---|---|
| 87 | 8 | `api/app.py:create_app` |
| 83 | 2 | `pipeline.py:decide` |
| 76 | 3 | `normalize/shell.py:_command` |
| 73 | 2 | `stage1/hard_deny.py:_rule_git_force` |
| 58 | 2 | `stage1/hard_deny.py:_rule_destructive` |
| 54 | 3 | `stage2/client.py:classify` |
| 49 | 2 | `normalize/shell.py:resolve_effective_argv` |
| 48 | 3 | `normalize/shell.py:walk` |
| 46 | 2 | `stage1/hard_deny.py:_sent_secret_paths` |
| 44 | 3 | `stage1/hard_deny.py:_excluded_read_paths` |
| 43 | 1 | `stage1/hard_deny.py:_consumes_piped_stdin` |
| 39 | 4 | `api/deps.py:make_require_token` |
| 38 | 6 | `stage2/run.py:run_stage2` |
| 15 | 13 | `pipeline.py:_record` |
| 15 | 7 | `pipeline.py:Gate.__init__` |
| 13 | 8 | `pipeline.py:_finish_early` |
