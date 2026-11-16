"""Истински модел върху случаите от tests/fixtures/classifier (без запис в базата).

`PYTHONPATH=. APP_LLM_PROVIDER=anthropic uv run python scripts/eval_classifier.py`
"""

import json
from pathlib import Path

from app.agents.email_classifier.graph import build_graph, decide

DIR = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "classifier"


def main() -> None:
    graph = build_graph()
    ok = total = 0
    for f in sorted(DIR.glob("*cases.json")):
        for c in json.loads(f.read_text(encoding="utf-8")):
            out = graph.invoke({"input": c["input"]})["output"]
            d = decide(out)
            if c.get("pending_spec"):
                print(f"-- {c['name']:28} чака спецификацията; модел={out.kind.value} {out.confidence:.2f}")
                continue
            hit = d.kind.value == c["expected"]
            ok, total = ok + hit, total + 1
            flag = "" if c.get("label_verified", True) else " (етикет непотвърден)"
            mark = "OK " if hit else "ERR"
            print(f"{mark} {c['name']:28} очаквано={c['expected']:12} модел={out.kind.value:12}", end=" ")
            print(f"{out.confidence:.2f}{flag}")
            if not hit:
                print("     причина:", out.reason)
    print(f"{ok}/{total} = {ok / total:.0%}")


if __name__ == "__main__":
    main()
