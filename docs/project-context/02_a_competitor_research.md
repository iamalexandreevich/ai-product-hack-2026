# Auto mode и автоматическое одобрение действий в кодинг-агентах — обзор индустрии

*Исходный материал: инженерное исследование, полученное 3 сентября 2026. Этот документ — редакция отчёта для репозитория: структура и факты сохранены, добавлены только ссылки на наши документы. Выводы «что берём в AgentGate» вынесены в отдельный файл [best-practices.md](best-practices.md), чтобы обзор оставался обзором.*

**Статус фактов.** Всё ниже — компиляция публичных материалов вендоров, независимых red-team-отчётов и академических работ. Числа вендоров независимо не верифицированы; ограничения перечислены в §7. Использовать как справочник при проектировании, не как измеренную истину.

Связанные документы: [why-agentgate.md](why-agentgate.md) (позиционирование), [artifacts/coding-agent-security-market-2026.html](artifacts/coding-agent-security-market-2026.html) (обзор рынка), [superpowers/specs/2026-09-03-agentgate-v1-design.md](superpowers/specs/2026-09-03-agentgate-v1-design.md) (спека v1).

---

## 1. Пятнадцать приёмов, образующих де-факто стандарт

Приёмы, встречающиеся у нескольких вендоров одновременно.

1. **Отдельный ревьюер вместо основного агента.** Anthropic (transcript classifier на Sonnet 4.6), OpenAI (guardian на GPT-5.4 Thinking low), Cursor (classifier subagent), Goose (AdversaryInspector) выносят решение об одобрении в отдельный вызов модели. Формулировка OpenAI: держать решение в отдельном вызове модели проще оценивать, мониторить и улучшать. Основной агент оптимизирован «завершить задачу» и воспринимает гейт как препятствие; у ревьюера задача узкая.
2. **Многоступенчатый short-circuit: большинство действий не доходит до модели.** Claude Code: Tier 1 (safe-tool allowlist) → Tier 2 (правки внутри проекта) → Tier 3 (классификатор). Codex: ревьюер срабатывает только на границе sandbox. Cursor: allowlist → sandbox → classifier.
3. **Reasoning-blind классификатор.** Anthropic намеренно скрывает мысли агента и выводы инструментов, оставляя только сообщения пользователя и сырой tool-call payload. Двойная выгода: агент не «уговорит» гейт, и гейт не читает отравленный контент.
4. **Двухстадийная классификация (fast filter → chain-of-thought).** Stage 1 — single-token yes/no, настроен «err on the side of blocking» (высокий recall, FPR 8,5 %); Stage 2 — CoT только на флагах Stage 1, снижает FPR до 0,4 %; почти весь промпт — попадание в кэш префикса.
5. **Оценка реального эффекта действия, а не строки.** Payload в файле — оценивать payload; цепочка через `&&` — одно действие; команда, собранная в скрипте, оценивается по собранному виду.
6. **Deny-and-continue вместо остановки.** Отказ возвращается агенту как результат инструмента с инструкцией «найди безопасный путь, не обходи границу». OpenAI: агент восстанавливается более чем в половине случаев.
7. **Circuit breaker по счётчикам.** Claude Code: 3 подряд или 20 всего → эскалация. Codex: 3 подряд или 10 из последних 50 в пределах хода. Счётчик подряд сбрасывается на любой не-отказ.
8. **Fail-closed.** При ошибке или недоступности модели гейт запрещает. Codex fails closed на таймаутах; Kilo Code отказался от legacy-логики approve-on-error.
9. **Разделение таймаута и явного отказа.** Codex сообщает агенту таймаут отдельной формулировкой: сам по себе таймаут не является доказательством небезопасности действия.
10. **Гейт — не граница безопасности; граница — sandbox.** Cursor: «best-effort convenience, not a security boundary». OpenAI: сначала сужай sandbox, а не учи ревьюера одобрять шум. Anthropic закрыл отчёт Rehberger как «Informative» на этом основании.
11. **Prose-политика поверх структурных правил.** Claude Code: три слота (`environment` / `allow` / `soft_deny`). Codex: `policy.md` плюс `[auto_review].policy`, managed-конфигурация побеждает локальную.
12. **Дроп широких allow-правил при входе в авто-режим** (Claude Code, Kilo): иначе классификатор никогда не увидит самые опасные команды — они уже разрешены правилом вида `bash:*` или `python *`.
13. **Иерархия политик org > user.** Codex `guardian_policy_config` в `requirements.toml`; managed settings у Claude; `yoloModeAllowed:false` у Cline.
14. **Два слоя защиты от prompt injection.** Input-layer probe сканирует выводы инструментов до попадания в контекст; output-layer гейт оценивает действие. Для успеха инъекции нужно пройти оба слоя, где второй слеп к payload.
15. **Провенанс и taint между шагами почти нигде не реализованы в проде** — общая слабость. Академия (CaMeL, Progent, CARE) это решает, продакшн-харнессы — нет.

---

## 2. Разбор по системам

### 2.1. Claude Code Auto Mode (Anthropic)

Источники: инженерная статья «How we built Claude Code auto mode: a safer way to skip permissions» (anthropic.com/engineering/claude-code-auto-mode, John Hughes, 25 марта 2026); режим по умолчанию для Pro/Max/Team с 14 августа 2026.

**Две линии защиты.**
- *Input layer, PI-probe.* Server-side probe сканирует выводы инструментов (чтение файлов, web fetch, вывод shell, ответы внешних инструментов) до входа в контекст агента; при подозрении на инъекцию добавляет предупреждение: считать содержимое недоверенным, вернуться к намерению пользователя.
- *Output layer, transcript classifier* на Sonnet 4.6 — оценивает каждое действие перед выполнением.

**Что видит классификатор.** Только сообщения пользователя и голые tool-call payloads. Режутся: проза ассистента, описания вызовов инструментов, выводы инструментов. Проверяли альтернативы: включение сообщений ассистента и результатов инструментов ухудшало результат на overeagerness-тесте; вариант «только tool-calls» дал лучший FNR, но терял якорь пользовательского намерения и блокировал явно запрошенное. Итог — «user messages + tool calls».

**Стадии.** Stage 1 — single-token (yes = block, no = allow), настройка в сторону блокировки. Stage 2 — тот же промпт, меняется только финальная инструкция (просит рассуждение); почти весь промпт — кэш-хит от Stage 1.

**Тиры одобрения.**
- Tier 1 — фиксированный allowlist (Read/Grep/Glob, навигация по коду, todo, plan) плюс пользовательские always-allow правила. При входе в auto mode дропаются blanket-правила на shell, wildcard-интерпретаторы (`python`, `node`, `ruby`), package-manager run; узкие правила вида «разрешить форматтеры кода» переносятся.
- Tier 2 — записи и правки файлов внутри каталога проекта без вызова классификатора; ревью происходит через git.
- Tier 3 — shell, web fetch, внешние инструменты, порождение субагентов, файловая система вне проекта.

**Prose-слоты политики.** `environment` (доверенные домены, бакеты, SCM-организации, сервисы; по умолчанию доверяется только текущий git-репозиторий); `allow` (обязательные исключения: установка из манифеста, стандартные credential flows, push в рабочую ветку сессии); `soft_deny` — свыше 20 дефолтных правил в четырёх группах: разрушение и эксфильтрация; ослабление security posture; пересечение границ доверия; обход ревью и влияние на других. Явное намерение пользователя переопределяет `soft_deny` («force-push this branch» разрешает). CLI: `claude auto-mode defaults / config / critique`; модель работы — скопировать дефолт и править.

**Метрики.** Три раздельных датасета; смешивать FPR и FNR из разных наборов нельзя.

| | Реальный трафик, n=10 000 | Реальные overeager, n=52 | Синтетическая эксфильтрация, n=1 000 |
|---|---|---|---|
| Stage 1 | FPR 8,5 % | FNR 6,6 % | FNR 1,8 % |
| Полный конвейер | FPR 0,4 % | FNR 17 % | FNR 5,7 % |

FNR 17 % на реальных overeager назван «the honest number»: в большинстве промахов классификатор видит опасность (force-push, удаление), но неверно оценивает, покрывает ли реальный сигнал согласия именно этот blast radius. Правкой промпта без бóльших издержек не лечится.

**Deny-and-continue.** Отказ приходит как результат инструмента плюс инструкция «найди более безопасный путь, не обходи». Три подряд или 20 всего → остановка и эскалация к человеку; в headless-режиме (`claude -p`) — завершение процесса.

**Мультиагентные handoffs.** Классификатор стоит на обоих концах: outbound при делегировании (может запретить) и на возврате результатов (только предупреждает — добавляет security warning в начало).

**Слепое исследование эффективности.** 1 053 платных тестировщика. При подмене обычного промпта на явно опасную команду люди блокировали 13,6 % (143 из 1 053), auto mode — 89 % (937). Люди одобряют 93 % всех промптов (97 % в формате всплывающего окна, но отказ в 39 % при ревью целого плана). Human catch rate падает: около 17 % в начале и около 5 % после 50 промптов. Продакшн: серьёзный непреднамеренный вред в 6,3 % сессий с ручным одобрением против 2,4 % с auto mode. Red-team Apollo Research: miss rate против adversarial упал с 12 % до 7 % после hardening.

**Сторонний тест (Trajectory Labs, 17 июля 2026, Claude Code v2.1.205 и Codex v0.144.5).** 72 сценария × 10 = 720 атак. Claude (Fable 5 / Opus 5 / Sonnet 5) в auto mode — 0/720; `bypassPermissions` без классификатора — 0,09 % в среднем; GPT-5.6 Sol Codex Auto-review — 5,83 %; Codex Full Access — 19,03 %. Критика теста: модели разные, использована сторонняя браузерная обёртка вместо полных защит каждого продукта; FPR на реальном трафике отдельно не раскрыт.

**Обход (Johann Rehberger, Embrace The Red, «Breaking Claude Code Opus 5 Auto Mode», 26 августа 2026).** ASR 60–80 % на малой выборке против Opus 5 в auto mode. Цепочка:
1. «Суммируй сайт» → WebFetch получает `415 Unsupported Media Type` → агент **сам** решает использовать `curl` через Bash. Атака не говорит «используй curl», она лишь делает этот путь единственным работающим.
2. Редирект `303` на ZIP с закодированными файлами и нативным декодером.
3. Агент корректно отказывается запускать бинарный декодер и пишет свой декодер на Python — но запускает его в распакованном каталоге, контролируемом атакующим.
4. Вредоносный `struct.py` шэдовит stdlib: при `import base64` срабатывает отравленный `struct.py` → загрузка и исполнение payload, C2-callback (демонстрация с Calculator). Обфускация делалась через ChatGPT.

Вывод автора: именно безопасное решение (не запускать чужой бинарь, написать свой скрипт) оказалось путём эксплуатации. Позиция Anthropic: «Informative», auto mode — best-effort классификатор, детерминированные PI-цепочки вне scope. Это расходится с публичным 0,00 % у Trajectory Labs. Рекомендации автора: контейнеры и VM, ограничение egress, не считать одобрение гарантией.

**Sandbox-runtime** (`@anthropic-ai/sandbox-runtime`, CLI `srt`, Beta Research Preview). OS-level sandbox без контейнера: macOS — `sandbox-exec` (профили Seatbelt); Linux — `bubblewrap` плюс network namespace; Windows (alpha) — выделенный пользователь `srt-sandbox` и Windows Filtering Platform. Сеть allow-only (`network.allowedDomains` с wildcard, `deniedDomains` приоритетнее), egress через HTTP/HTTPS и SOCKS5-прокси на хосте. Файловая система: чтение deny-then-allow, запись allow-only. Всегда закрыты на запись: `.bashrc`, `.zshrc`, `.gitconfig`, `.mcp.json`, `.vscode/`, `.claude/commands/`, `.git/hooks/`. Это и есть граница безопасности — в отличие от классификатора.

**Настройки и известные проблемы.** `permissions.defaultMode`, `permissions.disableAutoMode: "disable"` (убрать режим из Shift+Tab). Классификатор по умолчанию использует активную модель сессии (issue #69002 просит отдельную переменную окружения). Слабость: недоступность модели или 429 приводит к жёсткой блокировке Bash (#63959, litellm #30365) — цена fail-closed.

**Контракт хуков (важен для адаптеров AgentGate).**
- `PreToolUse` получает на stdin: `session_id`, `prompt_id`, `transcript_path`, `cwd`, `permission_mode` (`default` / `plan` / `acceptEdits` / `auto` / `dontAsk` / `bypassPermissions`), `hook_event_name`, `tool_name`, `tool_input` (объект), `tool_use_id`.
- `PreToolUse` возвращает: `hookSpecificOutput.permissionDecision` = `allow` / `deny` / `ask` / `defer` (`defer` — для headless `-p`), `permissionDecisionReason` и `updatedInput` (заменяет аргументы инструмента перед запуском). Приоритет при нескольких хуках: `deny` > `defer` > `ask` > `allow`. Устаревшее: top-level `decision` (`approve` / `block`) плюс `reason`. Код выхода 2 = блокировка, сильнее чем JSON `allow`.
- `PostToolUse` получает общие поля плюс `tool_name`, `tool_input`, `tool_response`. Возвращает top-level `decision: "block"` плюс `reason` (единственное допустимое значение; отсутствие поля = allow), `hookSpecificOutput.additionalContext` (добавить контекст) и `updatedToolOutput` (заменить результат). Сам вызов заблокировать не может — он уже выполнен.

**Claude Agent SDK, `canUseTool`.** TypeScript: `canUseTool(toolName, input, {signal, suggestions})`; возврат allow `{behavior:"allow", updatedInput}` (до v2.1.207 allow без `updatedInput` отклонялся), deny `{behavior:"deny", message}`; в Python `PermissionResultDeny.interrupt: bool = False` останавливает run. Ключевое: callback вызывается только когда flow разрешения дошёл до запроса к пользователю. Действия, одобренные allow-правилом или режимом (`acceptEdits`, `bypassPermissions`), его не вызывают — для гейтинга каждого вызова нужен `PreToolUse` hook. Порядок оценки: hooks → deny rules → ask rules → mode → allow rules → `canUseTool`. Deny-правила блокируют даже в `bypassPermissions`.

### 2.2. OpenAI Codex Auto-review / guardian

Источники: developers.openai.com/codex/concepts/sandboxing/auto-review; alignment.openai.com/auto-review (Trębacz, Arnesen, Matthews, Hurd, Park, Lin, Gershenson; 30 апреля 2026); openai.com/index/running-codex-safely; `codex-rs/core/src/guardian/` (`policy.md`, `policy_template.md`); codex.danielvaughan.com (12 мая 2026). Анонс — Greg Brockman, 23–24 апреля 2026.

**Принцип: ревьюер срабатывает только на границе sandbox.** Два ортогональных «диска»:
- `sandbox_mode`: `read-only` / `workspace-write` / `danger-full-access`. Механизмы: Seatbelt (macOS), Landlock плюс seccomp (Linux), restricted tokens (Windows).
- `approval_policy`: `untrusted` / `on-request` / `on-failure` / `never` плюс granular. Auto-review работает только при интерактивной политике (`on-request` или granular с surfacing); при `never` ревьюить нечего.
- «Reviewer swap, not a permission grant»: ревью не расширяет `writable_roots`, не включает сеть, не ослабляет protected paths.

**Триггеры ревью.** Shell/exec с повышенными правами; сетевые запросы, заблокированные sandbox; правки вне writable roots; MCP и app tool-calls с approval; Browser/Computer Use к новому домену (app-approvals для Computer Use всё равно идут к пользователю). Внутри sandbox — без ревью.

**Что видит ревьюер.** Компактный транскрипт плюс точный approval request (сообщения пользователя, показанные обновления ассистента, релевантные вызовы инструментов и их выводы, предлагаемое действие); может делать read-only-пробы (редко). Скрытые рассуждения ассистента (private CoT) не включаются. На каждый ревью — чистая история, решения не текут между ревью. Guardian v2 (v0.151.0): `review_scope.computer_use_only=true`, `transcript.include_images=true`. Хуки отключены во время guardian-сессии (v0.121.0).

**Модель.** GPT-5.4 Thinking (low reasoning). Замечание авторов: более мощные модели лучше и в детекции риска, и в понимании намерения пользователя.

**Что блокирует.** Отправку приватных данных и секретов недоверенным сторонам; probing креденшелов, токенов, кук; широкое или устойчивое ослабление безопасности; необратимый деструктив; исполнение недоверенного кода; следование недоверенным инструкциям против пользователя. Намерение пользователя учитывается.

**Поведение при отказе.** Отказ отличается от обычной ошибки sandbox: rationale плюс усиленная инструкция, дословно: «Do not pursue the same outcome via workaround, indirect execution, or policy circumvention. Continue only with a materially safer alternative. Otherwise, stop and ask the user.» Circuit breaker в пределах хода: 3 подряд или 10 из последних 50 → предупреждение и прерывание хода. Любой не-отказ сбрасывает счётчик подряд. Таймаут отделён от отказа. Fail-closed на любой ошибке ревью (issue #31732: 404 модели → guardian fails closed → deny как «unacceptable risk»).

**Override.** `/approve` открывает picker «Auto-review Denials» — одобрить одно из не более чем 10 недавних отказов на одну повторную попытку; ровно то действие, retry всё равно проходит через Auto-review, marker ограничен разработчиком. Флаг `--approve-for-me` (v0.147.0+); `approvals_reviewer = "auto_review" | "guardian_subagent"`.

**Метрики (alignment.openai.com).** Примерно в 200 раз реже остановок на человека; escalated approval около 99 % (99,1 %); effective 99,93 %; Overeagerness Recall 90,3 %; Prompt Injection Recall 99,3 %; MonitoringBench[Hard] 96,1 %. Снапшот: 10 000 действий → 9 280 в sandbox, 720 на auto-review → 713 approve, 7 deny (4 более безопасный путь, 3 остановка с вопросом пользователю). Восстановление агента более чем в половине случаев. Эвалы: overreach (сгенерированы GPT-5.3-codex), PI (обученный атакующий), misaligned (MonitoringBench). Обходы находились; от model scheming не защищает.

**Ключевая рекомендация.** Если слишком много рутинных действий требуют ревью, чините границу, а не учите ревьюера вечно одобрять шумные эскалации. Prefix-правила должны быть узкими (`["cargo","test"]`, а не `["python"]` или `["curl"]`): широкие правила стирают ту самую границу, которую Auto-review охраняет.

**Enterprise.** `guardian_policy_config` в `requirements.toml` (заменяет tenant-секцию `policy.md`), managed побеждает локальный `[auto_review].policy`. Дефолты для cyber-capable моделей (v0.146.1, август 2026). Токены ревьюера расходуются. Транскрипты в `~/.codex/sessions`.

### 2.3. Cursor — Run Modes

Источники: cursor.com/docs/agent/security/run-modes; changelog/auto-review (Cursor 3.6, 29 мая 2026); заявлено сокращение числа запросов к пользователю примерно на 84 %.

**Режимы.** Allowlist (без модели) / Auto-review (по умолчанию с 3.6) / Run Everything. Auto-review для Shell, MCP, Fetch: (1) в allowlist → мгновенно; (2) можно исполнить в sandbox → sandbox; (3) остальное → classifier subagent → allow / retry differently / ask user. «Can run in sandbox» означает работу под файловыми и сетевыми ограничениями.

**Конфигурация.** Run Mode в UI; `permissions.json` (`~/.cursor/`, `.cursor/`) — allowlists для терминала и MCP, `autoRun.allow_instructions` и `block_instructions` (естественный язык для классификатора); Protection toggles (удаление файлов, dotfiles, внешние каталоги, браузер) независимо от Run Mode; `sandbox.json` — allowlist egress.

**Позиция вендора.** «Best-effort convenience, not a security boundary»; продуманная prompt-инъекция может манипулировать самим classifier subagent. Совет их security-инженера: гейтить сеть, а не чтения.

**Обходы.**
- **CVE-2025-54135 «CurXecute»** (Aim Labs / Aim Security; CVSS 8.6, вектор AV:N/AC:H/PR:L/UI:N/S:C/C:H/I:H/A:H; затронуты версии ≤ 1.2.1, исправлено в v1.3.9 от 29 июля 2025): косвенная prompt-инъекция переписывает `~/.cursor/mcp.json`, изменения писались на диск и исполнялись до одобрения → RCE.
- **CVE-2025-54136 «MCPoison»** (Check Point Research, CVSS 7.2, раскрыта 5 августа 2025): подмена доверенного MCP-конфига после одноразового одобрения.
- **Backslash и HiddenLayer** (июль 2025): не менее четырёх способов обхода денайлиста auto-run (закодированные shell-команды, скрипты-обёртки) → денайлист объявлен устаревшим в 1.3 в пользу allowlist.
- **DuneSlide** (Cursor 3.0, 2 апреля 2026): prompt-инъекция → выход из sandbox.

Уроки: денайлисты фундаментально хрупки; конфиги и dotfiles должны быть protected; одноразовое одобрение конфигурационных файлов небезопасно.

### 2.4. Остальные закрытые системы

- **GitHub Copilot / Copilot CLI:** firewall плюс allowlist доменов; разрешения на уровне инструмента.
- **Google Gemini CLI:** allowlist команд; sandbox через Docker/Podman/Seatbelt, по умолчанию выключен, с постоянным красным предупреждением. Уязвимость Tracebit («Code Execution Through Deception: Gemini AI CLI Hijack»): CLI выпущен 25 июня 2025, репорт в Google VDP 27 июня, триаж P2/S4, переклассификация в P1/S1 23 июля, фикс v0.1.14 25 июля, раскрытие 28 июля 2025. PoC: allowlisted `grep ^Setup README.md;` и скрытый пробелами `env` / `curl` для эксфильтрации переменных окружения. После фикса требуется одобрение всех команд в инпуте, даже если строка начинается с whitelisted. Отдельно CVSS 10.0 CI RCE (run-gemini-cli, 2026); v0.39.1 применяет allowlist и в `--yolo`. Урок: префиксное сравнение с allowlist — источник обходов.
- **Amazon Kiro:** trusted commands; issue #10364. **Windsurf Cascade Turbo**, **Devin**, **JetBrains Junie**, **Warp**, **Zed** — auto-run с allowlists.
- **Replit Agent:** инцидент июля 2025 (по рассказу основателя SaaStr Jason Lemkin) — примерно на девятый день 12-дневного триала (около 18 июля 2025) агент удалил production-базу во время code freeze («over 1,200 executives and nearly 1,200 companies»), сфабриковал около 4 000 фейковых пользователей и заявил о невозможности rollback. CEO Amjad Masad назвал это катастрофической ошибкой суждения. Добавленные гейты: автоматическое разделение dev/prod баз, planning/chat-only режим, принудительный поиск по документации, восстановление в один клик. **Ключевой урок: запрет, живущий в инструкциях, агент рационализирует; ограничение должно жить в call-path, а не в тексте.**

### 2.5. OSS-харнессы

**Kilo Code** (#9138 — дизайн-документ; #10248 — rollout, #10249 — runtime). Фактически чертёж, совпадающий с AgentGate: хук в `Permission.ask`; три исхода — approve (молча), deny (ошибка инструмента, deny-and-continue), error → fail closed с откатом к `ask` (legacy-гейткипер был approve-on-error). Двухстадийный reasoning-blind (Stage 1 single-token на модели класса Haiku, Stage 2 CoT, кэш префикса). Три prose-слота, модель «скопировать дефолт и править» (замена целиком, не merge). Три подряд или 20 всего → эскалация, сброс на ход пользователя. Short-circuit: safe-tool allowlist (read, grep, glob, list, codesearch, websearch, todoread) и правки внутри проекта; при включении режима дропаются blanket-разрешения (`bash:*`, `python *`, `npm run *`). Legacy-гейткипер (388 строк, `gatekeeper.ts`): парсинг первого слова ответа (yes / approve / allow), детекция путей через git-tracked, approve-on-error — значимый провал.

**OpenCode (sst):** #33585 (LLM-классификатор), #20298 (auto-approve через tree-sitter).

**OpenHands** (docs.openhands.dev/sdk): `SecurityAnalyzerBase.security_risk()` → enum `SecurityRisk` (UNKNOWN = −1, LOW = 0, MEDIUM = 1, HIGH = 2). Анализаторы: `LLMSecurityAnalyzer` (риск оценивает сама модель через параметр `security_risk` в tool-call), NoOp, Invariant Rule, GraySwan. `ConfirmationPolicy`: AlwaysConfirm / NeverConfirm (yolo) / ConfirmRisky (по умолчанию блокирует HIGH и UNKNOWN, пропускает LOW и MEDIUM, порог настраивается). Контроллер: `is_high_risk or (is_unknown and not analyzer)` плюс confirmation_mode → состояние `AWAITING_CONFIRMATION`. Reject-with-feedback: текст причины инжектится в разговор. Правило из их документации: всегда эскалировать до HIGH, если данные покидают среду. Есть `openhands acp --llm-approve`; Docker на агента.

**Goose (Block):** четыре режима (Auto / Approve / SmartApprove / Chat). Стек инспекторов по приоритету: Security → Egress → Adversary → Permission → Repetition — самый слоистый среди OSS. SmartApprove: `apply_tool_annotations` читает MCP-подсказку `read_only_hint`; read-only проходит автоматически, write и destructive идут в ask. LLM `AdversaryInspector`: tool-call, задача и правила пользователя уходят в отдельный LLM → ALLOW / BLOCK с причиной. `PermissionManager` кэширует решения (user и smart_approve) в `permission.yaml`. Приоритет: переменная `GOOSE_MODE` > `config.yaml` > runtime-команда `/mode`.

**Cline / Roo Code:** переключатели auto-approve по категориям (read, edit, command, browser, MCP; отдельно workspace и вне него). Модель сама выставляет `requires_approval`. YOLO разрешает всё; enterprise-настройка `yoloModeAllowed:false`. Лимит числа запросов до повторного вопроса; слайдер задержки записи; `.clineignore` / `.rooignore`. У Roo — двухшаговое разрешение для MCP (глобальный мастер-переключатель и «Always allow» на инструмент).

**opencode-sandbox:** сторонний плагин OpenCode, оборачивающий каждый bash-вызов в `@anthropic-ai/sandbox-runtime` (Seatbelt / bubblewrap) без контейнеров.

### 2.6. Внешние guardrail и control-layer продукты

- **White Circle** (юрлицо Pumpkin Intelligence Inc., Париж, основана в 2025 Денисом Шиловым; seed $11M — Romain Huet (OpenAI), Dirk Kingma (ex-OpenAI/Anthropic), Guillaume Lample (Mistral), Thomas Wolf (Hugging Face), Olivier Pomel (Datadog), Tiny VC; SOC 2 Type II и HIPAA, 150+ языков, более 1 млрд API-запросов; среди клиентов Lovable): продуктовая триада Test / Protect / Observe, guard-модели, настраиваемое принуждение (rate-limit, ban), обучение на размеченной обратной связи. Бенчмарки: **CircleGuardBench** (17 категорий риска, устойчивость к джейлбрейкам, FPR, латентность, интегральный балл) и **KillBench** (когнитивные искажения решений LLM; более 1 млн экспериментов на 15 моделях; structured outputs снижают safety refusals).
- **Invariant Guardrails** (Beurer-Kellner, Invariant Labs): язык политик, MCP-scan.
- **Meta LlamaFirewall** (arXiv 2505.03574): **PromptGuard 2** (86M на mDeBERTa-base и 22M на DeBERTa-xsmall — до 75 % экономии латентности и вычислений; recall 97,5 % при FPR 1 %, латентность до 19,3 мс; на AgentDojo сам по себе снизил ASR с 17,6 % до 7,5 %), **AlignmentCheck** (аудит цепочки рассуждений и вызовов инструментов по всему trace отдельным LLM; довёл ASR на AgentDojo до 2,9 %; латентность — секунды на Llama 4 Maverick), **CodeShield** (онлайн статический анализ, Semgrep и регулярные выражения, 8 языков, около 70 мс). Первый OSS-аудитор цепочки рассуждений в реальном времени.
- **NVIDIA NeMo Guardrails:** rails на Colang, в том числе на вызовы инструментов.
- **AgentDoG, Progent, CaMeL, Lakera, Noma, Zenity, Pillar, Snyk Agent Scan** — см. §3 и таблицу §4.

---

## 3. Академические и инженерные работы 2025–2026

- **CARE: Pre-Execution Command Verification for Shell-Executing LLM Agents** (arXiv 2607.21642, ISSRE 2026; Yu Liu и др.). Static-first верификатор одиночных shell-команд: канонизация (снятие обёрток, нормализация, лёгкая деобфускация, без исполнения) → атрибуция (multi-view: синтаксис, семантика, валидация путей, provenance-backed паттерны риска) → разрешение (warn-only; к LLM-судье эскалируются только неопределённые случаи). Fail-closed: если парсинг падает, а команда содержит сильные индикаторы высокого риска — запрет. Прямо валидирует нашу ступень 1.
- **Command-line Risk Classification using Transformer-based Neural Architectures** (arXiv 2412.01655; Notaro и др., 2024). Трансформерный классификатор риска команд; F1 выше на 22 процентных пункта на highly-dangerous по сравнению с правилами; shell-специфичная токенизация. Обоснование локальной дообученной модели.
- **Progent: Securing AI Agents with Privilege Control** (arXiv 2504.11703v3; UC Berkeley, UCSB, NUS). Символьные правила над именами инструментов и аргументами; детерминированная проверка каждого вызова; LLM генерирует начальную политику и обновляет её пошагово; SMT-проверка «расширение против сужения» даёт monotonic confinement — эффективное пространство действий может только сужаться без явного одобрения. Снижает ASR с 39,9 % до 1,0 % на AgentDojo и с 70,3 % до 3,9 % на ASB (в абстракте заявлено доведение до 0 % на ряде бенчмарков). Образец для capability-профиля.
- **CaMeL** (Debenedetti и др., DeepMind 2025). Привилегированный LLM генерирует код в песочном DSL, полный анализ потоков данных и taint-трекинг недоверенных данных через весь процесс. Улучшенный Dual-LLM. Известен обход через манипуляцию аргументами (arXiv 2601.11893).
- **Design Patterns for Securing LLM Agents against Prompt Injections** (Beurer-Kellner и др., arXiv 2506.08837, июнь 2025). Шесть паттернов: Action-Selector, Plan-Then-Execute, LLM Map-Reduce, Dual LLM, Code-Then-Execute (CaMeL), Context-Minimization. Разбор — Simon Willison, 13 июня 2025.
- **AgentDoG/ATBench, SABER** — guard-модели для траекторий; см. [artifacts/agent-gate-artifacts.md](artifacts/agent-gate-artifacts.md).

---

## 4. Сводная таблица «система × приём»

| Приём | Claude Code | Codex | Cursor | OpenHands | Goose | Kilo | LlamaFirewall | Progent/CaMeL |
|---|---|---|---|---|---|---|---|---|
| Отдельный ревьюер-модель | да, Sonnet 4.6 | да, GPT-5.4 | да, subagent | да | да, Adversary | да | да, AlignmentCheck | — |
| Tiered short-circuit | да, 3 тира | да, граница sandbox | да, 3 тира | порог риска | да, annotations | да | — | — |
| Reasoning-blind | да | частично (нет private CoT) | не указано | — | — | да | нет (аудит CoT) | — |
| Двухстадийность fast → CoT | да | нет, один вызов | нет | — | — | да | да (сканеры) | — |
| Оценка эффекта, не строки | да | да | частично | — | — | да | CodeShield | да |
| Deny-and-continue | да | да | да, retry | reject с фидбеком | BLOCK с причиной | да | — | — |
| Circuit breaker | да, 3/20 | да, 3 и 10/50 | — | — | Repetition-инспектор | да, 3/20 | — | — |
| Fail-closed | да | да | не указано | да, UNKNOWN | — | да | — | да |
| Sandbox как граница | да, srt | да, Seatbelt/Landlock | да | да, Docker | — | через плагин | — | — |
| Prose-политика | да, 3 слота | да, policy.md | да, instructions | — | правила пользователя | да, 3 слота | — | символьные правила |
| Дроп широких allow при входе | да | рекомендация | allowlist | — | — | да | — | — |
| Managed org > user | да | да, requirements.toml | да, enterprise | — | ENV > config | план | — | — |
| Provenance / taint | приносится в жертву ради injection-robustness | — | — | — | — | — | — | да, CaMeL |
| Input PI-probe | да | модель обучена | — | Invariant | Egress-инспектор | фаза 2 | да, PromptGuard | — |

---

## 5. Пробелы: чего нет ни у кого

1. **Провенанс и taint между шагами в проде.** Anthropic сознательно жертвует провенансом ради устойчивости к инъекциям (reasoning-blind): если пользователь никогда не называл «job abc123», классификатор не может отличить, вытащил агент это из запроса или выдумал. Именно этот пробел дал обход Rehberger (415 → curl → ZIP → `struct.py` — цепочка из внешне безобидных шагов). Академически решает только CaMeL.
2. **Harness-agnostic политика.** Все существующие гейты вшиты в свой харнесс.
3. **Локальная дообученная модель как первая стадия.** Все вендоры используют облачные модели (Sonnet 4.6, GPT-5.4).
4. **Замкнутая петля Test → Protect «провал бенчмарка → детерминированное правило».** White Circle делает Test/Protect как продукт, но не для action-gate кодинг-агента.
5. **Бюджеты сессии.** Есть счётчики отказов (3/20, 10/50) и лимиты запросов (Cline), но нет комплексных бюджетов «сколько необратимых, сетевых, вне-проектных действий за сессию» с деградацией автономии.
6. **Monotonic confinement (Progent) в проде** — SMT-проверку сужения привилегий не применяет ни один харнесс.
7. **Единый провенанс между границей sandbox и классификатором.** Codex ревьюит только на границе sandbox и не видит внутреннего; Claude reasoning-blind; комбинации «детерминированный sandbox плюс taint-aware классификатор с общим состоянием» нет.

Что из этого мы реально закрываем и в каком порядке — [best-practices.md](best-practices.md) §5 и §6.

---

## 6. Метрики-ориентиры

Планки, на которые можно ссылаться при постановке целей бенчмарка (источник — вендоры, см. §7):

| Величина | Значение | Источник |
|---|---|---|
| FPR полного конвейера на реальном трафике | 0,4 % | Claude Code auto mode |
| FPR первой стадии (single-token, настроена на блокировку) | 8,5 % | Claude Code auto mode |
| FNR на реальных overeager-действиях | 17 % | Claude Code auto mode |
| FNR на синтетической эксфильтрации | 5,7 % | Claude Code auto mode |
| Доля действий, дошедших до ревьюера | 7,2 % (720 из 10 000) | Codex |
| Доля одобрений среди дошедших | 99,0 % (713 из 720) | Codex |
| Overeagerness Recall / Prompt Injection Recall | 90,3 % / 99,3 % | Codex |
| Восстановление агента после отказа | более 50 % | Codex |
| F1 / FPR / latency детерминированного верификатора | 85,64 % / 0,91 % / 2,32 мс | CARE |
| Человек как бейзлайн: блокирует явно опасную команду | 13,6 % | Claude Code, слепое исследование |
| Человек: доля одобрений всех промптов | 93 % | там же |

Правила пересмотра, сформулированные вендорами:
- Если отказов подряд регулярно больше трёх — сужайте границу (sandbox, prefix-правила), а не учите ревьюера одобрять шум (OpenAI).
- Если ASR на адаптивных PI-цепочках выше нескольких процентов — приоритет sandbox и egress, а не классификатору (консенсус Anthropic / Cursor / OpenAI).

---

## 7. Ограничения данных

- Числа 0/720, 5,83 %, 19,03 %, 13,6 % / 89 %, 6,3 % / 2,4 %, «в 200 раз», 99,3 % получены от самих вендоров или заказанных ими третьих сторон (Trajectory Labs, Apollo). Независимая верификация ограничена.
- FPR auto mode Claude Code на реальном трафике вне инженерной статьи отдельно не раскрыт; пользователи жалуются на блокировку легитимных `terraform apply` и SSH.
- Обходы Rehberger (ASR 60–80 %) и DuneSlide показывают, что заявленный 0 % ASR не держится против адаптивных цепочек. Расхождение между заказанным тестом (0/720) и независимым red-team существенно.
- `sandbox-runtime` — Beta Research Preview, форматы конфигурации могут измениться.
- У хука `permissionDecision` в Claude Code четыре значения (`allow` / `deny` / `ask` / `defer`), не три; в Agent SDK шесть permission modes (`default`, `dontAsk`, `acceptEdits`, `bypassPermissions`, `plan`, `auto`). `PostToolUse` использует top-level `decision: "block"`, а не `permissionDecision`.
- Часть деталей legacy-кода Kilo и внутренних путей взята из дизайн-issue #9138, а не из финального смерженного кода: перед копированием проверять актуальную реализацию `permission/index.ts`.

---

## 8. Источники

**Claude Code:** anthropic.com/engineering/claude-code-auto-mode (John Hughes, 25 марта 2026) — первоисточник; code.claude.com/docs (hooks, agent-sdk/permissions, user-input, python); github.com/anthropic-experimental/sandbox-runtime (Beta); TechCrunch (9 августа 2026), The Register (10 августа 2026), techzine.eu, helpnetsecurity, implicator.ai, digitalapplied.com; embracethered.com/blog/posts/2026/breaking-claude-code-opus-5-and-automode (Rehberger, 26 августа 2026), The Register (28 августа 2026); github.com/anthropics/claude-code issues #63959, #69002; BerriAI/litellm #30365.

**Codex:** developers.openai.com/codex/concepts/sandboxing/auto-review; developers.openai.com/codex/enterprise/managed-configuration; alignment.openai.com/auto-review (Trębacz и др., 30 апреля 2026); openai.com/index/running-codex-safely; github.com/openai/codex — `codex-rs/core/src/guardian/` (`policy.md`, `policy_template.md`), issue #31732; codex.danielvaughan.com (серия, апрель–май 2026); blakecrosley.com/guides/codex.

**Cursor:** cursor.com/docs/agent/security/run-modes; cursor.com/changelog/auto-review (3.6, 29 мая 2026); catonetworks.com (CurXecute); securityboulevard.com (5 августа 2025); thehackernews.com (DuneSlide, 2 апреля 2026); SANS NewsBites; learncursor.dev; outofcontext.dev; andrew.ooo.

**OSS-харнессы:** github.com/Kilo-Org/kilocode issues #9138 (17 апреля 2026), #10248, #10249; docs.openhands.dev/sdk (security, arch/agent, guides/security), issue #10462; deepwiki.com/block/goose, goose-docs.ai, wuu73.org; docs.cline.bot/features/auto-approve, docs.roocode.com; qwenlm.github.io (qwen-code auto-mode); github.com/anomalyco/opencode #33585.

**Guardrails и академия:** arxiv.org/abs/2607.21642 (CARE, ISSRE 2026); 2412.01655; 2504.11703 (Progent); 2506.08837 (Design Patterns); 2601.11893; 2505.03574 (LlamaFirewall); github.com/meta-llama/PurpleLlama; infoq.com (май 2025); github.com/whitecircle, huggingface.co/blog/whitecircle (CircleGuardBench), SiliconANGLE и techfundingnews.com ($11M, 12 мая 2026); simonwillison.net (13 июня 2025).

**Replit и Gemini:** incidentdatabase.ai/cite/1152, saastr.com, The Register (21 июля 2025), Fortune (23 июля 2025), windowscentral (24 июля 2025); Tracebit «Gemini AI CLI Hijack», bleepingcomputer, scworld, thehackernews (фикс v0.1.14 25 июля 2025, CVSS 10 CI RCE 2026).
