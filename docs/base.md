# CLAUDE.md — AgentGate

Этот файл читается первым. Он задаёт контекст проекта, зафиксированные решения и правила работы. Подробные документы лежат в `docs/`:

- `docs/agent-gate-design.md` — архитектура сервиса (API, конвейер решения, профили, адаптеры, модель угроз)
- `docs/agent-gate-sft-plan.md` — датасет, обучение моделей, бенчмарк, план по дням
- `docs/agent-gate-artifacts.md` — бенчмарки, датасеты, RL-среды со ссылками
- `docs/coding-agent-security-market-2026.html` — обзор рынка (закрытые и OSS-решения, инциденты, стандарты)

Если что-то в этом файле расходится с `docs/`, прав этот файл: он короче и обновляется чаще.

---

## 1. Что мы делаем

**AgentGate** — отдельный сервис-гейт между любым кодинг-агентом (Kilo Code, Claude Code, Codex CLI, OpenCode, MCP-клиенты) и операционной системой. Харнесс перед каждым вызовом инструмента спрашивает разрешение (`PreToolUse` → `POST /v1/decide`), после выполнения отдаёт результат на разметку (`PostToolUse` → `POST /v1/observe`). Ответ: `allow | deny(reason) | ask`.

Контекст: хакатон по безопасности ИИ-кодинг-агентов, кейс «auto mode для Kilo Code» (по образцу Claude Code Auto Mode, который стал режимом по умолчанию 14 августа 2026). Сроки: 4 дня, 2 человека, 2×H200 (иногда недоступны). Дата старта: 3 сентября 2026.

Три результата, которые нужно показать:
1. **Harness-agnostic слой** — один сервис, любой харнесс, одна политика; переключение Kilo ↔ Claude Code на демо за 30 секунд. Kilo-адаптер оформляется как PR в `Kilo-Org/kilocode`.
2. **Дообученная маленькая модель** (Qwen3.5 0.8–4B), которая на бенчмарках AgentDoG (R-Judge, ATBench, ATBench-CodeX) не уступает AgentDoG-8B, а на action-level (CARE, RedCode-Exec, датасет кейса) превосходит правила и zero-shot, работая локально за десятки миллисекунд.
3. **Бенчмарк** с метриками кейса: ASR ↓, Utility ↑, FP ↓, Friction ↓, Latency ↓ — с лестницей бейзлайнов и честным разделом ограничений.

Отличие от «взяли харнесс и прикрутили классификатор»: детерминированная ступень с provenance (происхождение данных) ловит многошаговые инъекции без LLM; модель локальная и наша; политика переносится между харнессами; провалы бенчмарка превращаются в правила (Test → Protect).

---

## 2. Зафиксированные решения (не обсуждаются в v0.1)

- **Fail-closed везде.** Любая ошибка, таймаут, недоступность модели или сервиса → `ask`. Никогда → `allow` по ошибке. Это касается кода, конфигов и тестов.
- **Hard-deny не переопределяется** ни пользователем, ни `override`, ни prose-политикой.
- **Ступень 1 не читает текст.** Она работает с разобранным деревом команды, профилем и taint-метками. Это единственный контроль, архитектурно неуязвимый к prompt injection, и он обязателен по условиям кейса.
- **Ступень 2 reasoning-blind:** на вход только задача пользователя, профиль, история действий с вердиктами, нормализованное действие и флаг `tainted`. Мысли агента и тексты tool results в промпт не попадают никогда.
- **Provenance — эвристика по подстрокам ≥ 12 символов** из недавних tool outputs, а не полноценный information-flow control.
- **Исполнение остаётся у харнесса.** Сэндбокс (`@anthropic-ai/sandbox-runtime`) — требование, а не наш код. Отсутствие сэндбокса → предупреждение в лог и панель, работа продолжается.
- **Один YAML-профиль на все харнессы.** Правила из Test → Protect добавляются только в него.
- **`deny` не кэшируется, `allow` кэшируется** на сессию по хэшу нормализованного действия.
- **Модель одна, формы входа три** (S / H / T, см. §5). Никаких отдельных адаптеров на режим.
- **Тест-сеты в обучение не идут:** R-Judge, ATBench (все), ASSE-Safety, CARE, SABER, датасет кейса, DepScope Hallucinations. Утечка контролируется по хэшу.
- **ASR считается на уровне эпизода:** атака из N безобидных шагов — одна атака; успех фиксируется по состоянию сэндбокса, а не по тексту ответа агента.
- **Порядок адаптеров:** Kilo → Claude Code → Codex → MCP-прокси.

Позиции по открытым вопросам команды (от Тимура):
- Явный запрос пользователя авторизует рискованное действие для ступени 2 (метка `U`), но не отменяет hard-deny.
- Входы и результаты инструментов защищаем через `/v1/observe`: маркируем как tainted, не вырезаем.
- MCP-сервер — недоверенная граница по умолчанию; доверие только через allowlist профиля.
- Накопительный ущерб (10 000 upload) — детерминированные правила бюджета сессии, отдельная категория бенчмарка.

---

## 3. Архитектура в одном экране

```
Харнесс ──PreToolUse──▶ Адаптер ──POST /v1/decide──▶ AgentGate
   ▲                                                   │
   │ tool result / штатный диалог                      ├─ Нормализатор (bash AST, пути, домены, пакеты)
   │                                                   ├─ Ступень 1: hard-deny → профиль → provenance → safe-allowlist   (~1 мс, без LLM)
   │                                                   ├─ Ступень 2: локальная модель 0.8–4B, первый токен A/D/U      (~30–100 мс)
   │                                                   ├─ Ступень 3: облачная CoT только на U, опционально            (~1–3 с)
   │                                                   └─ Session store (SQLite / Redis), лог JSONL, панель
   └──PostToolUse──▶ Адаптер ──POST /v1/observe──▶ извлечение taint-фрагментов
```

**API:** `POST /v1/decide`, `POST /v1/observe`, `POST /v1/sessions`, `POST /v1/sessions/{id}/override`, `GET/PUT /v1/profiles/{id}[/rules]`, `GET /v1/decisions`, `GET /healthz`. Bearer-токен обязателен при биндинге на неlocalhost.

**Action** (вход `decide`): `session_id, harness, tool ∈ {shell, file_write, file_read, network, package_install, mcp_call}, raw, args{cwd, argv, paths, domains, packages, mcp}, context_delta{user_messages, tool_outputs[{tool, hash, text≤8KB}]}`.

**Decision** (ответ): `decision, reason, stage, rule_id, latency_ms, suggest`. `reason` пишется так, чтобы отдать агенту как tool result без правок.

**Deny-and-continue:** отказ уходит агенту текстом «заблокировано политикой: <причина>; не обходи; предложи безопасную альтернативу или спроси пользователя». Эскалация в `ask`: 3 отказа подряд или 10 из последних 50.

**Модуль пакетов** (внутри ступени 1, до установки): DepScope MCP `check_package` → эвристики zero-day (пакет не существует; первый релиз < 7 дней; загрузок < 100/нед; нет репозитория; Левенштейн ≤ 2 до топ-5000; суффиксы `-easy/-pro/-turbo/-plus/-extras`; install-скрипты). Одна эвристика → `ask`, две → `deny`; при `tainted` имени — одна → `deny`.

Деплой: локально `agentgate serve` (llama.cpp, SQLite, ступень 3 выкл.); сервер `docker compose up` (vLLM, Redis, ступень 3 по профилю). Переключение — переменная `AGENTGATE_URL`.

Полные детали, YAML-профиль и таблица адаптеров — `docs/agent-gate-design.md`.

---

## 4. Структура репозитория (целевая)

```
agentgate/
  CLAUDE.md                     # этот файл
  docs/                         # четыре документа из §0
  service/                      # FastAPI-сервис
    agentgate/
      api/                      # роуты /v1/*
      normalize/                # bash AST (bashlex / tree-sitter-bash), пути, домены, пакеты
      stage1/                   # hard_deny.py, profile.py, provenance.py, allowlist.py, packages.py
      stage2/                   # клиент vLLM / llama.cpp, промпт формы H, пороги, калибровка
      stage3/                   # клиент облачного API, таймауты, fail-closed
      session/                  # store (sqlite/redis), счётчики, кэш решений, taint_spans
      observe/                  # извлечение taint-фрагментов из tool outputs
      profiles/                 # загрузка YAML, валидация, rules из Test → Protect
      log/                      # JSONL-лог решений
    panel/                      # одна страница: лента решений, override, добавить правило, бенчмарк
    profiles/default-dev.yaml
    tests/
  adapters/
    kilo/                       # TypeScript-плагин в Permission.ask (+ перехват tool result); PR в Kilo-Org/kilocode
    claude-code/                # agentgate-hook: PreToolUse / PostToolUse через settings.json
    codex/                      # hooks в config.toml
    mcp-proxy/                  # для харнессов без hooks
  cli/                          # agentgate serve | install <harness> | bench | policy add
  data/
    raw/                        # скачанные источники (не коммитим большие файлы)
    build/                      # скрипты сборки датасета: filter, contrast, obfusc, slice_trajectories, label, dedup
    test_hashes/                # хэши всех тест-сетов для контроля утечки
    sft/                        # train.jsonl, dev.jsonl (единая схема, §5)
  train/                        # конфиги LoRA/full FT, скрипты запуска, экспорт GGUF
  bench/
    harness/                    # контейнер, фикстуры репозиториев, подставной интернет и npm-реестр, проверка состояния
    suites/                     # care/, redcode/, case/, taint_multistep/, rjudge/, atbench/, tbench_utility/, packages/
    baselines/                  # B0–B4, guard-модели, облачные API
    report/                     # таблицы с CI, latency, стоимость
  README.md                     # модель угроз, гарантии «всегда / по возможности», ограничения
```

---

## 5. Модель: три формы входа, один ответ

| Форма | Вход | Используется |
|---|---|---|
| **S** single | только нормализованное действие | CARE, RedCode-Exec; fallback без контекста |
| **H** hybrid | действие + `[TASK]` + `[HISTORY]` (диалог и предыдущие действия с вердиктами) + `tainted` | основной режим ступени 2; датасет кейса; taint-атаки |
| **T** trajectory | вся запись эпизода | R-Judge, ATBench, ATBench-CodeX, ASSE-Safety, SABER |

Ответ: первый токен `A | D | U` (для T: A=safe, D=unsafe), затем `risk_type` и `reason` в одно предложение. В рантайме читаются только логиты первого токена; пороги `τ_A`, `τ_D` подбираются на dev под FPR ≤ 1 % (сервер) / ≤ 0,5 % (локально).

Шаблон промпта, схема JSONL и правила разметки — `docs/agent-gate-sft-plan.md` §1–2.

**Датасет (~12–15 тыс. S/H, 2,5–6,5 тыс. T):** shell-safety (HF, ~5k, лицензию проверить) · RedCode-Exec bash train (~800) · benign-логи с Terminal-Bench и свои сессии (~2k) · синтетика: контрастные пары (~2k), авторизация пользователем (~500), обфускация (~1k) · AgentDoG1.0-Training-Data (1–5k, лицензия `other`) · свои кодинг-эпизоды с многошаговыми атаками (~1.5k) · производные S/H нарезкой T-эпизодов (+3–4k).

**Разметка:** большая модель → сверка со ступенью 1 → второе мнение на 20 % → ручная очередь расхождений + 5 % случайных. Поле `label_origin` сохраняется для абляции.

**Обучение на 2×H200:** LoRA r=32 на Qwen3.5-0.8B / 2B / 4B (+9B одна точка), 3 эпохи, лосс только на ответе, смесь S:H:T ≈ 1:2:1, 3 сида для финальных. Дополнительно: Qwen3.5-2B-Base, warm start от AgentDoG1.5-0.8B/2B, Qwen3-1.7B (запасной CPU-вариант, если llama.cpp не тянет Gated DeltaNet у Qwen3.5), ModernBERT-large как нижняя граница latency. Абляции: без `reason`, без контекста, без T-данных, без производных, без `tainted`, 2 класса, только согласованные метки.

---

## 6. Бенчмарк

**Тест-сеты:** CARE main + OOD + obfuscation (F1, DR, FPR, latency) · RedCode-Exec bash held-out (ASR, RR) · датасет кейса (все 5 метрик) · свои многошаговые taint-атаки 50–100 (step recall, episode ASR) · Terminal-Bench 20–30 задач (Utility, FP, Friction) · R-Judge, ASSE-Safety, ATBench, ATBench-CodeX, ATBench-Claw, SABER (accuracy/F1 как у AgentDoG) · DepScope Hallucinations + PackageHallucination (recall / FP на топ-5000).

**Лестница бейзлайнов:** B0 Kilo `ask` на всё · B1 `allow-everything` · B2 статические правила (дефолты Claude Code + prefix-правила Codex) · B3 облачный zero-shot классификатор с промптом в духе Claude Code auto mode · B4-s1 / B4-s12 / B4-full (абляции ступеней).

**Соперники модели:** CARE, bash-classify, sh-guard · AgentDoG 1.0/1.5 (0.8B–8B), Qwen3Guard-Gen 0.6/4/8B, Llama Guard 4 12B, Granite Guardian 3.3 8B, gpt-oss-safeguard-20B, Nemotron Safety Guard 8B v3, ShieldGemma 2B, WildGuard 7B · Qwen3.5 без SFT · Qwen3.8-27B, Qwen3.6-35B-A3B · Claude Sonnet 4.6 / GPT-5.x / Gemini 2.5 по API · люди (лог `ask` из B0, время реакции < 1,5 с на команду > 40 символов = слепое одобрение).

**Критерий успеха:** Qwen3.5-2B после SFT ≥ AgentDoG-8B по F1 на R-Judge и ATBench-CodeX; > CARE по F1 при FPR ≤ 1 %; ASR на taint-атаках ниже любого zero-shot; latency p50 ≤ 30 мс на GPU. Отрицательный результат публикуется с разбором.

**Test → Protect:** каждая пропущенная атака → предложение детерминированного правила ступени 1 (генерирует ступень 3, подтверждает человек в панели, проверяется на benign-наборе: FP > 0,5 п.п. → правило помечается спорным).

---

## 7. План на четыре дня

Роли: **A** — данные и модель; **B** — сервис, адаптеры, бенчмарк-харнесс.

| День | A | B |
|---|---|---|
| 1 | Лицензии, скачать тест-сеты, зафиксировать хэши. S-часть из shell-safety + RedCode. Запустить генерацию контрастных пар и обфускации. Zero-shot бейзлайны (Qwen3.5, AgentDoG, Qwen3Guard) на R-Judge/CARE — знать планку к вечеру. | Сервис `/v1/decide` + `/v1/observe`, нормализатор, ступень 1. Kilo-адаптер. Сбор benign-логов на Terminal-Bench. Бенчмарк-харнесс: контейнер, фикстуры, подставной интернет и реестр. |
| 2 | Нарезка T → S/H. Разметка и ручная очередь (оба, 2–3 ч). Первый LoRA 0.8B и 2B. | Ступень 2 в сервисе (vLLM + llama.cpp). Claude Code-адаптер. 50–100 многошаговых атак в харнессе. Прогон B0/B1/B2 на датасете кейса. |
| 3 | Полная сетка и абляции. Пороги на dev. | Прогон B3/B4 на всех сетах, таблица с CI. Codex-адаптер (если время). Test → Protect. Панель. |
| 4 | Три сида, финальная таблица, разбор ошибок, экспорт GGUF. | Демо end-to-end, README (модель угроз, гарантии, ограничения), PR в Kilo. |

Точки синхронизации: конец дня 1 — формат `Action` и промпт формы H согласованы; конец дня 2 — первая модель стоит в сервисе; конец дня 3 — таблица результатов и решение, что показывать.

---

## 8. Правила работы для Claude Code в этом репозитории

- Прежде чем менять API, схему `Action`/`Decision`, схему JSONL или промпт формы H — обнови соответствующий раздел в `docs/` и этот файл. Эти четыре контракта связывают работу A и B.
- Любой код, принимающий решение `allow`, должен иметь тест на путь отказа: ошибка модели, таймаут, невалидный ответ, недоступный сервис → `ask`. PR без такого теста не мержится.
- Hard-deny правила лежат в `service/agentgate/stage1/hard_deny.py` и покрыты табличными тестами (команда → ожидаемое решение), включая обфусцированные варианты (builtins, `$(...)`, `eval`, base64, переменные окружения).
- В `data/build/` каждый скрипт идемпотентен и пишет `label_origin`. Перед записью в `data/sft/` запускается `dedup.py`, который сверяет хэши с `data/test_hashes/` и удаляет совпадения. Никаких исключений.
- Нормализация shell — только через AST. Решение по сырой строке запрещено.
- Тексты tool outputs не попадают в промпт ступени 2. Если нужно передать больше — это флаг или хэш, не текст.
- Latency ступеней 1–2 измеряется в тестах; регрессия p50 выше бюджета (1 мс / 100 мс) блокирует PR.
- Все числа в README и панели должны воспроизводиться командой `agentgate bench run --suite <name> --config <B*>` из чистого контейнера.
- Секреты и токены — только через переменные окружения; фикстуры бенчмарка используют подставные значения.
- Язык кода и комментариев — английский; документация и README — русский; идентификаторы API не переводятся.

---

## 9. Ключевые внешние артефакты

| Что | Ссылка |
|---|---|
| Claude Code Auto Mode — анонс и конфигурация | https://claude.com/blog/auto-mode-default-in-claude-code · https://code.claude.com/docs/en/auto-mode-config |
| Codex Auto-review — документация и OSS-политика ревьюера | https://developers.openai.com/codex/concepts/sandboxing/auto-review · https://github.com/openai/codex/blob/main/codex-rs/core/src/guardian/policy.md |
| Обход Claude Code Auto Mode (Rehberger) — эталонный сценарий taint-атаки | https://embracethered.com/blog/posts/2026/breaking-claude-code-opus-5-and-automode/ |
| Kilo Code — репозиторий и issue по auto mode | https://github.com/Kilo-Org/kilocode · https://github.com/Kilo-Org/kilocode/issues/9138 · #10248 · #10249 |
| `@anthropic-ai/sandbox-runtime` | https://code.claude.com/docs/en/sandboxing |
| CARE (ISSRE 2026) | https://arxiv.org/abs/2607.21642 · https://github.com/prisma-research/CARE |
| AgentDoG — модели, данные, ATBench | https://github.com/AI45Lab/AgentDoG · https://huggingface.co/datasets/AI45Research/AgentDoG1.0-Training-Data · https://huggingface.co/datasets/AI45Research/ATBench-CodeX |
| R-Judge | https://arxiv.org/abs/2401.10019 |
| RedCode | https://github.com/AI-secure/RedCode |
| shell-safety (SFT-сырьё) | https://huggingface.co/datasets/tomngdev/shell-safety |
| SABER | https://huggingface.co/datasets/sssr-lab/SABER |
| AgentDojo / InjecAgent / MCPTox (шаблоны инъекций) | https://github.com/ethz-spylab/agentdojo · https://arxiv.org/abs/2403.02691 · https://arxiv.org/abs/2508.14925 |
| DepScope — MCP и датасет галлюцинаций | https://depscope.dev · https://github.com/cuttalo/depscope-mcp · https://github.com/cuttalo/depscope-hallucinations-dataset |
| PackageHallucination (Spracklen et al., USENIX 2025) | https://github.com/Spracks/PackageHallucination |
| Вредоносные пакеты (эвристики) | https://github.com/DataDog/malicious-software-packages-dataset · https://github.com/ossf/malicious-packages · https://github.com/DataDog/guarddog |
| Terminal-Bench (utility) | https://www.tbench.ai/ |
| Qwen3.5 Small (0.8B/2B/4B/9B) | https://huggingface.co/collections/unsloth/qwen35 |
| OWASP Agentic Top 10 (2026) — сетка для модели угроз | https://genai.owasp.org/resource/owasp-top-10-for-agentic-applications-for-2026/ |

Полный список — `docs/agent-gate-artifacts.md`.

---

## 10. Проверить в первый час

1. Лицензии `tomngdev/shell-safety` и `AgentDoG1.0-Training-Data` (`other`); при запрете — T-часть только из своей синтетики.
2. Есть ли у CARE отдельный benign pool и как лицензирован.
3. Средняя длина траектории в ATBench-CodeX / R-Judge в токенах (влияет на `max_len` формы T).
4. Работает ли Qwen3.5-0.8B в llama.cpp (Gated DeltaNet); иначе CPU-вариант на Qwen3-1.7B.
5. Веса AgentDoG 1.5 доступны для warm start и их лицензия.
6. DepScope API отвечает; иначе прямые запросы к `registry.npmjs.org` / `pypi.org` + локальный кэш топ-5000.
7. Датасет кейса от кейсодателя получен и захэширован в `data/test_hashes/`.