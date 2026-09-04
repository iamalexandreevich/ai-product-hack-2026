# Team

Status: Intermediate / Work in Progress

## Team overview

Команда проекта состоит из трёх участников:

* **Александр Иванов — AI Engineer**
* **Алексей Балашов — AI Engineer**
* **Тимур Полищук — AI Product**

Формальные роли отражают роли, заявленные в рамках хакатона, однако фактическая работа команды носит кросс-функциональный характер.

Архитектура продукта, ключевые технические и продуктовые решения, benchmark, пользовательские сценарии, критерии успеха и план развития обсуждаются и формируются совместно всей командой.

При этом за непосредственную реализацию отдельных технических компонентов закреплены конкретные зоны ответственности.

---

# Александр Иванов

**Declared role:** AI Engineer

**Functional role:** AI Engineer / Technical Architect / Project Coordination

## Primary responsibilities

* разработка архитектуры сервиса принятия решений;
* реализация центрального decision-making service;
* deployment и эксплуатационная готовность серверной части;
* участие в определении пользовательского happy path;
* координация последовательности технических работ;
* синхронизация архитектурных решений между различными частями системы;
* участие в продуктовых и архитектурных обсуждениях команды.

## Additional contribution

Александр обладает глубоким пониманием архитектуры ML/AI-систем и опытом участия в хакатонах.

В рамках текущего проекта он фактически выполняет также функцию **технического оркестратора и Project Manager**: помогает определять последовательность действий команды, согласовывать взаимозависимости между компонентами и удерживать техническую реализацию в рамках общего плана проекта.

Его роль не ограничивается разработкой decision server: Александр активно участвует в обсуждении общей архитектуры продукта, benchmark, интеграционных решений и требований к финальному end-to-end сценарию.

## Implementation ownership

**Decision-making service / backend infrastructure**

Александр является основным владельцем конкретной инженерной реализации серверного слоя.

## Current involvement

**Full active participation**

---

# Алексей Балашов

**Declared role:** AI Engineer

**Functional role:** AI Engineer / AI Systems & Integration Architecture

## Primary responsibilities

* разработка механизмов перехвата данных на входе AI-агента;
* разработка механизмов перехвата данных на выходе AI-агента;
* интеграция AgentGate с различными AI-agent harnesses;
* разработка integration layer между harness и decision-making service;
* определение способа применения решения AgentGate к фактическому выполнению действий агентом;
* участие в общей архитектуре AI-системы;
* участие в продуктовых и технических обсуждениях команды.

## Additional contribution

Алексей обладает хорошим пониманием архитектуры современных AI-agent systems и поэтому участвует не только в реализации integrations.

Он активно вовлечён в обсуждение:

* общей архитектуры AgentGate;
* состава контекста, необходимого для принятия решения;
* взаимодействия различных компонентов;
* ограничений существующих agent harnesses;
* архитектуры benchmark;
* технической реализуемости продуктовых гипотез.

## Implementation ownership

**Harness integration / interception layer**

Алексей является основным владельцем конкретной инженерной реализации слоя интеграции AgentGate с AI-agent harnesses.

## Current involvement

**Full active participation**

---

# Тимур Полищук

**Declared role:** AI Product

**Functional role:** AI Product / Product Architecture / Evaluation

## Primary responsibilities

* разработка benchmark для проверки AgentGate;
* определение структуры benchmark и необходимых метрик;
* разработка верхнеуровневой целевой архитектуры продукта без фиксации конкретного инженерного способа реализации;
* формализация пользовательской проблемы и продуктовых требований;
* определение критериев успешности решения;
* участие в проектировании пользовательского сценария;
* конкурентное и продуктовое исследование;
* создание необходимых промежуточных и финальных артефактов проекта;
* подготовка Markdown-документации;
* подготовка презентации и материалов для защиты.

## Additional contribution

Тимур отвечает за то, чтобы техническая реализация оставалась связана с исходной пользовательской проблемой и проверяемыми продуктовыми гипотезами.

Benchmark рассматривается не только как технический тест, но и как инструмент проверки продуктовых характеристик системы:

* security;
* false positives / false negatives;
* user friction;
* latency;
* cost;
* степень сохранения автономности AI-агента.

Тимур также участвует вместе с AI Engineers в обсуждении общей архитектуры системы и инженерных компромиссов.

## Implementation ownership

**Benchmark / evaluation tooling / project artifacts**

Тимур является основным владельцем конкретной реализации benchmark и подготовки проектных материалов.

## Current involvement

**Full active participation**

---

# Collaboration model

Команда не работает по модели, где каждый участник занимается исключительно своей изолированной частью проекта.

Большинство ключевых решений принимается совместно.

Совместно прорабатываются:

* продуктовая проблема;
* целевой пользователь;
* value proposition;
* общий пользовательский сценарий;
* верхнеуровневая архитектура AgentGate;
* архитектура взаимодействия компонентов;
* требования к decision-making service;
* требования к harness integrations;
* структура benchmark;
* критерии и метрики оценки;
* security / friction trade-off;
* ограничения MVP;
* технические компромиссы;
* риски;
* план развития проекта до финальной версии;
* сценарий демонстрации;
* содержание финальной защиты.

Разделение ответственности становится более жёстким преимущественно на уровне **конкретной реализации**.

---

# Implementation ownership

| Component                          | Primary implementation owner |
| ---------------------------------- | ---------------------------- |
| Decision-making service            | Александр Иванов             |
| Server architecture and deployment | Александр Иванов             |
| Harness interception layer         | Алексей Балашов              |
| Harness integrations               | Алексей Балашов              |
| Benchmark implementation           | Тимур Полищук                |
| Project artifacts and presentation | Тимур Полищук                |

Это распределение означает ответственность за непосредственную реализацию, но не исключает участия остальных членов команды в проектировании, review, обсуждении и принятии решений.

---

# Responsibility matrix

| Area                           | Александр Иванов       | Алексей Балашов | Тимур Полищук           |
| ------------------------------ | ---------------------- | --------------- | ----------------------- |
| Product problem definition     | Joint                  | Joint           | Joint / Product lead    |
| Product hypothesis             | Joint                  | Joint           | Joint / Product lead    |
| Target product architecture    | Joint                  | Joint           | Joint / Product lead    |
| Technical architecture         | Joint / Technical lead | Joint           | Joint                   |
| Project coordination           | Lead                   | Active          | Active                  |
| Decision server architecture   | Lead                   | Active          | Active                  |
| Decision server implementation | **Owner**              | Review / input  | Requirements / review   |
| Server deployment              | **Owner**              | Support         | —                       |
| Harness architecture           | Active                 | Lead            | Active                  |
| Harness implementation         | Review / input         | **Owner**       | Requirements / review   |
| AI-agent interception          | Active                 | **Owner**       | Active                  |
| User happy path                | Joint                  | Joint           | Joint / Product lead    |
| Benchmark architecture         | Joint                  | Joint           | Joint / Evaluation lead |
| Benchmark implementation       | Review / input         | Review / input  | **Owner**               |
| Evaluation metrics             | Joint                  | Joint           | Joint / Product lead    |
| Competitive research           | Active                 | Active          | Lead                    |
| Product research               | Active                 | Active          | Lead                    |
| Risk analysis                  | Joint                  | Joint           | Joint                   |
| MVP scope                      | Joint                  | Joint           | Joint / Product lead    |
| Demo scenario                  | Joint                  | Joint           | Joint                   |
| Project documentation          | Review / input         | Review / input  | **Owner**               |
| Presentation / defense         | Joint input            | Joint input     | **Owner**               |

---

# Team operating model

Работа команды строится вокруг трёх основных технических направлений:

### Decision layer

Primary implementation owner: **Александр Иванов**

Отвечает за архитектуру и реализацию сервиса, принимающего решение относительно действия AI-агента.

### Integration layer

Primary implementation owner: **Алексей Балашов**

Отвечает за получение необходимого контекста из AI-agent harnesses и интеграцию решений AgentGate в execution flow агента.

### Evaluation and product layer

Primary implementation owner: **Тимур Полищук**

Отвечает за benchmark, продуктовую формализацию, критерии эффективности и проектные артефакты.

При этом эти направления не являются независимыми.

Конечный продукт проектируется всей командой как единая система:

**AI Agent / Harness → Context Interception → AgentGate Decision Service → Decision → Agent Action**

Benchmark проверяет работу всей этой системы end-to-end.

Таким образом, архитектурные и продуктовые решения являются результатом совместной работы команды, тогда как ответственность за непосредственную реализацию конкретных компонентов распределена между участниками.
