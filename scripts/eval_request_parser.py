"""Истински модел върху заявките от tests/fixtures/classifier/real_cases.json (без запис в базата).

`PYTHONPATH=. APP_LLM_PROVIDER=anthropic uv run python scripts/eval_request_parser.py`
"""

import json
from pathlib import Path

from app.agents.request_parser.graph import build_graph
from app.core import clock

CASES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "classifier" / "real_cases.json"


def main() -> None:
    graph = build_graph()
    for c in json.loads(CASES.read_text(encoding="utf-8")):
        if c["expected"] != "request":
            continue
        inp = {**c["input"], "today": clock.today_sofia().isoformat()}
        try:
            out = graph.invoke({"input": inp})["output"]
        except Exception as exc:
            print(f"== {c['name']}: ГРЕШКА {type(exc).__name__}: {str(exc)[:200]}")
            continue
        print(
            f"== {c['name']}: {len(out.lines)} реда, период {out.period_from}–{out.period_to}, "
            f"стока={out.product_hint}, склад={out.warehouse_hint}"
        )
        for ln in out.lines[:4]:
            print(
                "   ", ln.date, ln.plate_raw, ln.driver_name, ln.qty_t, ln.uncertain_fields, ln.uncertain_note
            )
        if out.notes:
            print("    бележки:", out.notes[:200])


if __name__ == "__main__":
    main()
