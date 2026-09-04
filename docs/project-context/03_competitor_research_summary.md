# Competitive Research Summary

> Status: Intermediate research summary
> Sources: `02_a_competitor_research.md`, `02_b_competitor_research.md`
>
> Обозначения: **[F]** — факт, напрямую следующий из одного из двух исследований; **[I]** — интерпретация (вывод из нескольких фактов); **[H]** — гипотеза, требующая проверки.
> Источник A = инженерный обзор auto-mode/гейтов; источник B = рыночный обзор конкурентов. При расхождениях оба варианта показаны явно (§ 12 и врезки).

---

## 1. Executive Overview

**[F]** Поле уже занято: контроль действий автономных агентов реализован во всех крупных кодинг-харнессах (Claude Code auto mode, OpenAI Codex Auto-review/guardian, Cursor Run Modes, GitHub Copilot Agents), в OSS-харнессах (Kilo Code, OpenHands, Goose, Cline/Roo, OpenCode) и в отдельных guardrail-продуктах (LlamaFirewall, Invariant, NeMo Guardrails, White Circle, Lasso Intent Deputy, Adversa AI, Arcade.dev).

**[I]** Доминируют три класса решений: (1) sandbox/isolation как настоящая граница безопасности; (2) tiered short-circuit + allowlist без модели; (3) отдельный ревьюер-модель на границе, оценивающий tool-call перед исполнением. Комбинация «allowlist → sandbox → LLM-классификатор → эскалация» повторяется у Anthropic, OpenAI, Cursor и Kilo практически один в один — это уже де-факто стандарт, а не дифференциатор.

**[I]** Насыщенные зоны: LLM-классификация действия перед исполнением, deny-and-continue, circuit breaker по счётчикам отказов, prose-политики, sandbox-профили, аудит-логи. Заявлять новизну в них нельзя.

**[I]** Менее закрытые зоны (§ 8): provenance/taint между шагами в проде; harness-agnostic политика (все проверенные гейты вшиты в свой харнесс — исключение guardrail-слои вроде Adversa/Arcade, но они не про shell кодинг-агента); локальная/дешёвая первая ступень; замкнутая петля benchmark → детерминированное правило; бюджеты автономии на сессию; monotonic confinement (Progent) в продакшне.

**[F]** Ключевая внешняя рамка: вендоры сами говорят, что гейт — «best-effort convenience, not a security boundary» (Cursor), а Anthropic закрыла обход Rehberger как «Informative» на том же основании. **[I]** Значит, продукт-гейт конкурирует не с sandbox, а с трением ручного подтверждения.

---

## 2. Problem Landscape

**[F]** Рынок формулирует проблему не как «детектировать атаку», а как **«убрать человека из цикла подтверждений, не получив катастрофу»**:

- **Approval fatigue как измеренная величина.** Люди одобряют 93 % всех промптов; блокируют явно опасную подменённую команду лишь в 13,6 % случаев; catch rate падает с ~17 % в начале сессии до ~5 % после 50 промптов (A, слепое исследование Anthropic, n=1 053). Auto mode в том же тесте блокировал 89 %.
- **Автономия как продуктовая цель.** Codex: ревьюер уменьшает остановки на человека примерно в 200 раз; 7,2 % действий доходят до ревью, из них 99 % одобряются (A). Cursor заявляет сокращение запросов к пользователю примерно на 84 % (A).
- **Prompt injection как основной вектор.** Косвенная инъекция через выводы инструментов (веб, файлы, репозитории) — сквозная тема обоих исследований; B отдельно выделяет «guardrails уязвимы к инъекции, потому что живут в контексте агента».
- **Необратимый ущерб как якорный кейс.** Инцидент Replit Agent (удаление production-базы во время code freeze, фабрикация данных, июль 2025) — канонический пример; вывод источника A: **запрет, живущий в инструкциях, агент рационализирует; ограничение должно жить в call-path**.
- **Supply chain / slopsquatting** (B): агент устанавливает галлюцинированный или тайпсквоттнутый пакет — отдельный класс проблемы, который командные гейты почти не покрывают.

---

## 3. Existing Solution Categories

### 3.1 Sandboxing / isolation

**How it works.** OS-level или контейнерная изоляция файловой системы и сети: Seatbelt (macOS), Landlock+seccomp / bubblewrap + network namespace (Linux), restricted tokens / WFP (Windows), Docker/контейнеры (OpenHands, Copilot Cloud Agent). Egress — allow-only по доменам через прокси.
**Strengths.** Единственный слой, который вендоры называют настоящей границей безопасности; детерминированен; не обманывается инъекцией.
**Limitations.** Не различает намерение; ломает легитимные сценарии, требующие сети/выхода за workspace; у Gemini CLI по умолчанию выключен; известны выходы из sandbox (DuneSlide, Cursor 3.0).
**Representative solutions.** `@anthropic-ai/sandbox-runtime` (Beta), Codex `sandbox_mode`, Cursor sandbox + `sandbox.json`, OpenHands Docker, Copilot Cloud Agent ephemeral env, opencode-sandbox.
**Evidence confidence.** High (обе работы, деталь — в A).

### 3.2 Static permissions / allowlists (без модели)

**How it works.** Фиксированный список безопасных инструментов и префиксов команд; всё остальное эскалируется.
**Strengths.** Нулевая латентность и стоимость; предсказуемость; закрывает подавляющее большинство трафика.
**Limitations.** **[F]** Денайлисты фундаментально хрупки (Backslash/HiddenLayer: ≥4 обхода auto-run денайлиста Cursor → денайлист объявлен устаревшим). Префиксное сравнение — источник обходов (Gemini CLI: `grep ^Setup README.md;` + скрытый `env`/`curl`). Широкие правила (`bash:*`, `python *`) стирают границу — поэтому при входе в авто-режим их дропают (Claude Code, Kilo).
**Representative solutions.** Claude Code Tier 1, Cursor `permissions.json`, Gemini CLI, Copilot CLI, Kiro trusted commands, Cline/Roo категории auto-approve.
**Evidence confidence.** High.

### 3.3 Отдельный ревьюер-модель (tool-call interception + model-based risk evaluation)

**How it works.** Перед исполнением tool-call уходит отдельным вызовом модели, возвращающей allow/block(+reason). Часто reasoning-blind: рассуждения агента и выводы инструментов не показываются ревьюеру.
**Strengths.** Понимает намерение пользователя; отдельно оценивается и улучшается; агент не «уговаривает» гейт; двухстадийность (single-token → CoT) даёт дешёвый recall и низкий FPR.
**Limitations.** Вероятностный; FNR 17 % на реальных overeager-действиях у Claude Code — «the honest number»; стоит токенов и латентности; сам может быть целью инъекции (Cursor); недоступность модели ведёт к жёсткой блокировке (цена fail-closed).
**Representative solutions.** Claude Code transcript classifier (Sonnet 4.6), Codex guardian (GPT-5.4 Thinking low), Cursor classifier subagent, Kilo двухстадийный гейт, Goose AdversaryInspector, OpenHands `LLMSecurityAnalyzer`, LlamaFirewall AlignmentCheck.
**Evidence confidence.** High.

### 3.4 Policy / rule engines (символьные и prose-политики)

**How it works.** Декларативные правила над именами инструментов и аргументами (Progent, Invariant, NeMo Colang) либо prose-слоты, подмешиваемые в промпт классификатора (Claude Code `environment`/`allow`/`soft_deny`, Codex `policy.md`, Cursor `allow_instructions`).
**Strengths.** Детерминизм (символьные) или выразительность и редактируемость (prose); иерархия org > user; Progent даёт SMT-проверку monotonic confinement.
**Limitations.** Символьные правила требуют сопровождения; prose-политики наследуют вероятностность классификатора.
**Representative solutions.** Progent (research), Invariant Guardrails, NVIDIA NeMo Guardrails, Adversa AI policy/rule engine (B), Claude/Codex/Cursor/Kilo prose-слоты.
**Evidence confidence.** High для prose-слотов; Medium для символьных (в основном research).

### 3.5 Human-in-the-loop / confirmation

**How it works.** Эскалация к человеку на риск выше порога; deny-and-continue вместо остановки; override-механики (`/approve` в Codex, до 10 недавних отказов, одно повторение).
**Strengths.** Последний рубеж; агент восстанавливается после отказа более чем в половине случаев (Codex).
**Limitations.** **[F]** Человек как детектор слаб и деградирует (13,6 %, 93 % одобрений, падение catch rate); ручное одобрение дало серьёзный непреднамеренный вред в 6,3 % сессий против 2,4 % с auto mode.
**Representative solutions.** OpenHands `ConfirmationPolicy` (AlwaysConfirm/ConfirmRisky/NeverConfirm), Goose четыре режима, Copilot CLI явные промпты, Codex `/approve`.
**Evidence confidence.** High.

### 3.6 Input-layer PI-probes и статический анализ содержимого

**How it works.** Сканирование выводов инструментов до попадания в контекст агента (Claude PI-probe, PromptGuard 2), онлайн статический анализ генерируемого кода (CodeShield).
**Strengths.** Двухслойность: для успеха инъекции нужно пройти и вход, и выход; PromptGuard 2 — recall 97,5 % при FPR 1 %, латентность до 19,3 мс.
**Limitations.** Сам по себе не останавливает действие; на AgentDojo PromptGuard снизил ASR с 17,6 % лишь до 7,5 %.
**Representative solutions.** Claude Code PI-probe, Meta LlamaFirewall (PromptGuard 2, CodeShield), Goose Egress-инспектор, Invariant (в OpenHands).
**Evidence confidence.** High.

### 3.7 Identity / authorization для агентских вызовов

**How it works.** Агент действует от делегированной личности пользователя (OAuth), доступ к инструментам ограничен scope, вызовы проходят через runtime-хуки (до показа инструмента, до исполнения, после).
**Strengths.** Убирает статические креденшелы из промпта; корпоративный аудит; расширяемость через webhook-и.
**Limitations.** **[F]** Тяжеловесно, vendor lock-in на MCP-рантайм, не сканирует вредоносный код/пакеты; ориентировано на enterprise, не на shell кодинг-агента.
**Representative solutions.** Arcade.dev (B), Copilot/GitHub OAuth-инфраструктура.
**Evidence confidence.** Medium (только источник B).

### 3.8 Runtime behavioural monitoring / observability

**How it works.** Непрерывный мониторинг сессии, построение «отпечатка» нормального поведения, семантическая проверка соответствия действия заявленной цели; логирование каждого решения.
**Strengths.** Ловит многошаговые атаки; explainable-логи для комплаенса.
**Limitations.** **[F]** Новые и непроверенные продукты; сложность моделей → риск ложных срабатываний и тюнинга; дорого; enterprise-ориентировано.
**Representative solutions.** Lasso Intent Deputy, Adversa AI Agent Control Platform, White Circle (Test/Protect/Observe), Goose Repetition-инспектор.
**Evidence confidence.** Medium (в основном источник B + вендорские цифры).

### 3.9 Supply-chain / dependency intelligence

**How it works.** Проверка имени пакета до установки: OSV, тайпсквоттинг, фиды вредоносных пакетов, hallucination-бенчмарк; SBOM.
**Strengths.** Закрывает slopsquatting — класс, который action-гейты не видят.
**Limitations.** Очень ранняя стадия, узкая область (только установки).
**Representative solutions.** DepScope, SecureClaw (частично), Snyk Agent Scan (упомянут в A).
**Evidence confidence.** Medium (источник B).

---

## 4. Most Relevant Competitors and Alternatives

| Solution | Status / maturity | Target user | Core approach | What is controlled | Runtime enforcement | Human involvement | Strengths | Limitations |
|---|---|---|---|---|---|---|---|---|
| **Claude Code Auto Mode** (Anthropic) | Commercial production, default для Pro/Max/Team | Разработчики | 3 тира + reasoning-blind 2-стадийный классификатор + PI-probe + sandbox-runtime | Shell, web fetch, внешние инструменты, ФС вне проекта, субагенты | Да, до исполнения; fail-closed | Эскалация 3 подряд / 20 всего | FPR 0,4 %, deny-and-continue, prose-политика, opt-in sandbox | FNR 17 % на реальных overeager; ASR 60–80 % в независимом red-team (Rehberger); блокировка Bash при 429 |
| **OpenAI Codex Auto-review / guardian** | Commercial production | Разработчики, enterprise | Ревьюер только на границе sandbox; один вызов GPT-5.4 Thinking (low) | Эскалации sandbox: exec с повышенными правами, сеть, запись вне roots, MCP, новые домены | Да, fail-closed на ошибках и таймаутах | Circuit breaker 3 подряд / 10 из 50; `/approve` override | ~7,2 % трафика доходит до ревью; PI Recall 99,3 %; managed-политики | 5,83 % ASR в стороннем тесте; от model scheming не защищает; токены ревьюера расходуются |
| **Cursor Run Modes** | Commercial production (3.6) | Разработчики | allowlist → sandbox → classifier subagent | Shell, MCP, Fetch; protection toggles | Да; позиция вендора — «не граница безопасности» | «ask user» как третий исход | −84 % запросов к пользователю; NL-инструкции классификатору | CVE-2025-54135/54136, обходы денайлиста, DuneSlide (побег из sandbox) |
| **GitHub Copilot Agents (CLI/Cloud)** | Commercial production, крупнейшая база | Разработчики GitHub | Firewall + allowlist доменов + эфемерные контейнеры + явные промпты | Egress, действия CLI, PR-флоу | Да (инфраструктурно) | Подтверждение каждого внешнего действия в CLI | Сильные дефолты изоляции, интеграция в workflow | Обнаружены обходы промптов инъекцией; lock-in на экосистему; непрозрачно |
| **Kilo Code гейт** (Kilo-Org, #9138/#10248/#10249) | Open-source, дизайн + rollout (часть деталей — из issue, не из смердженного кода) | OSS-разработчики | Хук в `Permission.ask`, двухстадийный reasoning-blind, 3 prose-слота, fail-closed | Все запросы разрешения харнесса | Да | Эскалация 3/20, сброс на ход пользователя | Ближайший OSS-аналог целевой архитектуры; отказ от legacy approve-on-error | Внутри одного харнесса; зрелость реализации не подтверждена |
| **OpenHands SDK security analyzers** | Open-source, рабочий | OSS-разработчики, исследователи | `SecurityRisk` enum + `ConfirmationPolicy` + анализаторы (LLM, Invariant, GraySwan) | Tool-calls по уровню риска | Да; UNKNOWN трактуется как риск | `AWAITING_CONFIRMATION`, reject-with-feedback | Подключаемые анализаторы, Docker на агента, понятные пороги | Риск оценивает сама модель через tool-call (не reasoning-blind); внутри своего SDK |
| **Goose (Block)** | Open-source, рабочий | OSS-разработчики | Стек инспекторов: Security → Egress → Adversary → Permission → Repetition | Tool-calls, egress, повторы | Да | SmartApprove по `read_only_hint`; ask на write/destructive | Самый слоистый OSS-стек; кэш решений | Привязан к своему харнессу; метрик нет |
| **Meta LlamaFirewall** | Open-source (research-grade, arXiv 2505.03574) | Строители агентов | PromptGuard 2 + AlignmentCheck + CodeShield | Входные тексты, trace рассуждений, генерируемый код | Да, как библиотека-слой | Не определяет | ASR на AgentDojo до 2,9 %; малые модели, 19,3 мс | Аудит CoT (противоположность reasoning-blind); AlignmentCheck — секунды латентности |
| **Lasso Intent Deputy** | Commercial, launched Feb 2026, ранние клиенты | Enterprise security | Поведенческий мониторинг сессии, semantic intent | Действия агента в сессии | Да (блок/алерт) | Алерты и логи для комплаенса | Заявлено 99,83 % detection при <50 мс; многошаговые атаки | Вендорские цифры, не верифицированы; enterprise-цена; риск FP |
| **Adversa AI Agent Control Platform** | Commercial, ранняя стадия | Enterprise/команды | Control layer между агентом и системой; ~100 паттернов + статический анализ, локально | Команды, входы/выходы, пакеты, CVE | Да, agent-agnostic | Не описано | Заявлена agent-agnostic работа (Claude/Copilot/Cursor) + OSS SecureClaw | Ранняя стадия; overhead и латентность; эффективность не подтверждена |
| **Arcade.dev** | Commercial production, enterprise-клиенты | Enterprise IT/DevOps | Auth → Scope → Contextual Access hooks (до/во время/после вызова) | MCP tool-calls, идентичность, scope | Да, через хуки | Кастомные webhook-политики | Решает auth/governance целиком; аудит | Тяжеловесно; lock-in на MCP-рантайм; не про shell/пакеты |
| **DepScope** | Open/free API, demo-стадия | Разработчики и агенты | Data-driven проверка пакета | Установка зависимостей | Опосредованно (агент спрашивает API) | Нет | Единственный в обзоре про slopsquatting; 19 экосистем | Очень ранний; только зависимости |
| **Progent / CaMeL / CARE** | Research prototypes | Исследователи | Символьные привилегии + SMT; taint-tracking DSL; статическая верификация shell-команд | Аргументы инструментов; потоки данных; одиночные shell-команды | Прототипно | — | Progent: ASR 39,9 %→1,0 % (AgentDojo); CARE: F1 85,64 %, FPR 0,91 %, 2,32 мс | Прототипы, не продукты; CaMeL имеет известный обход через аргументы |

*Не конкуренты, а контекст:* **Kilo Code как продукт** и **OpenClaw** — сами по себе харнессы/агенты без встроенного гейта (B); OpenClaw в источнике B описан как пример проблемы, а не решения.

---

## 5. Detailed Comparison by Product and Technical Dimensions

| Dimension | Observed approaches |
|---|---|
| Agent autonomy | Явная продуктовая цель: Codex ~×200 меньше остановок; Cursor −84 % запросов; Claude auto mode — дефолт. Полюса: `never`/`bypassPermissions`/YOLO (всё разрешено) ↔ AlwaysConfirm |
| Tool/action-level control | Повсеместно: гейт стоит на конкретном tool-call. Гранулярность различается: инструмент целиком (Copilot, Cline-категории) vs аргументы (Progent, Claude, Codex) |
| Runtime decision making | Три модели: детерминированный allowlist; ревьюер-модель; порог риска, выставленный самой моделью (OpenHands `security_risk`) |
| Context awareness | Claude: только user-сообщения + сырые tool-calls (reasoning-blind). Codex: компактный транскрипт с выводами инструментов, но без private CoT. LlamaFirewall: наоборот, аудит всей цепочки. Lasso: вся сессия |
| Static vs dynamic policies | Гибрид у всех: статический allowlist для 90+ % трафика, динамическая модель на остатке. Prose-политика — динамический ввод в статический промпт. Progent — динамически генерируемые символьные правила с монотонным сужением |
| Human confirmation model | Ask как третий исход; deny-and-continue как норма; circuit breaker (3/20 у Claude и Kilo; 3 и 10/50 у Codex); override у Codex (`/approve`, ≤10 отказов, одно повторение) |
| Sandboxing / isolation | Seatbelt / Landlock+seccomp / bubblewrap / restricted tokens / Docker; egress allow-only; неизменяемые пути (`.bashrc`, `.git/hooks/`, `.mcp.json`) |
| False-positive considerations | Единственные раскрытые цифры — Claude Code: Stage 1 FPR 8,5 % → полный конвейер 0,4 %; CARE FPR 0,91 %. Практика: жалобы на блокировку легитимных `terraform apply`, SSH |
| User friction considerations | Правило OpenAI: если отказов подряд регулярно >3 — чините границу, а не учите ревьюера одобрять шум. B отдельно называет approval fatigue критической проблемой |
| Latency considerations | PromptGuard 2 — до 19,3 мс; CARE — 2,32 мс; Lasso — заявлено <50 мс; AlignmentCheck — секунды. Двухстадийность + кэш префикса как приём удешевления (Claude, Kilo) |
| Cost considerations | Токены ревьюера расходуются у пользователя (Codex); Stage 1 — single-token; почти весь промпт Stage 2 — кэш-хит; DepScope и SecureClaw оптимизируют потребление токенов |
| Auditability | Транскрипты сессий (`~/.codex/sessions`), explainable-логи (Lasso), полный audit log (Arcade), кэш решений в `permission.yaml` (Goose) |
| Integration complexity | От «одна настройка в харнессе» (Claude/Codex/Cursor) до «принять MCP-рантайм» (Arcade) и «поднять enterprise-платформу» (Lasso/Adversa) |
| Coding-agent support | Максимальная у харнессов; у guardrail-продуктов частичная (Adversa заявляет agent-agnostic; Arcade — про MCP-инструменты, не про shell) |
| Extensibility across harnesses | **[F]** Все гейты харнессов вшиты в свой харнесс (§ 5 источника A). Кросс-харнессные претензии есть только у Adversa (agent-agnostic control layer) и косвенно у Arcade (любой LLM через MCP) — обе на ранней стадии |

---

## 6. Closest Alternatives to Our Project

### Kilo Code gate (#9138 / #10248 / #10249)
**Why it is close:** источник A прямо называет дизайн «чертежом, совпадающим с AgentGate»: хук в точке запроса разрешения, три исхода approve/deny/error→fail-closed-ask, двухстадийный reasoning-blind классификатор, три prose-слота, эскалация 3/20, дроп широких allow при входе в режим.
**Where it differs:** живёт внутри одного харнесса; не отдельный сервис; часть описанного взята из дизайн-issue, а не из смерженного кода.
**Competitive risk:** **Medium-High** — совпадение архитектуры почти полное, отличие — в упаковке.
**What we need to investigate further:** что реально смерджено в `permission/index.ts`; есть ли планы вынести гейт наружу.

### Adversa AI — Agent Control Platform
**Why it is close:** позиционируется как control layer *между агентом и системой*, agent-agnostic (Claude, Copilot, Cursor), локальная обработка без внешних AI-API, политики + детекция.
**Where it differs:** по источнику B — упор на статический анализ, CVE/SBOM, вредоносные npm-пакеты и библиотеку ~100 паттернов; про AST-нормализацию shell, prose-политику или двухстадийную LLM-классификацию данных нет.
**Competitive risk:** **High по позиционированию, неизвестен по исполнению** — это ближайший заявленный кросс-харнессный конкурент.
**What we need to investigate further:** реальный механизм интеграции с харнессами; латентность; что именно означает «agent-agnostic» на практике; наличие публичных метрик FPR/FNR.

### Anthropic Claude Code auto mode
**Why it is close:** тот же каскад (тиры → reasoning-blind двухстадийный классификатор → эскалация), те же prose-слоты, тот же fail-closed, deny-and-continue, circuit breaker.
**Where it differs:** встроен в один харнесс и в одну модельную экосистему; классификатор по умолчанию использует активную модель сессии; никакой внешней точки принятия решения нет.
**Competitive risk:** **High** — это эталон, с которым нас будут сравнивать по FPR/FNR.
**What we need to investigate further:** воспроизводимость их цифр на нашем бенчмарке; насколько FNR 17 % на overeager — планка или потолок класса.

### OpenAI Codex guardian
**Why it is close:** отдельный ревьюер, fail-closed, deny-and-continue с усиленной формулировкой, circuit breaker, managed-политики org > user.
**Where it differs:** триггерится только на границе sandbox (внутри sandbox ревью нет), один вызов без двухстадийности, ревьюер видит выводы инструментов.
**Competitive risk:** **High** — задаёт норму «мало эскалаций, почти все одобряются».
**What we need to investigate further:** применим ли принцип «ревью только на границе» без нашей собственной sandbox-границы.

### OpenHands security analyzers
**Why it is close:** подключаемая абстракция анализатора риска + политика подтверждения — то есть гейт как *интерфейс*, а не как жёстко вшитая логика.
**Where it differs:** риск оценивает сама модель агента через параметр tool-call (не reasoning-blind, уязвимо к инъекции); живёт внутри их SDK.
**Competitive risk:** **Medium.**
**What we need to investigate further:** можно ли встроиться как внешний `SecurityAnalyzer` — это потенциальный канал дистрибуции, а не только конкурент.

---

## 7. Common Industry Patterns

**Pattern 1 — Sandbox is the boundary, the gate is convenience.**
*Evidence:* Cursor («best-effort convenience, not a security boundary»), Anthropic (закрытие отчёта Rehberger как «Informative»), OpenAI («сначала сужай sandbox»), sandbox-runtime, Landlock/Seatbelt, Docker в OpenHands, эфемерные контейнеры Copilot.
*Product implication:* нельзя продавать гейт как гарантию безопасности. Честная формулировка ценности — «меньше подтверждений при не худшем риске», и это надо измерять.

**Pattern 2 — Tiered short-circuit: большинство действий не доходит до модели.**
*Evidence:* Claude Code 3 тира; Codex — только граница sandbox (7,2 % трафика); Cursor allowlist→sandbox→classifier; Kilo safe-tool allowlist; Goose `read_only_hint`.
*Product implication:* дешёвая детерминированная ступень 1 обязательна; доля трафика, дошедшая до LLM, — самостоятельная метрика.

**Pattern 3 — Отдельный ревьюер, изолированный от контекста агента.**
*Evidence:* Anthropic, OpenAI, Cursor, Goose, Kilo, OpenHands (кроме `LLMSecurityAnalyzer`).
*Product implication:* reasoning-blind — норма отрасли, не дифференциатор; спорный момент — что именно ревьюер видит (§ 12).

**Pattern 4 — Deny-and-continue + circuit breaker вместо остановки.**
*Evidence:* Claude (3/20), Codex (3 подряд, 10/50, восстановление >50 %), Cursor (retry differently), OpenHands (reject-with-feedback), Goose (BLOCK с причиной), Kilo (3/20).
*Product implication:* формат deny-сообщения — часть продукта; нужен счётчик и эскалация; текст отказа должен запрещать обход, а не только действие.

**Pattern 5 — Fail-closed.**
*Evidence:* Claude, Codex (404 модели → deny), Kilo (отказ от legacy approve-on-error), OpenHands (UNKNOWN → блок), CARE.
*Product implication:* совпадает с нашей позицией; цена известна и наблюдаема (жалобы на блокировку Bash при 429) — её надо измерять как отдельный failure mode.

**Pattern 6 — Денайлисты хрупки, allowlist-и должны быть узкими.**
*Evidence:* Cursor (денайлист устарел после ≥4 обходов), Gemini CLI (обход префикса через `;`), рекомендация OpenAI (`["cargo","test"]`, а не `["python"]`), дроп `bash:*` у Claude и Kilo.
*Product implication:* решение по сырой строке недопустимо; нормализация через AST и узкие правила — обязательны, и это подтверждается извне.

**Pattern 7 — Иерархия политик org > user и prose-политика поверх правил.**
*Evidence:* Codex `requirements.toml`, managed settings Claude, `yoloModeAllowed:false` у Cline, три prose-слота у Claude и Kilo, `allow_instructions` у Cursor.
*Product implication:* один YAML-профиль на сервисе согласуется с рынком; enterprise ожидает, что managed побеждает локальное.

---

## 8. Unresolved Gaps

### Gap 1 — Provenance / taint между шагами в продакшене
**Evidence:** источник A, §5.1: Anthropic сознательно жертвует провенансом ради устойчивости к инъекциям; классификатор не может отличить, взял агент значение из запроса пользователя или выдумал. Именно это дало обход Rehberger (415 → curl → ZIP → `struct.py`) — цепочка внешне безобидных шагов. Академически решает только CaMeL (у которого есть известный обход через аргументы).
**Why it matters:** самые опасные реальные атаки — многошаговые, где каждый шаг по отдельности выглядит нормально.
**Existing partial solutions:** CaMeL (research), Progent (monotonic confinement, research), Lasso Intent Deputy (сессионные baselines, коммерческий, метрики вендорские).
**Confidence:** High, что в проде этого нет у харнессов; Medium, что этого нет вообще (источник B описывает Lasso именно как решение многошаговой проблемы — противоречие не разрешено).
**What must be validated:** действительно ли Intent Deputy закрывает эту задачу; какой минимальный taint даёт эффект без взрыва FPR.

### Gap 2 — Harness-agnostic политика и enforcement
**Evidence:** источник A прямо: «все существующие гейты вшиты в свой харнесс». Источник B: Adversa заявляет agent-agnostic control layer, Arcade — любой LLM через MCP.
**Why it matters:** команды используют несколько агентов; политика безопасности не должна дублироваться и расходиться.
**Existing partial solutions:** Adversa AI (заявка, ранняя стадия), Arcade.dev (только MCP-инструменты и идентичность, не shell).
**Confidence:** **Medium** — «никто не делает» здесь недопустимо: среди рассмотренных решений как минимум два заявляют кросс-агентность.
**What must be validated:** глубина кросс-харнессности Adversa; сколько харнессов реально дают точку перехвата уровня `PreToolUse` (у Claude Code контракт документирован детально).

### Gap 3 — Отсутствие открытых, низкофрикционных решений для не-enterprise
**Evidence:** источник B: продвинутые инструменты (Lasso, Arcade) целятся в enterprise со сложным внедрением; у пользователей OSS-агентов эквивалента нет. Источник A: у OSS-харнессов гейты есть, но каждый — свой и внутри себя.
**Why it matters:** сегмент, который сегодня просто отключает защиту.
**Existing partial solutions:** SecureClaw (только OpenClaw), Goose/OpenHands/Kilo (только внутри себя).
**Confidence:** Medium.
**What must be validated:** готовность этого сегмента ставить отдельный сервис, а не плагин.

### Gap 4 — Замкнутая петля «провал бенчмарка → детерминированное правило»
**Evidence:** источник A, §5.4: White Circle делает Test/Protect как продукт, но не для action-gate кодинг-агента. Источник B: «мало публичных данных по угрозам кодинг-агентов; вклад в бенчмарк отличит команду».
**Why it matters:** без такой петли улучшения гейта не воспроизводимы и не доказуемы.
**Existing partial solutions:** White Circle (CircleGuardBench, KillBench — другая предметная область), AgentDojo/ASB/MonitoringBench (используются, но как эвалы, не как петля).
**Confidence:** Medium.
**What must be validated:** насколько CircleGuardBench переносим; не покрывает ли петлю уже внутренний процесс вендоров (в публичных материалах не описан).

### Gap 5 — Бюджеты автономии на сессию
**Evidence:** источник A, §5.5: есть счётчики отказов (3/20, 10/50) и лимиты запросов (Cline), но комплексных бюджетов «сколько необратимых / сетевых / вне-проектных действий за сессию» с деградацией автономии в рассмотренных системах не описано.
**Why it matters:** ограничивает blast radius там, где пер-экшн-классификация ошибается (FNR 17 %).
**Existing partial solutions:** circuit breakers Claude/Codex/Kilo, Repetition-инспектор Goose, лимиты Cline.
**Confidence:** Medium (аргумент от отсутствия в обзоре).
**What must be validated:** нет ли такого в закрытых enterprise-настройках; какой бюджет не разрушает продуктивность.

### Gap 6 — Стоимость и латентность слоя безопасности как продуктовая характеристика
**Evidence:** цифры разрознены (CARE 2,32 мс, PromptGuard 19,3 мс, Lasso <50 мс, AlignmentCheck — секунды); Codex отмечает, что токены ревьюера расходуются; ни один вендор не публикует сквозной бюджет «латентность + стоимость на действие» рядом с FPR/FNR.
**Why it matters:** это ровно тот компромисс, который решает, включит ли разработчик защиту.
**Existing partial solutions:** двухстадийность и кэш префикса (Claude, Kilo); малые модели (PromptGuard 2).
**Confidence:** Medium.
**What must be validated:** наш собственный бюджет p50/p95 и цена на 1 000 действий.

### Gap 7 — Supply chain / slopsquatting в контуре action-гейта
**Evidence:** источник B: модели часто галлюцинируют имена пакетов; большинство решений не мешают агенту поставить несуществующую библиотеку; DepScope — почти единственный адресный ответ и он на demo-стадии.
**Why it matters:** установка пакета — обычное «безобидное» действие, которое гейты пропускают.
**Existing partial solutions:** DepScope, SecureClaw, Snyk Agent Scan, Adversa (детекция вредоносных npm).
**Confidence:** Medium (только источник B).
**What must be validated:** частота этого класса в реальных сессиях; можно ли решать проверкой аргументов установки без внешнего сервиса.

**Проверено и НЕ признано gap-ом:** selective human confirmation, dynamic risk evaluation, action-level runtime enforcement, deny-and-continue, аудит решений, fail-closed — всё это широко представлено (§ 7) и не может заявляться как незакрытая ниша.

---

## 9. Relevance to Our Product Hypothesis

### What appears established in the market?
Каскад «детерминированная ступень → LLM-ступень → эскалация»; reasoning-blind; fail-closed; deny-and-continue с причиной и подсказкой; circuit breaker; prose-политика в трёх слотах; один профиль с иерархией org > user; отказ от решений по сырой строке в пользу нормализованного действия; запись каждого решения в лог. Всё это — воспроизведение существующих практик, а не новизна.

### What appears similar?
Kilo Code gate — почти совпадающая архитектура (OSS, внутри харнесса). Claude Code auto mode — тот же конвейер в продакшене. Adversa AI — то же позиционирование «слой между агентом и системой», иная техническая начинка. OpenHands — тот же гейт, но как подключаемый интерфейс.

### What may be differentiated?
**[H]** Гейт как **отдельный сервис с собственным HTTP-контрактом**, а не как код внутри харнесса — среди рассмотренных решений такой упаковки для shell-действий кодинг-агента не описано (кроме заявки Adversa, чьё исполнение неизвестно). **[H]** Единый профиль поверх нескольких харнессов. **[H]** Бюджеты автономии на сессию. **[H]** Замкнутая петля benchmark→правило. Каждое требует проверки, ни одно не доказано как уникальное.

### What is NOT proven to be unique?
- LLM-классификация действия перед исполнением;
- двухстадийность fast→CoT и reasoning-blind;
- fail-closed, deny-and-continue, эскалация по счётчикам;
- prose-политика и профили;
- AST-нормализация shell-команд (CARE делает это лучше и раньше, с опубликованными метриками);
- аудит решений;
- «мы снижаем approval fatigue» (Codex и Cursor заявили это раньше и с цифрами);
- «мы работаем с любым харнессом» (заявляют Adversa и, в своей нише, Arcade).

### What competitive assumptions are risky?
1. **«Мы безопаснее»** — вендоры сами не считают гейт границей безопасности; независимый red-team (ASR 60–80 %) показывает, что заявленные 0 % не держатся. Тезис о безопасности почти наверняка будет опровергнут адаптивной атакой.
2. **«Внешний сервис не добавляет заметной латентности»** — рискованно; у CARE детерминированная ступень 2,32 мс, но сеть + LLM-ступень измеряются отдельно.
3. **«Кросс-харнессность — наш ров»** — точка перехвата детально документирована в основном у Claude Code (`PreToolUse`); у других харнессов глубина интеграции не подтверждена.
4. **«Меньше ложных срабатываний, чем у конкурентов»** — сравнивать FPR можно только на одном датасете; вендорские FPR/FNR получены на трёх разных наборах и, как отмечает сам источник A, не смешиваются.
5. **«Наш каскад новый»** — он воспроизводит Kilo/Claude почти дословно.

---

## 10. Implications for MVP

| Market observation | Product implication | Technical implication | Validation needed |
|---|---|---|---|
| Гейт — не граница безопасности (Cursor, Anthropic, OpenAI) | Позиционировать как снижение трения при сопоставимом риске, не как защиту | Рекомендовать sandbox рядом с гейтом; не обещать блокировку определённых атак | Есть ли у наших пользователей sandbox вообще |
| Только 7,2 % действий доходят до ревьюера (Codex) | Дешёвая ступень 1 — ядро продукта, а не преамбула | Метрика «доля трафика на LLM» в API-ответе/логе | Наша реальная доля на бенчмарке |
| Денайлисты и префиксные allowlist-ы обходятся (Cursor, Gemini CLI) | Не предлагать денайлисты как фичу | AST-нормализация обязательна; узкие правила; тесты на обфускацию | Покрытие обфускаций в табличных тестах |
| Человек — слабый детектор (13,6 %; 93 % одобрений; деградация) | `ask` — дорогой ресурс: каждая эскалация должна нести причину и вариант | Формат deny/ask-сообщения — часть контракта | Восстанавливается ли агент после нашего deny (цель Codex >50 %) |
| Fail-closed имеет цену (блокировка Bash при 429) | Явно документировать поведение при недоступности LLM | Отделить таймаут от отказа в формулировке (приём Codex) | Частота таймаут-ask на реальном трафике |
| Широкие allow-правила стирают границу | При включении режима дропать `bash:*`/`python *` | Профиль должен различать узкие и blanket-правила | Не ломает ли дроп типовые рабочие сценарии |
| Prose-политика — норма (Claude, Codex, Cursor, Kilo) | Три слота как формат профиля | Модель «скопировать дефолт и править», а не merge | Понятность слотов пользователю |
| Все гейты вшиты в харнесс; кросс-харнессность заявляют 2 продукта | Ставка на отдельный сервис оправдана, но не как «уникальность» | Стабильный HTTP-контракт + адаптеры; приоритет `PreToolUse` Claude Code (контракт документирован) | Наличие эквивалентной точки в втором харнессе |
| Аудит — ожидание рынка (Arcade, Lasso, Codex-транскрипты) | Лог решений с причиной — must-have для демо | Атрибуция решения к ключу/сессии | Что именно нужно показать в демо |
| Slopsquatting не покрыт action-гейтами (B) | Кандидат на расширение после MVP, не в MVP | Проверка аргументов install-команд | Частота класса в реальных сессиях |

---

## 11. Implications for Benchmark

1. **Что обязательно измерять.** Не только детекцию: (a) FPR на реалистичном «нормальном» трафике; (b) FNR отдельно на overeager-действиях и отдельно на adversarial-цепочках — смешивать нельзя (прямое предупреждение источника A); (c) долю трафика, дошедшую до LLM-ступени; (d) латентность p50/p95 по ступеням; (e) стоимость на 1 000 действий; (f) долю восстановлений агента после deny; (g) частоту ask по таймауту/ошибке (цена fail-closed).
2. **Baselines для сравнения.** Ручное подтверждение человеком (планка: блокирует 13,6 %, одобряет 93 %); «всё разрешено» (bypass/YOLO); только allowlist без модели; только sandbox; одностадийный LLM-классификатор без ступени 1. Вендорские цифры (Claude 0,4 % / 17 %, Codex 90,3 % / 99,3 %, CARE 85,64 % / 0,91 % / 2,32 мс) можно использовать как ориентиры, но не как измерения на нашем датасете.
3. **Критичные failure modes.** Многошаговые цепочки, где каждый шаг безобиден (паттерн Rehberger: недоступность инструмента → агент сам выбирает опасный путь); обфускация (`$(…)`, `eval`, переменные, base64); обход через префикс и разделители (`;`, `&&`); команда, собранная в скрипте; вредоносный конфиг/dotfile (CVE-2025-54135/54136); подмена ранее одобренного конфига; недоступность LLM; блокировка легитимных операций (`terraform apply`, SSH, git push).
4. **Почему нельзя мерить только attack detection.** Продукт конкурирует с ручным подтверждением и с «отключить защиту»: решение с идеальной детекцией и высоким FPR проигрывает, потому что его выключают. Сам рынок формулирует правило «если отказов подряд регулярно >3 — чините границу», то есть трение считается дефектом наравне с пропуском.
5. **Usability / friction metrics.** Число эскалаций на сессию; серии отказов подряд; доля действий, требующих вмешательства человека; понятность причины отказа (есть ли безопасная альтернатива в тексте); ложные блокировки на типовых DevOps-командах.
6. **Performance metrics.** Ступень 1 — p50 (ориентир класса: CARE 2,32 мс); сквозная задержка ответа сервиса; накладные расходы адаптера; поведение при таймауте.
7. **Что можно будет утверждать только после бенчмарка.** Любое сравнение FPR/FNR с конкурентами; любое «дешевле/быстрее»; «снижает трение на X %»; «ловит многошаговые цепочки»; «работает с любым харнессом» (это проверяется интеграционно, не бенчмарком).

---

## 12. Open Questions

### High priority
1. **Расхождение между источниками по Claude Code:** A — классификатор на Sonnet 4.6, auto mode дефолт с 14 августа 2026; B — «Claude 4.6» и «auto mode по умолчанию с августа 2023». Датировка B несовместима с остальной хронологией обоих документов; требует проверки перед любым публичным использованием.
2. **Расхождение по Kilo Code:** A описывает проработанный гейт (двухстадийный, fail-closed, 3 слота); B утверждает, что Kilo «не содержит встроенного auto-mode и проверок безопасности». Возможное объяснение — разные стадии (дизайн-issue против релиза), но это не подтверждено.
3. **Расхождение по заявленной эффективности:** заказанный тест Trajectory Labs даёт 0/720 для Claude auto mode, независимый red-team Rehberger — ASR 60–80 %. Какая планка честная для нашего бенчмарка?
4. Насколько реально agent-agnostic решение Adversa AI — это прямой конкурент нашему позиционированию.
5. Есть ли у нас точка перехвата уровня `PreToolUse` во втором и третьем харнессе, или кросс-харнессность останется декларацией.

### Medium priority
6. Цифры Lasso (99,83 % при <50 мс) — вендорские и невалидированные; какова их методология.
7. Оценка рынка $55 млрд в 2026 → $888 млрд к 2035 (B) — единственный источник, независимо не подтверждён; использовать в презентации только с явной атрибуцией.
8. Атрибуция OpenClaw в источнике B («OpenAI, community») выглядит сомнительно и в A не встречается.
9. Переносим ли принцип Codex «ревью только на границе sandbox» на архитектуру без собственной sandbox-границы.
10. Что из legacy-описаний Kilo (`gatekeeper.ts`, 388 строк) актуально в смерженном коде.

---

## 13. Claims We Can Safely Make

### Supported by research
- Ручное подтверждение — слабый и деградирующий контроль: люди блокируют явно опасную подменённую команду в 13,6 % случаев, одобряют 93 % промптов, catch rate падает с ~17 % до ~5 % после 50 промптов.
- Индустрия сошлась на многоступенчатом каскаде, где до модели доходит меньшинство действий (Codex: 7,2 %).
- Денайлисты и префиксное сравнение с allowlist-ом систематически обходятся (Cursor → отказ от денайлиста; Gemini CLI CVE-цепочка).
- Fail-closed — преобладающая практика (Claude, Codex, Kilo, OpenHands, CARE), и Kilo сознательно отказался от approve-on-error.
- Гейт на основе модели вендоры сами не считают границей безопасности; граница — sandbox.
- Ограничение, живущее в тексте инструкций, агент рационализирует (Replit Agent) — контроль должен быть в call-path.
- Reasoning-blind оценка и deny-and-continue — устоявшиеся приёмы, а не новизна.

### Supported with caveats
- «Среди рассмотренных решений гейты кодинг-агентов вшиты в конкретный харнесс» — верно для харнессов, но Adversa AI и Arcade.dev заявляют кросс-агентность; формулировать только как *among the reviewed solutions*.
- «Provenance/taint между шагами в проде не реализован» — верно для харнессов из источника A; Lasso заявляет сессионный поведенческий анализ, который частично адресует ту же задачу.
- Метрики уровня FPR 0,4 % / FNR 17 % — вендорские, на трёх разных датасетах; цитировать только с указанием набора и источника, никогда не смешивать.
- Оценка объёма рынка и цифры детекции Lasso/Adversa — использовать с явной атрибуцией «по заявлению вендора».
- «Slopsquatting слабо покрыт» — подтверждается только источником B.

### Do not claim yet
- «Никто не делает harness-agnostic гейт» / «единственное решение» / «уникальный подход» — доказательств недостаточно.
- «Мы безопаснее Claude Code / Codex / Cursor» — сравнимых измерений нет.
- «Ниже FPR / выше recall, чем у конкурентов» — до собственного бенчмарка на общем датасете невозможно.
- «Ниже латентность / дешевле» — не измерено.
- «Устойчивы к prompt injection» — независимый red-team пробивал все проверенные системы.
- «Закрываем provenance/taint» — этого не сделал никто, кроме research-прототипов, и у CaMeL есть известный обход.
- Любые заявления о доле рынка, спросе или готовности платить — в исследованиях данных нет.
