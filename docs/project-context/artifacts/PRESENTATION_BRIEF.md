# AgentGate — content brief / storyboard промежуточной презентации

**Формат:** 11 слайдов  
**Статус:** Intermediate / Work in Progress  
**Назначение:** source of truth для AI-инструмента, который создаст визуальную презентацию. Это не технический отчёт и не готовый deck.

## Сквозная история

Автономные coding agents ценны, потому что действуют без постоянного участия человека → постоянные подтверждения уничтожают эту ценность, но полный bypass создаёт риск → значит, задача не в максимальном блокировании → нужно одновременно снижать unsafe action pass-through и вмешательство человека → AgentGate выносит решение за пределы harness и проверяет действие до исполнения → очевидные случаи решает дешёвая детерминированная Stage 1, остаток — LLM Stage 2 → decision core и benchmark toolchain уже построены → теперь систему нужно проверить через реальный harness и live benchmark.

## Правила для всего deck

- На каждом слайде — одна главная мысль, крупный headline, одна визуальная структура и не более 2–5 supporting facts.
- Основной язык — русский. Естественные технические названия оставить на английском: `runtime decision layer`, `harness`, `allow / deny / ask`, `benchmark`, `Stage 1 / Stage 2`, `fail-closed`, `friction`, `safe continuation`, `tool call`.
- Визуально различать статусы: **solid / насыщенный** = Implemented + Tested; **полосатый / янтарный** = Partial; **dashed / серый** = Target / Not yet validated.
- Нигде не изображать AgentGate как законченный или product-validated продукт. Всегда различать component testing и live system validation.
- Не строить графики с вымышленными процентами. Разрешены только подтверждённые числа: 75 cases, 15 categories, 5 difficulty levels, 122 passed offline benchmark tests, 0 dataset validation errors, Stage 1 unit budget p50 ≤ 1 ms.
- Стиль: minimal, modern, technical, restrained; светлый или очень тёмный нейтральный фон, один холодный accent color и янтарный цвет для gaps; крупная типографика, много whitespace, тонкие линии, простые геометрические формы.
- Не использовать фотографии, stock imagery, роботов, мозги, неоновых hackers, щиты и замки, generic AI illustrations.
- Нижний служебный маркер на слайдах 1, 6–11: **Intermediate / Work in Progress**.

---

## Slide 1 — AgentGate

### Core message

AgentGate — внешний runtime decision layer для безопасной автономии AI coding agents; проект находится на промежуточной стадии.

### On-slide copy

**AgentGate**

**Safe autonomy for AI coding agents**

`Intermediate / Work in Progress`

Александр Иванов · Алексей Балашов · Тимур Полищук

### Visual

Минималистичный титульный слайд. Крупное название в левом верхнем или центральном блоке. Под ним — одна тонкая линия-поток из трёх узлов: `Agent → AgentGate → Action`; средний узел выделен accent color. Никаких продуктовых claims, метрик или декоративных иллюстраций.

### Speaker notes

AgentGate проверяет предложенное действие агента до исполнения и возвращает `allow`, `deny` или `ask`. Это промежуточный checkpoint: decision core и evaluation tooling реализованы, но реальная интеграция с harness и live validation ещё впереди.

### Evidence / source

- `docs/project-context/artifacts/PRODUCT_INTERIM.md` — вводная формулировка продукта и статус Work in Progress.
- `docs/project-context/artifacts/PROJECT_INTERIM.md` — Project Summary и Current Project Status.
- `docs/project-context/07_team.md` — состав команды.

---

## Slide 2 — Автономность против контроля

### Core message

Чем больше автономности получает coding agent, тем хуже бинарная permission-модель балансирует продуктивность и реальный риск.

### On-slide copy

**Автономность против контроля**

**Больше подтверждений**  
`human confirmations ↑ → friction ↑ → autonomy ↓`

**Меньше подтверждений**  
`human confirmations ↓ → autonomy ↑ → uncontrolled risk ↑`

Небольшой evidence-блок:

`n = 1 053` · `93%` показанных prompts были одобрены · явно опасную подменённую команду заблокировали только в `13,6%` случаев

**Текущий контроль регулирует усилие человека, а не реальный риск действия.**

### Visual

Двусторонние весы или V-образная trade-off diagram без декоративной метафорики. Слева — поток «подтверждать всё» с ростом friction и падением autonomy. Справа — «bypass» с ростом autonomy и uncontrolled risk. В центре — пустой разрыв, который должен закрыть risk-aware decision layer. Evidence-блок показать как небольшой источник, не как главный график.

### Speaker notes

Ценность автономного агента — в способности действовать без постоянного участия человека. Но существующий бинарный выбор заставляет либо проверять каждый шаг, либо отключать контроль. Исследование, процитированное в Product Interim, показывает, что дорогое ручное подтверждение само по себе является слабым детектором и ухудшается по мере сессии. Пользовательских исследований самой команды пока не было.

### Evidence / source

- `docs/project-context/artifacts/PRODUCT_INTERIM.md`, §1 “Target User & Problem” — конфликт autonomy / friction / risk и данные blind study Anthropic.
- `docs/project-context/08_product_decisions.md`, §2–3 — user problem и current product insight.

---

## Slide 3 — Оптимизировать риск и friction вместе

### Core message

Security здесь — не максимум блокировок, а минимум вмешательства человека при низком unsafe action pass-through.

### On-slide copy

**Security ≠ maximum blocking**

Центральный вопрос:

**Можно ли снизить количество решений, требующих человека, не увеличив долю пропущенных атак?**

**Safety**  
`unsafe action pass-through`  
Сколько атак получили `allow`?

**Friction**  
`benign deny + benign ask`  
Сколько легитимных действий были остановлены или отданы человеку?

**`ask` на benign action = friction, а не success**

`Live measurement: pending`

### Visual

Две равноправные измеряемые оси или 2×2 matrix: вертикальная — unsafe pass-through, горизонтальная — human intervention on benign work. Целевая зона — lower-left: мало пропусков и мало friction. Не ставить точку AgentGate и не рисовать текущие значения; вместо неё — пунктирный контур «to be measured in live benchmark».

### Speaker notes

Система, всегда отвечающая `deny`, может формально блокировать все атаки, но уничтожает автономность. Аналогично, `ask` на безопасном действии — это возврат к ручной permission-модели. Поэтому гипотеза проверяется только парой показателей: безопасность на attack cases и friction на benign controls. Текущих процентов нет.

### Evidence / source

- `docs/project-context/artifacts/PRODUCT_INTERIM.md`, §2 “Product Insight & Hypothesis”, §3 “Value Proposition”, §7 “Preliminary Success Criteria”.
- `docs/project-context/08_product_decisions.md`, §4 и §9 — product hypothesis и success criteria.
- `docs/project-context/06_benchmark_status.md`, “Benign scenarios” и “Metrics”.

---

## Slide 4 — Слой решения между агентом и исполнением

### Core message

AgentGate выносит risk decision за пределы harness и должен оценивать каждое действие до его фактического исполнения.

### On-slide copy

**A decision layer between the agent and execution**

`Пользователь`  
→ `AI Agent / Harness`  
→ `Pre-execution interception`  
→ **`AgentGate`**  
→ `allow / deny / ask`  
→ `Action`

Обратная ветка:

`deny → reason + safe alternative → Agent`

Ветка человека:

`ask → Human decision`

Небольшая подпись: **Selected product approach — не схема текущей готовности**

### Visual

Одна простая горизонтальная product-level flow diagram. AgentGate выделен цветом. От `deny` сделать петлю обратно к агенту с `reason + safe alternative`; от `ask` — короткую ветку к человеку. Не показывать внутренности Stage 1 / Stage 2 и не добавлять будущие компоненты. Точку `Pre-execution interception` пометить маленьким янтарным маркером «integration gap», не разрушая основной flow.

### Speaker notes

Сервис сам не исполняет команды и физически не может остановить обход harness. Продукт существует только тогда, когда harness вызывает AgentGate перед relevant tool call и применяет ответ. Контракт такого взаимодействия есть, production adapter — ещё нет. Ветка safe continuation пока является требуемым сценарием, а не доказанным end-to-end поведением.

### Evidence / source

- `docs/project-context/artifacts/PRODUCT_INTERIM.md`, §4 “Selected Product Approach”.
- `docs/project-context/artifacts/PROJECT_INTERIM.md`, §1–3 и §6 — роль harness, сервиса и текущий flow.
- `docs/project-context/05_current_state.md`, “Current end-to-end flow”.

---

## Slide 5 — Сначала детерминированное, затем LLM

### Core message

Дешёвые и предсказуемые проверки должны закрывать очевидные случаи, а LLM используется только для нерешённого остатка.

### On-slide copy

**Do the deterministic work first**

`Proposed action`  
↓  
`Structural normalization`  
↓  
**`Stage 1 — deterministic`**  
↓ `unresolved only`  
**`Stage 2 — LLM`**  
↓  
`Session escalation`  
↓  
**`ALLOW / DENY / ASK`**  
↓  
`Audit`

Три коротких callout:

- Rules оценивают структуру действия, а не reasoning агента.
- LLM получает только нерешённый остаток.
- Ошибка или неопределённость → `ask`, не `allow`.

### Visual

Вертикальный pipeline с двумя визуально разными вычислительными зонами. Stage 1 — компактный solid-блок с подписью `cheap · deterministic · no LLM`. Stage 2 — более узкий блок после funnel `unresolved only`. Session escalation объединяет ветви перед verdict. Audit показать боковой append-only дорожкой от verdict, а не отдельной стадией принятия решения.

### Speaker notes

Сначала action переводится из сырого tool call в структурное представление: команды, пути, домены и другие аргументы. Stage 1 применяет hard-deny, profile checks и allowlist без обращения к модели. Только если решения нет, вызывается Stage 2. Сессионная логика ограничивает повторяющиеся risky decisions. Fail-closed подтверждён для decision service, но ещё не доказан для всей системы с реальным harness.

### Evidence / source

- `docs/project-context/artifacts/PROJECT_INTERIM.md`, §3 “Selected Technical Approach”.
- `docs/project-context/artifacts/PRODUCT_INTERIM.md`, §3–4 — technical enabler и product approach.
- `docs/project-context/04_architecture/agentgate_v1_design_snapshot.md` — current v1 pipeline.

---

## Slide 6 — Ядро реализовано, enforcement ещё нет

### Core message

Decision core и benchmark toolchain уже реализованы и протестированы, но без production harness adapter система пока не контролирует реального агента.

### On-slide copy

**Implemented core, missing real harness enforcement**

**Implemented + Tested**

- Decision API
- Action normalization
- Deterministic Stage 1
- LLM Stage 2
- Session escalation + allow cache
- Audit / persistence
- Benchmark toolchain

**Partial**

- Harness contract
- Reference hook client
- `deny` response: `reason + suggest`

**Missing / Not yet validated**

- Production harness adapter
- Real `ask` flow and enforcement
- `deny → safe continuation`
- Live benchmark and product metrics

Нижний статус-блок:

**Главный integration gap: AgentGate возвращает решение, но реальный harness ещё не доказал его применение.**

### Visual

Три вертикальные status cards одинаковой ширины: зелёно-синий `Implemented + Tested`, янтарный `Partial`, серый dashed `Missing / Not yet validated`. Самый крупный элемент — нижняя полоса с главным integration gap. Не использовать progress bars или проценты готовности.

### Speaker notes

Важно разделять component status и product validation. Stage 2 реализована и тестировалась с mock/fake provider, но не проверена live benchmark. Reference hook client принимает payload двух форм harness и вызывает API, но это диагностический мост, а не установленная production integration. Поля `reason` и `suggest` существуют, но продолжение агента после отказа не реализовано end-to-end.

### Evidence / source

- `docs/project-context/artifacts/PROJECT_INTERIM.md`, §4–7 и §14.
- `docs/project-context/artifacts/PRODUCT_INTERIM.md`, §5 “MVP & Current State”.
- `docs/project-context/05_current_state.md`, “Implemented”, “Partially implemented”, “Known limitations”.

---

## Slide 7 — Current MVP ≠ Target architecture

### Core message

Текущий MVP реализует нижний decision contour; богатый контекст, provenance и recovery остаются элементами target architecture.

### On-slide copy

**Current MVP ≠ Target architecture**

**Current MVP**

- Interception contract + reference client **(Partial)**
- Decision Service
- Structural normalization
- Stage 1 + Stage 2
- Session counters + allow cache
- Audit
- Benchmark toolchain

**Target / Later**

- Context Guard
- Dialogue history
- Tool-result provenance
- Richer session risk state
- Richer safe recovery + human loop
- Package intelligence

Финальная строка:

**Мы сознательно разделяем то, что существует сейчас, и то, что относится к target architecture.**

### Visual

Слева — compact architecture current MVP с solid-блоками; `Interception contract + reference client` показать полосатым как Partial. Справа — расширяющийся target-контур с dashed-блоками. Между ними — направленная стрелка `validation + iteration`, а не знак равенства. Не копировать целевую схему целиком и не соединять future-блоки так, будто они участвуют в текущем runtime.

### Speaker notes

Сегодня сервис принимает одно действие и последний запрос пользователя. В target architecture до и после решения появляются Context Guard, history, tool-result provenance, более богатое session state, отдельная safe recovery и полноценный human loop. Package intelligence сейчас представлена только пустым слотом. Target — направление развития, не обещание к финалу и не описание работающей системы.

### Evidence / source

- `docs/project-context/artifacts/PROJECT_INTERIM.md`, §4 “Target Architecture vs Current Architecture”.
- `docs/project-context/04_architecture/architecture_description.md` — target components и roadmap v2/v3/v4.
- `docs/project-context/04_architecture/target_architecture.jpg` — визуальный target reference.
- `docs/project-context/04_architecture/agentgate_v1_design_snapshot.md` — current v1 snapshot.
- `docs/project-context/05_current_state.md`, “Target architecture components not yet implemented”.

---

## Slide 8 — Benchmark измеряет safety и friction вместе

### Core message

Evaluation framework проверяет не только пропуски атак, но и цену защиты для легитимной работы.

### On-slide copy

**Benchmark safety and friction together**

Крупный metrics strip:

**75 cases** · **15 categories** · **5 difficulty levels**

**Attack cases — 70**

- attack pass-through
- detection failures

**Benign controls — 5**

- benign `deny`
- benign `ask`
- `allow` rate

**Performance — planned live outputs**

- latency + stage distribution
- cost, where measurable

Два обязательных status stamp:

**Benchmark tool: IMPLEMENTED + TESTED**

**Live AgentGate benchmark: NOT RUN YET**

### Visual

Вверху — три крупных validated number tiles `75 / 15 / 5`. Ниже — симметричные чаши `Attack cases` и `Benign controls`, которые сходятся в общий evaluation output. Performance вынести третьей тонкой дорожкой. Status stamps должны быть контрастнее списка метрик: первый solid, второй янтарный outline. Не показывать gauge, score или mock percentages.

### Speaker notes

Набор включает 70 атакующих кейсов и 5 benign controls. Последних пока мало для устойчивого вывода о false positives: один кейс меняет показатель на 20 percentage points, поэтому любые будущие выводы о friction потребуют осторожности и, желательно, расширения controls. Инструмент валидирует набор, вызывает API, детерминированно оценивает ответы и строит отчёт. Но ни один кейс ещё не прошёл через живой AgentGate с реальной Stage 2.

### Evidence / source

- `docs/project-context/artifacts/PROJECT_INTERIM.md`, §5.3 и §8 “Benchmark & Preliminary Results”.
- `docs/project-context/06_benchmark_status.md`, “Attack categories”, “Benign scenarios”, “Metrics”, “Implemented”, “Current results”.
- `docs/project-context/artifacts/PRODUCT_INTERIM.md`, §7 — разделение Safety / Friction / Performance.

---

## Slide 9 — Что можно доказать сегодня

### Core message

Пока есть инженерные доказательства работоспособности компонентов и evaluation tooling, но ещё нет продуктовых результатов живой системы.

### On-slide copy

**Engineering evidence so far**

**Можно подтвердить**

- Decision pipeline implemented + tested
- Benchmark offline tests: **122 passed**
- Dataset validation: **75 / 15 / 5 · 0 errors**
- Stage 1 unit budget: **p50 ≤ 1 ms**
- Reference-client / e2e wiring exists
- Current vs Target gaps documented

**Пока нельзя заявлять**

- ASR · FPR · FNR
- Utility · Friction
- Full-system latency · Cost
- Competitor benchmark results
- Cross-harness performance

Небольшая подпись под latency:

`Stage 1 unit measurement ≠ product latency`

### Visual

Две неравные колонки: слева 60% ширины — evidence cards с checkmark и реальными числами; справа 40% — outlined блок `Not measured yet`. Внутри левой колонки не объединять `122 passed` с test count decision service. Под Stage 1 числом обязательно показать scope label `unit`. Не называть слайд Results.

### Speaker notes

122 passed относятся только к офлайн-тестам benchmark toolchain. Валидатор подтвердил структуру датасета без ошибок. Stage 1 уложилась в unit latency budget на 200 прогонах, но это не включает сеть, Stage 2, harness и persistence. Reference client и controlled e2e wiring подтверждают контрактный путь, но не production integration. Техническая оговорка: текущий service test run имеет Windows-path failures; целевая среда — Linux container. Пользовательских исследований не проводилось. Ни один компонент сегодня не product-validated.

### Evidence / source

- `docs/project-context/artifacts/PROJECT_INTERIM.md`, §7–9 и §14.
- `docs/project-context/06_benchmark_status.md`, “Test status” и “Current results”.
- `docs/project-context/artifacts/PRODUCT_INTERIM.md`, вводная оговорка и §12 “Current Product Status”.

---

## Slide 10 — Что должно быть доказано до финала

### Core message

Следующий этап — превратить протестированные компоненты в проверенный end-to-end flow на реальном harness и живых измерениях.

### On-slide copy

**What must be proven next**

**Technical validation**

1. Установить один real harness adapter.
2. Подтвердить enforcement `allow / deny / ask` и system fail policy.
3. Провести `deny → reason/suggest → safe continuation → re-gate`.

**Product validation**

4. Запустить live benchmark и разобрать каждый failure.
5. Измерить full latency, stage distribution и cost where measurable.
6. Провести минимальную проверку friction на реальных developer sessions.

Риск-блок:

**Biggest current risk: integration, not decision-core implementation.**

### Visual

Две параллельные дорожки `Technical validation` и `Product validation`, каждая из трёх крупных milestones. Они сходятся в один outlined финальный узел `Evidence-backed final demo`. Главный риск показать отдельной янтарной полосой. Не использовать календарные даты и проценты готовности, если их нет в source of truth.

### Speaker notes

Production-like adapter должен не просто вызвать API, а перехватить каждый заявленный tool type и применить решение. Отдельно нужно определить поведение системы при недоступности AgentGate: service fail-closed ещё не означает system fail-closed. Первый live benchmark должен сохранить commit, profile, model и run metadata, после чего failures разбираются на dataset error, known v1 limitation и service miss. Метрики публикуются только после этого. Минимальная пользовательская проверка нужна, потому что сегодня реальных наблюдений нет.

### Evidence / source

- `docs/project-context/artifacts/PROJECT_INTERIM.md`, §10–12 — open questions, risks и work plan.
- `docs/project-context/artifacts/PRODUCT_INTERIM.md`, §8–9 — assumptions, risks и validation before final.
- `docs/project-context/08_product_decisions.md`, §11–12.
- `docs/project-context/06_benchmark_status.md`, “Before final”.

---

## Slide 11 — Команда и следующий milestone

### Core message

Три зоны ownership сходятся в одном следующем milestone: реальный harness, enforced decision и live benchmark.

### On-slide copy

**Команда AgentGate**

**Александр Иванов — AI Engineer**

- Decision service architecture
- Decision service implementation
- Server deployment
- Engineering happy path

`Full active participation`

**Алексей Балашов — AI Engineer**

- Agent input/output interception
- Harness integration + adapters
- Enforcement решений AgentGate

`Full active participation`

**Тимур Полищук — AI Product**

- Benchmark design + implementation
- Product-level target architecture
- Product requirements + metrics
- Competitive/product research
- Project artifacts + presentation

`Full active participation`

Нижняя объединяющая полоса:

**Next milestone**  
**Real harness → AgentGate → enforced decision → live benchmark**

### Visual

Три равноправные карточки участников без фотографий и аватаров. В каждой: имя, роль, 3–5 коротких ownership lines и одинаковый status chip `Full active participation`. Внизу — одна широкая flow-полоса следующего milestone, визуально связывающая все три карточки. Не изображать зоны как изолированные команды: добавить тонкие соединительные линии к общему milestone.

### Speaker notes

Архитектурные и продуктовые решения команда обсуждает совместно, но implementation ownership разделён: Александр владеет decision service и deployment, Алексей — interception и harness integration, Тимур — benchmark, evaluation и project artifacts. Все участники активно вовлечены. Завершаем не обещанием готового продукта, а конкретным направлением следующего доказательства.

### Evidence / source

- `docs/project-context/07_team.md` — роли, responsibility matrix, implementation ownership и current involvement.
- `docs/project-context/artifacts/PROJECT_INTERIM.md`, §12–14 — owners, текущий статус и next milestone.

---

## Финальная проверка перед визуальной генерацией

- Официальные обязательные блоки покрыты: problem — слайд 2; task definition / hypothesis — слайд 3; selected approach — слайды 4–5; solution architecture — слайды 4, 5 и 7; completed work — слайд 6; preliminary evidence / demo state — слайды 8–9; risks и plan — слайд 10; team — слайд 11.
- Deck содержит 11 слайдов и следует одной цепочке: autonomy → permission conflict → measurable hypothesis → decision layer → cascade → current status → target boundary → evaluation → evidence → next proof → owners.
- Live benchmark против AgentGate с реальной Stage 2 явно отмечен как **NOT RUN YET**.
- ASR, FPR, FNR, Utility, Friction, full-system latency, cost, competitor results и cross-harness performance не представлены как измеренные.
- Production harness adapter явно отсутствует; reference hook client не назван production integration.
- `deny → safe continuation` и реальный `ask` flow явно не подтверждены end-to-end.
- Пользовательские исследования явно не проводились.
- Target architecture визуально и текстово отделена от Current MVP.
- Ни один компонент не назван product-validated; весь deck сохраняет статус **Intermediate / Work in Progress**.
