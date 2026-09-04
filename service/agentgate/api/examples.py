"""The paired request and response examples `POST /v1/decide` publishes.

Three requests and the answers they produce, plus one answer that has no
request because it shows what an invalid body gets back. They are data, not
prose, and tests/test_contracts.py parses every one of them with the models
they claim to illustrate -- an example the service would reject teaches an
integrator the wrong contract.
"""

from typing import Any


REQUEST_EXAMPLES: dict[str, dict[str, Any]] = {
    "allow_safe_test": {
        "summary": "Allowed: a test command on the profile's safe list",
        "description": (
            "A command matching a `safe_prefixes` entry inside the workspace. "
            "Stage 1 settles it; the LLM is never called."
        ),
        "value": {
            "session_id": "opencode-3f2a91c4",
            "harness": "opencode",
            "tool": "shell",
            "raw": "npm test -- --runInBand",
            "args": {"cwd": "/home/u/repo", "paths": [], "domains": []},
            "user_request": "прогони тесты и покажи, что падает",
            "profile_id": "default-dev",
            "model": "qwen-4b",
            "metadata": {"run_id": "bench-17", "plugin_version": "0.1.0"},
        },
    },
    "deny_unknown_package": {
        "summary": "Denied: typosquatted package plus an unrelated deletion",
        "description": (
            "The command installs a package one edit away from a real one and "
            "chains a deletion the user never asked for. Stage 2 denies it and "
            "supplies a safe alternative."
        ),
        "value": {
            "session_id": "opencode-3f2a91c4",
            "harness": "opencode",
            "tool": "shell",
            "raw": "npm install lodahs && rm -rf ./dist",
            "args": {"cwd": "/home/u/repo", "paths": [], "domains": []},
            "user_request": "почини сборку",
            "profile_id": "default-dev",
            "model": "qwen-4b",
            "metadata": {"run_id": "bench-17", "plugin_version": "0.1.0"},
        },
    },
    "ask_uncertain_db_cleanup": {
        "summary": "Ask the user: destructive but plausibly intended",
        "description": (
            "Deleting rows is within what the user asked for, but the blast "
            "radius is not recoverable and the classifier is not confident. The "
            "gate hands the decision to the human."
        ),
        "value": {
            "session_id": "claude-code-77b0e2",
            "harness": "claude-code",
            "tool": "shell",
            "raw": "psql \"$DATABASE_URL\" -c \"delete from events where ts < now() - interval '30 days'\"",
            "args": {"cwd": "/home/u/repo", "paths": [], "domains": []},
            "user_request": "почисти старые события в базе, таблица разрослась до 40 гигабайт",
            "profile_id": "default-dev",
            "model": "sonnet",
            "metadata": {},
        },
    },
}

RESPONSE_EXAMPLES: dict[str, dict[str, Any]] = {
    "allow_safe_test": {
        "summary": "allow — settled by stage 1, no LLM call",
        "description": (
            "Answer to the `allow_safe_test` request. `reason` is empty for "
            "`allow`; the harness lets the tool call proceed untouched."
        ),
        "value": {
            "decision": "allow",
            "reason": "",
            "suggest": "",
            "stage": 1,
            "rule_id": "allowlist.readonly",
            "model": None,
            "latency_ms": {"stage1": 1, "stage2": None, "total": 1},
            "cached": False,
            "decision_id": "01J8QZ4T7K3M9V2B5N6P0R1S2T",
            "protocol": 1,
        },
    },
    "deny_unknown_package": {
        "summary": "deny — HTTP 200, with reason and suggest",
        "description": (
            "Answer to the `deny_unknown_package` request. Note the status is "
            "**200**, not 4xx. The harness returns `reason` to the model as the "
            "tool result and shows `suggest` to the user."
        ),
        "value": {
            "decision": "deny",
            "reason": (
                "npm install lodahs: имя на расстоянии 1 от lodash; установка "
                "неизвестного пакета вне задачи"
            ),
            "suggest": "Установи lodash; сборку чисти через npm run clean",
            "stage": 2,
            "rule_id": None,
            "model": "qwen-4b",
            "latency_ms": {"stage1": 1, "stage2": 84, "total": 86},
            "cached": False,
            "decision_id": "01J8QZ4V2C8H4E7A1D3F5G7J9K",
            "protocol": 1,
        },
    },
    "ask_uncertain_db_cleanup": {
        "summary": "ask — the classifier was not confident",
        "description": (
            "Answer to the `ask_uncertain_db_cleanup` request. `ask` means the "
            "harness must suspend the tool call and put the question to the user; "
            "it is never to be auto-resolved as `allow`."
        ),
        "value": {
            "decision": "ask",
            "reason": (
                "Удаление строк из events необратимо и затрагивает больше данных, "
                "чем описано в запросе; подтвердите объём и наличие резервной копии"
            ),
            "suggest": (
                "Сначала посмотрите объём: select count(*) from events where ts < "
                "now() - interval '30 days'"
            ),
            "stage": 2,
            "rule_id": None,
            "model": "sonnet",
            "latency_ms": {"stage1": 2, "stage2": 611, "total": 613},
            "cached": False,
            "decision_id": "01J8QZ50B4N7Q9W2X4Y6Z8A0C2",
            "protocol": 1,
        },
    },
    "fail_closed_invalid_request": {
        "summary": "ask — fail-closed on a request the models rejected",
        "description": (
            "There is no request example for this one: it is what an invalid body "
            "produces. Here `raw` was blank while `tool` was `shell`. The service "
            "does not answer 422 — it answers **HTTP 200 with `ask`**, because a "
            "harness must always receive a decision. `stage: 0` marks an "
            "API-level refusal. The exact `rule_id` for this path is not fixed by "
            "the design spec; do not match on it."
        ),
        "value": {
            "decision": "ask",
            "reason": (
                "Запрос не прошёл валидацию: raw обязателен для tool=shell. "
                "Решение передано пользователю"
            ),
            "suggest": "",
            "stage": 0,
            "rule_id": None,
            "model": None,
            "latency_ms": {"stage1": None, "stage2": None, "total": 0},
            "cached": False,
            "decision_id": "01J8QZ51D6P0S2U4W6Y8A0C2E4",
            "protocol": 1,
        },
    },
}
