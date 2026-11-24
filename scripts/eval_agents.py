"""Eval на агентите върху golden set (docs/testing.md §3.3, §5). Не пише в базата и не праща писма.

    uv run python scripts/eval_agents.py --dataset tests/fixtures \\
        --agents email_classifier,request_parser --out evals/results/run.json

Нужен е истинският модел (`APP_LLM_PROVIDER=anthropic`); със `stub` се брои за integration, не за gate
(нужно е `--allow-stub`). Изход ≠ 0 при gate под прага или регресия >3 пункта спрямо baseline.
`--accept-baseline` записва метриките като нов baseline; `--write-fixtures` пише отговорите на модела в
`tests/fixtures/llm/<agent>/` за stub тестовете.
"""

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from app.agents.llm import recording_fixtures
from app.core.config import LlmProvider, get_settings
from evals import baseline, report
from evals.dataset import load_cases
from evals.runner import registry, run_eval

ROOT = Path(__file__).resolve().parent.parent


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", type=Path, default=ROOT / "tests" / "fixtures")
    ap.add_argument("--agents", default=",".join(registry()), help="списък през запетая")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--accept-baseline", action="store_true")
    ap.add_argument("--write-fixtures", action="store_true")
    ap.add_argument("--allow-stub", action="store_true")
    args = ap.parse_args(argv)

    agents = [a for a in args.agents.split(",") if a]
    unknown = set(agents) - set(registry())
    if unknown:
        ap.error(f"непознати агенти: {sorted(unknown)}")
    stub = get_settings().llm_provider is LlmProvider.STUB
    if stub and not args.allow_stub:
        ap.error("APP_LLM_PROVIDER=stub: това е integration, не eval. Задай anthropic или --allow-stub.")
    if stub and args.write_fixtures:
        ap.error("--write-fixtures иска истински модел")

    cases = load_cases(args.dataset, set(agents))
    if not cases:
        print(f"Няма случаи за {agents} в {args.dataset}", file=sys.stderr)
        return 2
    if args.write_fixtures:
        with recording_fixtures():
            result = run_eval(cases, agents)
    else:
        result = run_eval(cases, agents)

    bases = {a: baseline.load(a) for a in result["agents"]}
    print(report.render(result, bases))
    out = args.out or ROOT / "evals" / "results" / f"{datetime.now(UTC):%Y%m%d_%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Резултат: {out}")

    failed = False
    for agent, r in result["agents"].items():
        failed |= bool(r["gate_failures"]) or bool(baseline.regressions(r["metrics"], bases[agent]))
    if args.accept_baseline and not failed:
        for agent, r in result["agents"].items():
            print("baseline:", baseline.save(agent, r))
    elif args.accept_baseline:
        print("Baseline НЕ е обновен: има gate/регресия.", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
