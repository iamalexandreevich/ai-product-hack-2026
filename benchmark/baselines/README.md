# Внешний baseline: ActBench

Для измерения исходов задач выбран [ActBench](https://github.com/ZJUICSR/ActBench):
исполняемые задачи, парные чистые окружения, MCP-инструменты и нативные оценщики атаки
и полезности. Проверено 5 сентября 2026; зафиксирована
[ревизия от 15 августа 2026](https://github.com/ZJUICSR/ActBench/commit/e532aa45fdb77ac81565fa5151d2652ffd94c828).
В ней 300 пар задач в 15 группах поведения; стандартный `representative` запускает 15 пар.
Лицензия upstream — MIT. Это современный релевантный исполняемый baseline, а не заявление
о первенстве по дате среди всех исследований.

Сопоставлены также [ATBench](https://github.com/LiYu0524/ATbench) и его
[coding-набор](https://huggingface.co/datasets/AI45Research/ATBench-Codex),
[TraceSafe](https://huggingface.co/datasets/CyCraftAI/TraceSafe) и
[TS-Bench](https://github.com/MurrayTom/ToolSafe). ATBench оценивает траектории;
его метки нельзя механически присвоить каждому отдельному действию. TraceSafe требует
доступа к gated-датасету. Для этой интеграции важны доступные исполняемые задачи с чистыми
контролями и собственными оценщиками, поэтому выбран ActBench.

## Граница эксперимента

`execution_mode=harness_task` хранится отдельно от существующих `single_decision`,
`harness_loop` и inspect-отчётов. Нативный Claude Code backend получает только пять
MCP-инструментов ActBench. Встроенные инструменты Claude Code отключены.

| Режим | Исполнение | Результат инструмента |
|---|---|---|
| `--guard off` | Нативный ActBench | Нативный ответ |
| `--guard decide` | Только после `allow` AgentGate | Нативный ответ |
| `--guard decide-inspect` | Только после `allow` AgentGate | `pass`, точная замена при `mask`, блокировка при `drop` |

Gateway перехватывает реальный вызов обработчика MCP. `deny` и `ask` предотвращают вызов,
агент получает сообщение о блокировке. Подтверждения человеком не симулируются:
`benign_ask_count` — число запросов подтверждения на чистых задачах, не измеренное время человека.
Ошибки транспорта/контракта блокируют действие и делают прогон непригодным для итоговых метрик.

В сервис идут исходный запрос задачи, аргументы инструмента, общий `call_id` и предыдущие
публичные вызовы/доставленные результаты. Скрытые рассуждения и grader в историю не добавляются.
Ответ inspect не обрезается: превышение лимита обрабатывает сам сервис. Задачи с несколькими
сессиями пока отвергаются с явной ошибкой; стандартные 15 пар таких задач не содержат.
Поддержка произвольного `--suite` не означает, что каждый расширенный набор уже проверен.

## Подготовка и проверки без модели

Команды PowerShell выполняются из `benchmark/`. Исходники скачиваются в игнорируемый `results/`;
`verify` проверяет точную ревизию, изменения tracked-файлов, 300 пар и вычисляет digest YAML.

```powershell
uv run python tools/actbench.py fetch
uv run python tools/actbench.py verify
docker build -f tools/sandbox/Dockerfile -t agentgate-bench-sandbox .
docker build --build-context contracts=../contracts -f tools/actbench/Dockerfile -t agentgate-actbench .
$resultDir = (New-Item -ItemType Directory -Force results/actbench).FullName
docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges --mount "type=bind,source=$resultDir,target=/home/bench/out" agentgate-actbench self-test --source /opt/actbench --output /home/bench/out
docker run --rm --network none --entrypoint python agentgate-actbench tools/check_actbench_gate.py /opt/actbench
docker run --rm --network none --entrypoint python agentgate-actbench tools/check_actbench_http.py /opt/actbench
```

`self-test` использует нативный fake backend, не вызывает модель и не измеряет качество защиты.
Две следующие проверки вызывают настоящие MCP-обработчики и HTTP gateway с тестовым сервисом:
проверяют отсутствие записи при блокировке и доставку точной замены после разрешённой записи.
Весь upstream harness, включая его временные рабочие каталоги, остаётся внутри контейнера.

## Измерение с моделью

Модель агента и judge задаются явно, чтобы сравниваемые прогоны использовали одинаковые модели.
Пример использует `ANTHROPIC_API_KEY` для агента и `OPENROUTER_API_KEY` для judge;
установите их и параметры AgentGate в окружении. Команды передают значения
через имена переменных; ключи не записываются в команду или provenance-файлы.
Для сервиса на Windows-хосте используйте `http://host.docker.internal:8000`.
Для удалённого адреса требуется `--allow-remote`.

```powershell
$env:SECURITY_SERVICE_URL = 'http://host.docker.internal:8000'
$agentModel = '<explicit-agent-model-id>'
$judgeModel = '<OpenRouter-provider/model-id>'
$resultDir = (Resolve-Path results/actbench).Path
docker run --rm --cap-drop ALL --security-opt no-new-privileges -e ANTHROPIC_API_KEY -e OPENROUTER_API_KEY --mount "type=bind,source=$resultDir,target=/home/bench/out" agentgate-actbench run --source /opt/actbench --output /home/bench/out --model $agentModel --judge-model $judgeModel --score-mode combined-ags --guard off
docker run --rm --cap-drop ALL --security-opt no-new-privileges -e ANTHROPIC_API_KEY -e OPENROUTER_API_KEY -e SECURITY_SERVICE_URL -e SECURITY_SERVICE_TOKEN --mount "type=bind,source=$resultDir,target=/home/bench/out" agentgate-actbench run --source /opt/actbench --output /home/bench/out --model $agentModel --judge-model $judgeModel --score-mode combined-ags --guard decide-inspect --profile default
```

Контейнер имеет сеть для обращения к моделям и сервису. На хост монтируется только каталог
результатов; live-запуск вне Docker отвергается. Для отдельной абляции pre-action повторите
второй запуск с `--guard decide`. `--service-model` выбирает классификатор AgentGate независимо
от модели агента. `plan --model ...` печатает команду и источник без запуска модели.

`--score-mode automated` запускает только автоматическую часть нативного AGS. Это отдельный
режим оценки: UGS и task completion остаются `null`. `combined-ags --judge-model ...` добавляет
нативную LLM-оценку атаки, затем упаковку чистых траекторий и нативный utility scorer.
Для judge действуют настройки провайдера из
[документации закреплённой версии](https://github.com/ZJUICSR/ActBench/tree/e532aa45fdb77ac81565fa5151d2652ffd94c828).

## Отчёты и сопоставимость

Каждый запуск создаёт уникальный каталог `<run-id>` с `source.json`, нативными артефактами,
собственным `clean-cache/`, а при включённом сервисе — `service.json` и `gate-events.jsonl`.
Повторное использование чистого cache между режимами исключено. Не публикуйте сырые траектории
вместе с рабочими данными: в них сохраняется фактический текст инструментов.

```powershell
uv run python tools/actbench_report.py results/actbench/<run-id>
uv run python tools/actbench_report.py results/actbench/<off-run-id> --compare-to results/actbench/<guarded-run-id>
```

- ASR и средний AGS берутся из завершённого нативного scoring, а не из placeholder-полей collection.
- Utility публикуется только при полном совпадении популяции чистых задач и валидной оценке каждой.
- Slowdown — среднее отношений времени выполнения одинаковых успешно выполненных чистых задач;
  число пар выводится рядом. Это условная оценка по завершённым задачам, не компенсация провалов utility.
- Сравнение требует одинаковых источника, задач, агента, judge и режима оценки.
- Fake/self-test, неполные оценки, ошибки интеграции и отсутствие активности включённого gateway
  оставляют измерения `null` с причиной. Они не превращаются в нулевой ASR или стопроцентную защиту.

На 5 сентября 2026 проверены источник, fake self-test, блокировка реальных MCP-вызовов и HTTP-путь.
Прогоны с живыми моделями и новые численные доказательства ASR/utility пока не выполнены.
