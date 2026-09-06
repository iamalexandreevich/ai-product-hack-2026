# Как прогнать ActBench

Внешний baseline по исходам задач. Подробности и оговорки — `baselines/README.md`.
Все команды из `benchmark/`, PowerShell.

Что нужно знать заранее:

- Живой прогон (`run`) работает **только внутри Docker** — вне контейнера отвергается.
- `representative` = 15 пар задач = 30 агентных сессий на прогон, строго последовательно.
- Агент оплачивается `ANTHROPIC_API_KEY`, judge — `OPENROUTER_API_KEY`,
  классификатор AgentGate (`--service-model`) — настройками сервиса.
- Guarded-прогон без контрольного `--guard off` бессмыслен: сравнивать не с чем.

## 1. Подготовка (бесплатно, без модели)

```powershell
uv run python tools/actbench.py fetch     # исходники в results/vendor/actbench
uv run python tools/actbench.py verify    # ревизия e532aa4, 300 пар, digest

docker build -f tools/sandbox/Dockerfile -t agentgate-bench-sandbox .
docker build --build-context contracts=../contracts -f tools/actbench/Dockerfile -t agentgate-actbench .
```

## 2. Проверки интеграции (бесплатно, без модели)

```powershell
$resultDir = (New-Item -ItemType Directory -Force results/actbench).FullName

docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges `
  --mount "type=bind,source=$resultDir,target=/home/bench/out" `
  agentgate-actbench self-test --source /opt/actbench --output /home/bench/out

docker run --rm --network none --entrypoint python agentgate-actbench tools/check_actbench_gate.py /opt/actbench
docker run --rm --network none --entrypoint python agentgate-actbench tools/check_actbench_http.py /opt/actbench
```

`self-test` крутит fake backend: проверяет проводку, но **не измеряет защиту** — его числа не результат.

Посмотреть точную команду прогона, ничего не запуская:

```powershell
uv run python tools/actbench.py plan --model <agent-model-id> --guard decide-inspect
```

## 3. Прогон с моделью (платно)

```powershell
$env:SECURITY_SERVICE_URL = 'http://host.docker.internal:8000'   # сервис на Windows-хосте
$agentModel = '<agent-model-id>'
$judgeModel = '<provider/model-id для OpenRouter>'
$resultDir  = (Resolve-Path results/actbench).Path
```

Контроль — обязателен первым:

```powershell
docker run --rm --cap-drop ALL --security-opt no-new-privileges `
  -e ANTHROPIC_API_KEY -e OPENROUTER_API_KEY `
  --mount "type=bind,source=$resultDir,target=/home/bench/out" `
  agentgate-actbench run --source /opt/actbench --output /home/bench/out `
  --model $agentModel --judge-model $judgeModel --score-mode combined-ags --guard off
```

Под AgentGate:

```powershell
docker run --rm --cap-drop ALL --security-opt no-new-privileges `
  -e ANTHROPIC_API_KEY -e OPENROUTER_API_KEY -e SECURITY_SERVICE_URL -e SECURITY_SERVICE_TOKEN `
  --mount "type=bind,source=$resultDir,target=/home/bench/out" `
  agentgate-actbench run --source /opt/actbench --output /home/bench/out `
  --model $agentModel --judge-model $judgeModel --score-mode combined-ags --guard decide-inspect --profile default
```

Абляция pre-action (опционально) — та же команда с `--guard decide`.

Ключи передаются именами переменных (`-e VAR` без значения), чтобы не попасть в команду и в
provenance-файлы. Удалённый адрес сервиса требует `--allow-remote`.

## 4. Отчёты

```powershell
uv run python tools/actbench_report.py results/actbench/<run-id>
uv run python tools/actbench_report.py results/actbench/<off-run-id> --compare-to results/actbench/<guarded-run-id>
```

Сравнение требует одинаковых источника, задач, агента, judge и режима оценки.
Неполные оценки и молчащий gateway оставляют метрику `null` с причиной — это не нулевой ASR.

## Шпаргалка по флагам

| Флаг                                          | Смысл                                                                     |
|-----------------------------------------------|---------------------------------------------------------------------------|
| `--guard off \| decide \| decide-inspect`     | без защиты / только pre-action / плюс проверка результата                 |
| `--score-mode automated`                      | только автоматический AGS; UGS и task completion остаются `null`          |
| `--score-mode combined-ags --judge-model ...` | полная нативная оценка атаки и utility                                    |
| `--suite`                                     | по умолчанию `representative` (15 пар из 300); другие наборы не проверены |
| `--service-model`                             | классификатор AgentGate, независимо от модели агента                      |
| `--profile`                                   | профиль сервиса для guarded-прогона                                       |
