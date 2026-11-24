"""Таблица за човека: метрика по агент + само проваленото на ниво случай."""

from typing import Any

from evals import baseline


def _fmt(v: float | None) -> str:
    return "—" if v is None else f"{v:.3f}"


def render(result: dict[str, Any], baselines: dict[str, dict[str, Any] | None] | None = None) -> str:
    lines: list[str] = []
    for agent, r in result["agents"].items():
        base = (baselines or {}).get(agent)
        lines.append(f"## {agent}  (n={r['n']}, {r['model']}, {r['prompt_version']})")
        lines.append(f"{'метрика':26} {'сега':>8} {'baseline':>9}")
        for name, value in r["metrics"].items():
            was = base["metrics"].get(name) if base else None
            lines.append(f"{name:26} {_fmt(value):>8} {_fmt(was):>9}")
        for msg in r["gate_failures"]:
            lines.append(f"  GATE ✗ {msg}")
        for msg in baseline.regressions(r["metrics"], base):
            lines.append(f"  РЕГРЕСИЯ ✗ {msg}")
        for c in r["cases"]:
            if not c["ok"]:
                lines.append(f"  ✗ {c['id']}: " + "; ".join(p for p in c["problems"] if p)[:300])
        lines.append("")
    scores = [
        v for r in result["agents"].values() for k, v in r["metrics"].items() if k in _SCORE and v is not None
    ]
    if scores:
        lines.append(f"ОБЩ SCORE: {sum(scores) / len(scores):.3f}")
    return "\n".join(lines)


_SCORE = {
    "schema_valid",
    "kind_accuracy",
    "line_f1",
    "uncertain_recall",
    "no_hallucinated_plates",
    "field_accuracy",
}
