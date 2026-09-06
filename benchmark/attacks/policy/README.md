# Регрессии пользовательской политики

12 команд проверяют текущую реализацию правил: приоритет `deny`/`ask`/`allow`, hard-deny и
запретов профиля, нормализацию переменной, составные команды, пустой набор правил.
Во всех случаях обязательны вердикт, `stage: 1` и префикс `rule_id`.
Профиль — `service/profiles/default-dev.yaml`; рабочая директория — `/home/dev/repo`.
Калибровка не запускает ни одной команды и не вызывает модель.

```powershell
uv run python cli.py validate --path attacks/policy --subset
uv run --with-editable ../service python tools/check_service.py
uv run python cli.py benchmark --path attacks/policy --subset --concurrency 1
```

`--subset` необходим: это отдельная таблица регрессий политики, а не новая категория
атак с пятью обязательными уровнями сложности. Набор не включён в исходные 120 кейсов.
Его accuracy проверяет политику; ASR/FP нельзя объединять с результатами без правил.
Легитимная команда, для которой пользователь потребовал `ask` или `deny`, остаётся
легитимной: намеренное ограничение не превращает её в атаку.

`tools/check_service.py` применяет итоговый пол через `STAGE1.run(...).settled()`.
Дополнительные v3.1–v4 матрицы (MCP, сетевые методы, stage 2, сессии, inspect)
проверяет `tools/service_regressions.py`; они не добавляются к 120-case headline suite.
