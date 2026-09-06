"""Калибровка полного набора находок ступени 1 текущего сервиса.

Секреты (с учётом провенанса, secrets=on) -> детекторы -> apply. Вывод и
число redacted проверяются для detector-кейсов. Semantic gap обязан оставаться
непойманным ступенью 1. Для classifier-кейсов проверяется только достижимость
модели в выбранном режиме --classifier, по умолчанию on-flag; модель не вызывается.

Запуск из benchmark с зависимостями сервиса:
    uv run --with-editable ../service python tools/calibrate_inspect.py
    uv run --with-editable ../service python tools/calibrate_inspect.py --show GAP_002
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVICE_ROOT = REPO_ROOT.parent / "service"


def _load_stage1():
    """Импорт ступени 1 из соседнего service/, с внятной ошибкой вместо ImportError."""
    if not (SERVICE_ROOT / "agentgate" / "inspect" / "detectors.py").is_file():
        raise SystemExit(
            f"не найден пакет agentgate в {SERVICE_ROOT}: калибровка требует чекаут service/ рядом"
        )
    sys.path.insert(0, str(SERVICE_ROOT))
    try:
        from agentgate.inspect.chain import INSPECT_STAGE1
        from agentgate.inspect.detectors import scan
        from agentgate.inspect.mask import apply
    except ImportError as exc:  # зависимости сервиса не установлены в это окружение
        raise SystemExit(f"не удалось импортировать ступень 1 сервиса: {exc}") from exc
    return INSPECT_STAGE1, scan, apply


def stage1(case) -> dict[str, Any]:
    """Run the service's detectors and provenance-aware secret scan, secrets enabled."""
    detectors, scan, apply = _load_stage1()
    from agentgate.api.schemas import InspectRequest
    from agentgate.inspect.secrets import entropy_candidates_allowed, scan_secrets
    from agentgate.profiles.loader import detect_workspace

    from client.inspect import build_inspect_request
    from client.security_service import SecurityServiceClient
    from config import ServiceConfig

    request = InspectRequest.model_validate(
        build_inspect_request(
            case, SecurityServiceClient(ServiceConfig()), session_id="calibrate", call_id=case.id
        )
    )
    candidates = entropy_candidates_allowed(request.provenance, detect_workspace(request.args.cwd))
    findings = scan_secrets(case.output, entropy_candidates=candidates)
    findings.extend(scan(case.output, detectors))
    outcome = apply(case.output, findings)
    return {
        "verdict": outcome.verdict.value,
        "rule_id": outcome.rule_id,
        "reason": outcome.reason,
        "replacement": outcome.replacement,
        "redacted": outcome.redacted,
        "spans": [s.model_dump(mode="json") for s in outcome.spans],
        "classifier_eligible": any(
            f.rule_id != "inspect.invisible"
            and (f.rule_id != "inspect.secret" or f.candidate_key is not None)
            for f in findings
        ),
        "findings": [
            {"line": f.line, "rule_id": f.rule_id, "action": f.action.value} for f in findings
        ],
    }


def check(case, actual: dict[str, Any], *, classifier: str = "on-flag") -> list[str]:
    """Расхождения между тем, что объявил кейс, и тем, что делает ступень 1."""
    problems: list[str] = []

    if case.tier == "semantic_gap":
        # Кейс существует, чтобы отмечать край. Если ступень 1 его берёт — края больше нет.
        if actual["verdict"] != "pass":
            problems.append(
                f"щель закрылась: ступень 1 даёт {actual['verdict']}"
                f" ({actual['rule_id']}); переведите кейс в tier detector"
            )
        return problems

    if case.tier == "classifier":
        # Последнее слово за ступенью 2; проверяем только предпосылку этого tier.
        eligible = actual.get(
            "classifier_eligible",
            any(
                f["rule_id"] not in {"inspect.invisible", "inspect.secret"}
                for f in actual["findings"]
            ),
        )
        if classifier == "off" or (classifier == "on-flag" and not eligible):
            problems.append(
                f"tier classifier недостижим при classifier={classifier}: "
                "модель отключена или отсутствует находка, которую она может пересмотреть"
            )
        return problems

    if actual["verdict"] != case.expected_verdict:
        problems.append(f"ожидался {case.expected_verdict}, ступень 1 даёт {actual['verdict']}")
    if case.expected_redacted is not None and actual.get("redacted") != case.expected_redacted:
        problems.append("expected_redacted не совпал с числом скрытых значений")
    if case.expected_rule_id_prefix and not (actual["rule_id"] or "").startswith(
        case.expected_rule_id_prefix
    ):
        problems.append(
            f"ожидался rule_id с префиксом {case.expected_rule_id_prefix!r}, "
            f"получен {actual['rule_id']!r}"
        )
    delivered = actual["replacement"] if actual["verdict"] == "mask" else case.output
    if actual["verdict"] != "drop":
        for text in case.must_remove:
            if text in (delivered or ""):
                problems.append(f"must_remove не выполнено, текст остался: {text[:60]!r}")
        for text in case.must_preserve:
            if text not in (delivered or ""):
                problems.append(f"must_preserve не выполнено, текст потерян: {text[:60]!r}")
    if (
        case.expected_output is not None
        and actual["verdict"] == "mask"
        and actual["replacement"] != case.expected_output
    ):
        problems.append("expected_output не совпал с реальной заменой")
    return problems


def main(argv: list[str] | None = None) -> int:
    # Russian diagnostics must also work when Windows redirects stdout to a pipe.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", default="attacks/inspect")
    parser.add_argument(
        "--classifier",
        choices=("off", "on-flag", "always"),
        default="on-flag",
        help="profile mode used to check classifier-tier reachability",
    )
    parser.add_argument("--show", help="напечатать разбор ступени 1 для одного кейса по id")
    args = parser.parse_args(argv)

    sys.path.insert(0, str(REPO_ROOT))
    from dataset.inspect_loader import load_inspections

    cases = load_inspections(Path(args.path))

    if args.show:
        selected = [c for c in cases if c.id == args.show]
        if not selected:
            print(f"кейс не найден: {args.show}", file=sys.stderr)
            return 2
        import json

        print(json.dumps(stage1(selected[0]), ensure_ascii=False, indent=2))
        return 0

    failures = 0
    skipped = 0
    for case in sorted(cases, key=lambda c: c.id):
        if case.api_refusal:
            skipped += 1
            continue
        problems = check(case, stage1(case), classifier=args.classifier)
        if problems:
            failures += 1
            print(f"[РАСХОЖДЕНИЕ] {case.id} ({case.category}, tier={case.tier})")
            for problem in problems:
                print(f"    {problem}")
    checked = len(cases) - skipped
    print(
        f"\nсверено {checked} кейс(ов), пропущено api_refusal: {skipped}, расхождений: {failures}"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
