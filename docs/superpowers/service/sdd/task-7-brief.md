### Task 7: `CommandSpec` — одна таблица знаний о командах

Закрывает: F9 (три ответа на «какие пути трогает команда»), F10 (одиннадцать множеств в пяти файлах).

Самый рискованный шаг: он трогает ядро безопасности целиком. Идёт последним из технических и опирается на корпус эквивалентности из задачи 4. **Если сроки поджимают — этот шаг откладывается**, остальные семь самодостаточны.

**Files:**
- Create: `service/agentgate/shell/commands.py`, `service/tests/shell/test_commands.py`
- Modify: `service/agentgate/normalize/shell.py` (`PATH_COMMANDS`, `_collect_paths`), `service/agentgate/rules/hard_deny/shared.py`, `service/agentgate/rules/allowlist.py`, `service/agentgate/rules/profile_paths.py`, `service/agentgate/rules/argv_paths.py`
- Delete: `service/agentgate/rules/argv_paths.py` (растворяется в таблице)

**Interfaces:**
- Produces: `agentgate.shell.commands.CommandSpec` — frozen dataclass: `name: str`, `roles: frozenset[Role]`, `value_flags: frozenset[str]`, `upload_flags: frozenset[str]`, `write_target: WriteTarget`, `path_arguments: PathArguments`.
- Produces: `Role` (Enum): `READONLY`, `MUTATING`, `NETWORK`, `DOWNLOADER`, `INTERPRETER`, `SHELL`, `FIREWALL`, `WRAPPER`, `WRITE`.
- Produces: `COMMANDS: Mapping[str, CommandSpec]` и `spec_for(executable: str) -> CommandSpec` (для неизвестной команды — нейтральная спецификация, а не `KeyError`).
- Produces: `commands_with_role(role: Role) -> frozenset[str]` — через неё выражаются нынешние одиннадцать множеств.

- [ ] **Step 1: Свести существующие множества в таблицу — без изменения поведения**

Порядок: сначала таблица описывает ровно то, что уже есть, и старые множества **выводятся** из неё, а не удаляются. Так корпус эквивалентности обязан оставаться зелёным на каждом шаге.

`service/agentgate/shell/commands.py` — таблица со строкой на команду:

```python
COMMANDS: dict[str, CommandSpec] = {
    "curl": CommandSpec(
        name="curl",
        roles=frozenset({Role.NETWORK, Role.DOWNLOADER}),
        value_flags=frozenset({"-o", "--output", "-T", "--upload-file", ...}),
        upload_flags=frozenset({"-d", "--data", "--data-binary", "-T", "--upload-file", ...}),
        write_target=WriteTarget.NONE,
        path_arguments=PathArguments.FLAG_VALUES,
    ),
    "rm": CommandSpec(
        name="rm", roles=frozenset({Role.MUTATING}), value_flags=frozenset(),
        upload_flags=frozenset(), write_target=WriteTarget.EVERY_POSITIONAL,
        path_arguments=PathArguments.EVERY_POSITIONAL,
    ),
    "cp": CommandSpec(..., write_target=WriteTarget.LAST_POSITIONAL, ...),
    "tee": CommandSpec(..., write_target=WriteTarget.EVERY_POSITIONAL, ...),
    "sed": CommandSpec(..., write_target=WriteTarget.POSITIONALS_AFTER_FIRST, ...),
    ...
}
```

и производные:

```python
def commands_with_role(role: Role) -> frozenset[str]:
    return frozenset(name for name, spec in COMMANDS.items() if role in spec.roles)
```

Затем в старых местах заменить литералы на запросы:
- `hard_deny/shared.py`: `NETWORK_COMMANDS = commands_with_role(Role.NETWORK)`, `DOWNLOADERS = commands_with_role(Role.DOWNLOADER)`, `SHELLS`, `INTERPRETERS`, `WRITE_COMMANDS`, `FIREWALL` — тем же способом.
- `rules/allowlist.py`: `READONLY = commands_with_role(Role.READONLY)`.
- `rules/profile_paths.py`: `MUTATING = commands_with_role(Role.MUTATING)`.
- `normalize/shell.py`: `PATH_COMMANDS = commands_with_role(...)` по соответствующей роли.

**Тест на равенство старому составу** — написать до замены, пока оба существуют:

```python
@pytest.mark.parametrize("role,legacy", [
    (Role.NETWORK, LEGACY_NETWORK_COMMANDS),
    (Role.READONLY, LEGACY_READONLY),
    (Role.MUTATING, LEGACY_MUTATING),
    ...
], ids=["network", "readonly", "mutating", ...])
def test_table_reproduces_the_legacy_set(role, legacy):
    assert commands_with_role(role) == frozenset(legacy)
```

`LEGACY_*` — копии нынешних множеств, вписанные в тест литералами. Тест удаляется вместе с последним старым множеством; до тех пор он доказывает, что таблица ничего не потеряла.

Run: `cd service && uv run pytest tests/shell/test_commands.py tests/equivalence -q`

- [ ] **Step 2: Один ответ на «какие пути трогает команда»**

Свести три функции в одну, опирающуюся на таблицу:

```python
def command_paths(command: SimpleCommand, cwd: str, role: PathRole) -> tuple[str, ...]:
    """Paths this command references in the given role.

    READ, WRITE and ANY are different questions, and the three functions
    that used to answer them disagreed -- which was already a bug once
    (a readonly command outside PATH_COMMANDS reading a bare-name
    protected file was invisible to the allowlist guard).
    """
```

Заменять по одному вызывающему, прогоняя корпус после каждого:
1. `rules/argv_paths.command_argv_paths` → `command_paths(..., PathRole.ANY)`; модуль удаляется.
2. `hard_deny/shared.command_paths` (нынешний `_cmd_paths`) → та же функция с `PathRole.ANY`, плюс редиректы и stdin, которые теперь описаны в таблице как часть роли.
3. `normalize/shell._collect_paths` → таблица; find-специфика (`-delete`, `-exec`, narrowing predicates) переезжает в `CommandSpec` для `find`.

Run после каждого: `cd service && uv run pytest tests/equivalence tests/rules tests/normalize -q`
Expected: зелено. Красный корпус — откатить конкретную замену.

- [ ] **Step 3: Тест «добавить команду — одна строка»**

```python
def test_a_new_command_is_one_row():
    spec = CommandSpec(
        name="shred", roles=frozenset({Role.MUTATING}), value_flags=frozenset(),
        upload_flags=frozenset(), write_target=WriteTarget.EVERY_POSITIONAL,
        path_arguments=PathArguments.EVERY_POSITIONAL,
    )
    assert spec.name in {**COMMANDS, spec.name: spec}
    assert Role.MUTATING in spec_for("shred").roles


def test_an_unknown_command_gets_a_neutral_spec():
    spec = spec_for("some-tool-we-have-never-seen")
    assert spec.roles == frozenset()
    assert spec.write_target is WriteTarget.NONE
```

Второй тест важнее первого: неизвестная команда не должна ни падать, ни получать привилегий.

- [ ] **Step 4: Прогон, удаление корпуса, коммит**

Run:
```bash
cd service && AGENTGATE_TEST_DB_URL=postgresql+asyncpg://agentgate:agentgate@localhost:5433/agentgate_test uv run pytest
```
Expected: всё зелёное.

Корпус эквивалентности выполнил свою работу и удаляется — он фиксирует реализацию, а не поведение, и оставлять его значит заморозить внутренности (гайд 6.2):

```bash
cd service && git rm -r tests/equivalence
```

```bash
git add service/agentgate/shell/commands.py service/agentgate/normalize service/agentgate/rules service/tests/shell
git rm service/agentgate/rules/argv_paths.py
git rm -r service/tests/equivalence
git commit -m "refactor(service): one CommandSpec table instead of eleven command sets

PATH_COMMANDS, MUTATING, WRITE_COMMANDS, READONLY, GIT_READONLY,
NETWORK_COMMANDS, DOWNLOADERS, INTERPRETERS, SHELLS, FIREWALL and the
wrapper set become queries against one table, and the three disagreeing
answers to 'which paths does this command touch' become one. Adding a
command is a row.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

