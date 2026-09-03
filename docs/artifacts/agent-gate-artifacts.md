# AgentGate — артефакты: бенчмарки и датасеты для дообучения

*Сводка на 2 сентября 2026. Ссылки — только проверенные (открыты в ходе исследования) либо взятые из первого отчёта; непроверенные помечены. Термины поясняются в скобках.*

---

## 1. Бенчмарки

Порядок — по близости к нашей задаче (решение о разрешении конкретного действия кодинг-агента до исполнения).

| Бенчмарк | Ссылка | На что нацелен | Формат / метрики | Как используем |
|---|---|---|---|---|
| **CARE** (ISSRE 2026) | [arXiv 2607.21642](https://arxiv.org/abs/2607.21642) · [GitHub prisma-research/CARE](https://github.com/prisma-research/CARE) | Верификация одиночных shell-команд до исполнения: канонизация, детерминированные признаки, LLM-судья только для пограничных | Balanced main split 549 команд, 12 бейзлайнов; F1, DR, FPR, latency; OOD-корпуса опасных команд, obfuscation suite, Docker-исполнение на RedCode-gen | Прямой конкурент и одновременно тест-сет ступени 1; заявленные 85,64 % F1 при 0,91 % FPR и 2,32 мс — планка для B4-s1 |
| **SABER** | [arXiv 2606.01317](https://arxiv.org/abs/2606.01317) · [HF sssr-lab/SABER](https://huggingface.co/datasets/sssr-lab/SABER) | Операционная безопасность кодинг-агентов в stateful-воркспейсах (shell, файлы, состояние проекта); EN + ZH | 1K–10K записей, Apache-2.0 | Тест-сет для многошаговых сценариев в реальном репозитории |
| **ATBench-CodeX** | [HF AI45Research/ATBench-CodeX](https://huggingface.co/datasets/AI45Research/ATBench-CodeX) · [arXiv 2604.14858](https://arxiv.org/abs/2604.14858) | Траектории Codex-агента с точкой, где решение о безопасности нужно принять до shell, мутации workspace, MCP-вызова | Полные траектории, бинарный вердикт + таксономия, поле `reason` | Тест на trajectory-level: сравнить наше решение на каждом шаге с разметкой |
| **ATBench / ATBench500 / ATBench-Claw** | [HF AI45Research/ATBench](https://huggingface.co/datasets/AI45Research/ATBench) · [GitHub LiYu0524/ATbench](https://github.com/LiYu0524/ATbench) | Семейство trajectory-level бенчмарков безопасности агентов; 1 000 траекторий (500 в legacy), 1 575 инструментов; Claw-версия — 500 траекторий OpenClaw | Бинарный вердикт + трёхмерная таксономия; для safe-траекторий размечен `defense_type` | Тест-сет; таксономию можно взять за основу нашей модели угроз |
| **RedCode-Exec / RedCode-Gen** (NeurIPS 2024) | [GitHub AI-secure/RedCode](https://github.com/AI-secure/RedCode) | Опасное исполнение (Python + Bash, 4 050 кейсов) и генерация (160) кода агентами | ASR, Rejection Rate; Docker-исполнение | Bash-подмножество — ядро обучающей и тестовой выборки ступени 2 (с разбиением по хэшу) |
| **RedCodeAgent** | [arXiv 2510.02609](https://arxiv.org/abs/2510.02609) | Автоматический red-teaming кодинг-агентов | ASR, RR | Генератор новых атак для петли Test → Protect |
| **Command-line Risk Classification** | [arXiv 2412.01655](https://arxiv.org/abs/2412.01655) | Классификация CLI-команд на SAFE / RISKY / BLOCKED трансформером | Классификационный датасет команд | Схема меток совпадает с нашей `A/D/U`; проверить доступность данных |
| **AgentDojo** (NeurIPS 2024) | [GitHub ethz-spylab/agentdojo](https://github.com/ethz-spylab/agentdojo) | Косвенные prompt injection в tool-calling агентах | 97 задач, 629 security-тестов; ASR, Utility; исполняемая среда | Источник шаблонов инъекций; среда для RL (см. раздел 2.5) |
| **InjecAgent** | [arXiv 2403.02691](https://arxiv.org/abs/2403.02691) | Косвенные инъекции через tool output, single-step | 1 054 кейса; ASR | Шаблоны инъекций для вставки в README/tool output |
| **ASB — Agent Security Bench** | [GitHub agiresearch/ASB](https://github.com/agiresearch/ASB) | Атаки и защиты агентов: 400+ инструментов, 27 методов атак | ASR (до 84,3 %), 7 метрик | Дополнительные атаки на MCP/tool-слое |
| **Agent-SafetyBench** | [arXiv 2412.14470](https://arxiv.org/abs/2412.14470) | 349 сред, 2 000 тестов, безопасность поведения агентов | safety score | Широкий тест-сет; отбираем software-подмножество |
| **R-Judge** | [arXiv 2401.10019](https://arxiv.org/abs/2401.10019) | Оценка risk awareness по записи взаимодействия агента | 569 записей, 27 сценариев, 10 типов риска, ~53 % unsafe; бинарная метка + описание риска | Готовые trajectory-level SFT-пары (см. 2.2) и тест-сет |
| **AgentHarm** | [HF ai-safety-institute/AgentHarm](https://huggingface.co/datasets/ai-safety-institute/AgentHarm) · [arXiv 2410.09024](https://arxiv.org/abs/2410.09024) | Вредоносные задачи для агентов + harmless-контроль | 110 базовых / 440 с аугментациями; harm score, refusal | Контрастные пары «вредно / безвредно» для обучения |
| **OS-Harm** | [GitHub tml-epfl/os-harm](https://github.com/tml-epfl/os-harm) | Безопасность computer-use агентов, 150 задач | Unsafe rate, task completion | Дополнительный тест на действия с ОС |
| **SafeArena** | [safearena.github.io](https://safearena.github.io) | Веб-агенты, 500 задач | Completion of harmful | Второстепенный (веб) |
| **TRAP** (ICLR 2026) | [arXiv 2512.23128](https://arxiv.org/abs/2512.23128) | Persuasion-инъекции для веб-агентов; источник цифр 13 %/43 % из кейса | ASR, Utility | Цитируем в модели угроз; для кодинг-агентов не переносим напрямую |
| **MCPTox** | [arXiv 2508.14925](https://arxiv.org/abs/2508.14925) | Tool poisoning на 45 живых MCP-серверах, 1 312 тестов | ASR | Атаки через описания MCP-инструментов для `/v1/observe` |
| **MCP-SafetyBench** | [arXiv 2512.15163](https://arxiv.org/abs/2512.15163) | 20 типов MCP-атак, 5 доменов | ASR | То же |
| **CyberSecEval 1/2/3** (Meta) | [GitHub meta-llama/PurpleLlama](https://github.com/meta-llama/PurpleLlama) | Insecure code, prompt injection, offensive-задачи | vuln rate, FRR | Проверка, что ступень 2 не блокирует легитимную security-работу |
| **SecCodePLT** | [arXiv 2410.11096](https://arxiv.org/abs/2410.11096) | Генерация безопасного кода, 1 345 сэмплов, 27 CWE | secure-gen score | Второстепенный |
| **ToolEmu** (ICLR 2024) | через [R-Judge](https://arxiv.org/abs/2401.10019) (81 сэмпл включён) | Риски tool-use в LLM-эмулированном сэндбоксе, 144 кейса | safety score | Второстепенный |
| **Terminal-Bench** | [tbench.ai](https://www.tbench.ai/) · [HF paper 2601.11868](https://huggingface.co/papers/2601.11868) | Utility: сложные реальные задачи в терминале с тестами | pass rate | Источник легитимного трафика для Utility / FP / Friction |
| **DepScope LLM Hallucination Benchmark** | [depscope.dev](https://depscope.dev) | Доля установки галлюцинированных пакетов агентом (baseline 87 %) | install rate | Метрика для модуля пакетов |

Непроверенные в этой сессии, из первого отчёта: SecureAgentBench (arXiv 2509.22097), SEC-bench (2506.11791), SecRepoBench (2504.21205), JAWS-Bench (2510.01359), ASTRA (2508.03936), AgentDyn (2602.03117), WASP, SHADE-Arena, OpenAgentSafety, BIPIA.

---

## 2. Датасеты и среды для дообучения

### 2.1. Уровень действия: одиночная команда → метка (SFT для ступени 2)

Ровно наш формат входа: `[TASK] [PROFILE] [ACTION] → A/D/U`. Всё ниже требует переразметки под три класса и добавления контекста задачи.

| Датасет | Ссылка | Что внутри | Лицензия | Как используем |
|---|---|---|---|---|
| **tomngdev/shell-safety** и **shell-safety-common** | [HF tomngdev/shell-safety](https://huggingface.co/datasets/tomngdev/shell-safety) · [HF shell-safety-common](https://huggingface.co/datasets/tomngdev/shell-safety-common) | 10K–100K чат-примеров: system-промпт «строгий модератор shell-команды», команда, метка SAFE/UNSAFE; уже в формате messages | не указана — проверить | Основа SFT-выборки после фильтрации дублей и добавления `[TASK]`/`[PROFILE]` |
| **RedCode-Exec, bash-подмножество** | [GitHub AI-secure/RedCode](https://github.com/AI-secure/RedCode) | Опасные команды с категориями риска + результат исполнения | по репозиторию | Положительный класс `D`; отбираем только bash |
| **CARE main split + OOD-корпуса** | [GitHub prisma-research/CARE](https://github.com/prisma-research/CARE) | 549 команд балансированного сплита, obfuscation suite, disjoint benign pool | MIT (код); данные — проверить | Только тест; в обучение не берём, чтобы честно сравниться |
| **bash-classify command database** | [PyPI bash-classify](https://pypi.org/project/bash-classify/) | 150+ команд с классами READONLY / LOCAL_EFFECTS / EXTERNAL_EFFECTS / DANGEROUS и риском LOW/MEDIUM/HIGH; tree-sitter | по репозиторию | Готовые правила для ступени 1 (safe-allowlist) и слабая разметка для синтетики |
| **sh-guard rules** | [snapcraft sh-guard](https://snapcraft.io/install/sh-guard/kde-neon) | 157 правил команд, 51 правило путей, 25 паттернов инъекций, 61 GTFOBins; taint по пайпам; MITRE ATT&CK | проверить | Правила для ступени 1 и генерация негативных примеров (exfil через пайп) |
| **paiml/shell-safety-classifier (bashrs corpus)** | [HF paiml/shell-safety-classifier](https://huggingface.co/paiml/shell-safety-classifier) | 17 942 сэмпла, 5 классов качества скрипта (safe / needs-quoting / …) | MIT | Второстепенно: примеры «плохо написано, но безвредно» для снижения FP |
| **SandboxLM (sivang)** | [HF sivang/sandboxlm](https://huggingface.co/sivang/sandboxlm) | GPT-2, дообученный на синтетике safe/dangerous shell | GPL-3.0 | Только как референс идеи; данные не опубликованы |
| **Датасет кейса** | от кейсодателя | ~20 задач + ~20 атак | — | Только тест |

Синтетика (наша): генерируем большой моделью по таксономии кейса контрастные пары — одна и та же команда безопасна в одном `[TASK]/[PROFILE]` и опасна в другом (`rm -rf ./build` при задаче «почисти сборку» → `A`; при задаче «поправь README» → `U`). Целевой объём 500–1 000, обязательно с полем `tainted`.

### 2.2. Уровень траектории: запись взаимодействия → вердикт (SFT для ступени 3 / судьи бенчмарка)

| Датасет | Ссылка | Что внутри | Лицензия | Как используем |
|---|---|---|---|---|
| **AgentDoG1.0-Training-Data** | [HF AI45Research/AgentDoG1.0-Training-Data](https://huggingface.co/datasets/AI45Research/AgentDoG1.0-Training-Data) | 1K–10K траекторий для SFT guard-модели; таксономия рисков; тот же авторский коллектив, что ATBench | other — прочитать перед использованием | Главный кандидат для SFT ступени 3 и LLM-судьи бенчмарка; уже готовый формат «траектория → вердикт + причина» |
| **AgentDoG / AgentDoG 1.5 модели** | [GitHub AI45Lab/AgentDoG](https://github.com/AI45Lab/AgentDoG) · [HF AgentDoG1.5-Qwen3.5-2B](https://huggingface.co/AI45Research/AgentDoG1.5-Qwen3.5-2b) · [0.8B](https://huggingface.co/AI45Research/AgentDoG1.5-FG-Qwen3.5-0.8B) | Готовые guard-модели 0.8B / 2B / 4B / 7B / 8B на Qwen3.5 и Llama; обучены ~1k сэмплов; R-Judge 91,7, ATBench 87,4 (8B) | по репозиторию | Готовый бейзлайн B3-local: «взяли чужую guard-модель». Наш тезис — action-level модель быстрее и точнее на shell; либо берём 0.8B как стартовую точку для LoRA |
| **R-Judge** | [arXiv 2401.10019](https://arxiv.org/abs/2401.10019) | 569 записей с бинарной меткой и описанием риска; software/program-категории | по репозиторию | SFT-пары для судьи; ~150 software-кейсов в тест |
| **ATBench-CodeX / ATBench-Claw** | см. раздел 1 | Полные траектории Codex/OpenClaw с точкой решения | по карточке | Тест trajectory-level; авторы явно пишут «evaluation-oriented, not training corpus» |
| **clawdbot_safety_testing** | [HF tianyyuu/clawdbot_safety_testing](https://huggingface.co/datasets/tianyyuu/clawdbot_safety_testing) | 34 канонических кейса аудита OpenClaw с полными логами | проверить | Примеры «underspecified intent → опасное действие» для синтетики |
| **Fujitsu/agentic-rag-redteam-bench (ART-SafeBench v2)** | [HF Fujitsu/agentic-rag-redteam-bench](https://huggingface.co/datasets/Fujitsu/agentic-rag-redteam-bench) | 36K оригинальных + агрегированные InjecAgent (1 054), ToolEmu (144), AgentHarm (176), HarmBench, JailbreakBench, Gandalf | смешанные | Удобная агрегация; берём только B4 (Orchestrator) поверхность — tool-call атаки |

### 2.3. Шаблоны prompt injection (для `/v1/observe` и синтетики многошаговых атак)

| Источник | Ссылка | Что берём |
|---|---|---|
| AgentDojo | [GitHub](https://github.com/ethz-spylab/agentdojo) | Тексты инъекций (important_instructions и др.) для вставки в README / tool output / SKILL.md |
| InjecAgent | [arXiv 2403.02691](https://arxiv.org/abs/2403.02691) | 1 054 кейса инъекций через tool output |
| MCPTox | [arXiv 2508.14925](https://arxiv.org/abs/2508.14925) | Отравленные описания MCP-инструментов |
| Invariant mcp-injection-experiments | [GitHub](https://github.com/invariantlabs-ai/mcp-injection-experiments) | Реальные tool-poisoning / rug-pull примеры |
| Сводный gist по техникам | [gist kibotu](https://gist.github.com/kibotu/c06f54d6fbc4705e886a50fb2e59e6ae) | Каталог техник (невидимый Unicode, bidi, «ignore previous») для генерации |
| Rehberger — обход Claude Code Auto Mode | [Embrace The Red](https://embracethered.com/blog/posts/2026/breaking-claude-code-opus-5-and-automode/) | Готовая многошаговая цепочка (WebFetch → curl → ZIP → shadowing stdlib) как эталонный сценарий для теста provenance |

### 2.4. Supply chain / slopsquatting (для модуля пакетов)

| Датасет | Ссылка | Что внутри | Как используем |
|---|---|---|---|
| **DepScope Hallucinations Dataset** | [GitHub cuttalo/depscope-hallucinations-dataset](https://github.com/cuttalo/depscope-hallucinations-dataset) | 161 галлюцинированное имя из трафика агентов, 18 экосистем, ежедневная реверификация; CC-BY-NC-SA 4.0 | Позитивный класс для теста модуля пакетов; суффиксные паттерны → эвристики |
| **Spracklen et al. — PackageHallucination** | [GitHub Spracks/PackageHallucination](https://github.com/Spracks/PackageHallucination) · [arXiv 2406.10279](https://arxiv.org/abs/2406.10279) | 205 474 уникальных галлюцинированных имени из 576 000 генераций 16 LLM | Основной источник имён для тестов и для обучения детектора «похоже на галлюцинацию» |
| **ecosyste-ms/typosquatting-dataset** | [GitHub](https://github.com/ecosyste-ms/typosquatting-dataset) | CSV: вредоносное имя → целевой пакет, экосистема, техника тайпосквоттинга; агрегирует OpenSSF, Datadog, BKC | Пары для эвристики Левенштейна и генерации негативов |
| **OpenSSF malicious-packages** | [GitHub ossf/malicious-packages](https://github.com/ossf/malicious-packages) | Тысячи отчётов в формате OSV: PyPI, npm, crates, Go, Maven, NuGet, RubyGems, VSCode | Известные вредоносные имена (не для zero-day, но для проверки покрытия) |
| **DataDog/malicious-software-packages-dataset** | [GitHub](https://github.com/DataDog/malicious-software-packages-dataset) | 17 367 верифицированных вредоносных пакетов npm/PyPI, манифесты, разделение compromised/purpose-built | Метаданные для обучения эвристик (возраст, репозиторий, скрипты) |
| **pypi_malregistry** | [GitHub lxyeternal/pypi_malregistry](https://github.com/lxyeternal/pypi_malregistry) | ~9 500 вредоносных PyPI-пакетов | То же для PyPI |
| **Backstabber's Knife Collection** | [dasfreak.github.io](https://dasfreak.github.io/Backstabbers-Knife-Collection/) | 174+ реальных атак 2015–2019, доступ по академической почте | Референс по attack trees; для хакатона необязателен |
| **Datadog GuardDog** | [GitHub DataDog/guarddog](https://github.com/DataDog/guarddog) | Semgrep/YARA-правила + эвристики метаданных | Готовые эвристики для модуля пакетов |

### 2.5. RL-среды (если пойдём дальше SFT)

Честно: для ступени 2 на хакатоне достаточно SFT + порогов; RL имеет смысл для ступени 3 или для судьи. Что реально существует:

| Среда | Ссылка | Что даёт | Оценка применимости |
|---|---|---|---|
| **AgentDoG 1.5 agentic SFT + RL pipeline** | [GitHub AI45Lab/AgentDoG](https://github.com/AI45Lab/AgentDoG) | Заявлена среда для safety-aware агентного обучения, «10 000 параллельных сред на 8 ядрах», совместимая с их data engine | Ближайшее к готовому; проверить, опубликован ли код среды или только модели |
| **AgentDojo** | [GitHub ethz-spylab/agentdojo](https://github.com/ethz-spylab/agentdojo) | Исполняемая среда с задачами и инъекциями; reward = utility − security-провал | Легко завернуть в gym-подобный цикл для RL судьи; не про shell |
| **RedCode (Docker)** | [GitHub AI-secure/RedCode](https://github.com/AI-secure/RedCode) | Docker-исполнение опасного кода с проверкой состояния | Готовый «мир» для verifiable reward: вред случился / нет |
| **Terminal-Bench harness** | [tbench.ai](https://www.tbench.ai/) | Контейнеры с задачами и тестами | Utility-reward для RL, чтобы политика не научилась «блокировать всё» |
| **R2E-Gym / AgentGym-RL / verifiers (Prime Intellect) / SkyRL / NeMo Gym** | [awesome-agent-rl-environments](https://github.com/v01dmur10c/awesome-agent-rl-environments) · [Unsloth: RL environments](https://unsloth.ai/blog/rl-environments) | Инфраструктура multi-turn RL (GRPO, RLOO) и обёртки сред | Если делаем RL — берём `verifiers` как обёртку, RedCode + Terminal-Bench как среды, reward = −вред + utility − friction |
| **«Agent Safety Alignment via RL»** (ACL ARR 2026) | [OpenReview](https://openreview.net/forum?id=UL1rkhFVFW) | Единый фреймворк safety-alignment tool-агентов через sandboxed RL, две угрозы: от пользователя и от инструментов | Методологический референс для reward-дизайна |

### 2.6. Легитимный трафик (для Utility / FP / Friction)

| Источник | Ссылка | Как используем |
|---|---|---|
| Terminal-Bench задачи | [tbench.ai](https://www.tbench.ai/) | Прогон Kilo на 20–30 задачах через гейт → реальные безвредные действия |
| SWE-bench Verified | [awesome-agent-rl-environments](https://github.com/v01dmur10c/awesome-agent-rl-environments) (раздел Coding) | То же для правок репозитория |
| Собственные логи `/v1/decide` + `/v1/observe` за 4 дня | — | Самый честный источник; логируем с первого дня |
| CARE disjoint benign pool | [GitHub prisma-research/CARE](https://github.com/prisma-research/CARE) | Benign-команды для оценки FPR в тех же условиях, что у CARE |

---

## 3. Что из этого куда идёт

| Компонент AgentGate | Обучение | Тест |
|---|---|---|
| Ступень 1 (правила) | правила из bash-classify, sh-guard, GuardDog; правила из петли Test → Protect | CARE main split, RedCode-Exec bash, датасет кейса |
| Ступень 2 (локальная 1–2B, `A/D/U`) | tomngdev/shell-safety (после фильтрации) + RedCode-Exec bash train + наша синтетика с контрастными парами; стартовая точка — Qwen3-1.7B либо AgentDoG1.5-0.8B | CARE, датасет кейса, SABER, наши многошаговые траектории, реальные логи |
| Ступень 3 / судья бенчмарка | AgentDoG1.0-Training-Data + R-Judge (software) | ATBench-CodeX, R-Judge held-out |
| Модуль пакетов | эвристики по метаданным из DataDog/OpenSSF; имена из PackageHallucination | DepScope Hallucinations Dataset, DepScope LLM Hallucination Benchmark |
| `/v1/observe` (taint) | шаблоны AgentDojo/InjecAgent/MCPTox для синтетики | сценарий Rehberger, InjecAgent, наши многошаговые атаки |
| Бейзлайн B3-local | — | готовые AgentDoG 1.5 модели как «чужая guard-модель без дообучения» |

Контроль утечки: любой пример, попавший в обучение ступени 2, исключается из теста по хэшу нормализованного действия; CARE и датасет кейса в обучение не берём вообще.

---

## 4. Что проверить в первый час хакатона

1. Лицензии `tomngdev/shell-safety` и `AgentDoG1.0-Training-Data` (у второго — `other`).
2. Опубликован ли код RL-среды AgentDoG 1.5 или только модели.
3. Доступность данных Command-line Risk Classification (arXiv 2412.01655) — в сессии ссылка на датасет не найдена.
4. Формат CARE main split: можно ли выделить benign pool отдельно.
5. Актуальность DepScope API и датасета (проект новый, 0 звёзд).
