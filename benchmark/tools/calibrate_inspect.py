"""Проверка ожиданий набора inspect по реальной ступени 1 сервиса.

Ожидаемый вердикт кейса — это утверждение о поведении сервиса, и писать его на глаз
нельзя: детекторы устроены сложнее, чем кажется по названиям (у `inspect.invisible`
действие `clean`, а не `mask`, и он не считается в порог `drop`; у `inspect.encoded`
порог — 513 символов **одного** пробега `[A-Za-z0-9+/=]`; ступень 2 вообще не
запускается, если все находки невидимые).

Поэтому ожидания калибруются: скрипт импортирует `agentgate.inspect` из соседнего
`service/` **в своём процессе**, прогоняет `scan` + `apply` на `output` каждого кейса и
сверяет с тем, что кейс объявил. Ни сети, ни поднятого сервиса, ни базы не нужно.

    uv run python tools/calibrate_inspect.py                 # сверить весь набор
    uv run python tools/calibrate_inspect.py --show INJ_DIRECT_001   # что даёт ступень 1
    uv run python tools/calibrate_inspect.py --path attacks/inspect

Код возврата 1, если хоть один кейс разошёлся с реализацией.

Границы применимости, чтобы на скрипт не полагались сверх меры:

* сверяется **только ступень 1**. Кейсы `tier: classifier` ждут вердикта от ступени 2,
  которой здесь нет, — для них скрипт печатает, что нашла ступень 1, и не считает
  расхождение ошибкой;
* `api_refusal` отвергается плоскостью протокола до детекторов — такие кейсы
  пропускаются целиком;
* `tier: semantic_gap` обязан расходиться: кейс объявляет, что сделал бы правильный
  гейт, а v3 его пропускает. Скрипт проверяет ровно это — что ступень 1 **не** ловит
  текст. Если поймала, щель закрылась, и кейс пора переводить в его настоящий tier.
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


def stage1(output: str) -> dict[str, Any]:
    """Что реальная ступень 1 сервиса делает с этим текстом."""
    detectors, scan, apply = _load_stage1()
    findings = scan(output, detectors)
    outcome = apply(output, findings)
    return {
        "verdict": outcome.verdict.value,
        "rule_id": outcome.rule_id,
        "reason": outcome.reason,
        "replacement": outcome.replacement,
        "findings": [
            {"line": f.line, "rule_id": f.rule_id, "action": f.action.value} for f in findings
        ],
    }


def check(case, actual: dict[str, Any]) -> list[str]:
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
        if not any(f["rule_id"] != "inspect.invisible" for f in actual["findings"]):
            problems.append(
                "tier classifier, но ни один детектор не сработал: ступень 2 не запустится, "
                "и вердикт будет pass со ступени 1"
            )
        return problems

    if actual["verdict"] != case.expected_verdict:
        problems.append(f"ожидался {case.expected_verdict}, ступень 1 даёт {actual['verdict']}")
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

        print(json.dumps(stage1(selected[0].output), ensure_ascii=False, indent=2))
        return 0

    failures = 0
    skipped = 0
    for case in sorted(cases, key=lambda c: c.id):
        if case.api_refusal:
            skipped += 1
            continue
        problems = check(case, stage1(case.output))
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
