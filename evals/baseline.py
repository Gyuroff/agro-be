"""Baseline = последният приет run на агента (`evals/baselines/<agent>.json`, в git).

Регресия: ключова метрика пада с повече от 3 пункта (0.03) спрямо baseline — дори да е над gate-а.
"""

import json
from pathlib import Path
from typing import Any

from evals.metrics import HIGHER_BETTER

BASELINES = Path(__file__).resolve().parent / "baselines"
MAX_DROP = 0.03


def path_for(agent: str, directory: Path = BASELINES) -> Path:
    return directory / f"{agent}.json"


def load(agent: str, directory: Path = BASELINES) -> dict[str, Any] | None:
    p = path_for(agent, directory)
    return json.loads(p.read_text(encoding="utf-8")) if p.is_file() else None


def save(agent: str, result: dict[str, Any], directory: Path = BASELINES) -> Path:
    keep = {k: result[k] for k in ("model", "prompt_version", "n", "metrics")}
    p = path_for(agent, directory)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(keep, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return p


def regressions(current: dict[str, float | None], base: dict[str, Any] | None) -> list[str]:
    if base is None:
        return []
    out: list[str] = []
    for name in HIGHER_BETTER:
        was, now = base["metrics"].get(name), current.get(name)
        if was is not None and now is not None and was - now > MAX_DROP + 1e-9:
            out.append(f"{name}: {was:.3f} → {now:.3f} (−{(was - now) * 100:.1f} пункта)")
    return out
