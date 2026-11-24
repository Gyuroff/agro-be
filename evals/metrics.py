"""Метрики (docs/testing.md §3.3). Чисти функции върху `output.model_dump(mode="json")` и очакваното.

Без LLM-as-judge. Всеки скорер връща `CaseScore`: стойности 0..1 (None = не се прилага за случая),
броячи за микро-средните (tp/fp/fn на редовете) и грешките по тонаж.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from statistics import median
from typing import Any

from app.domain.plates import normalize_plate

HIGHER_BETTER = (
    "schema_valid",
    "kind_accuracy",
    "line_f1",
    "uncertain_recall",
    "no_hallucinated_plates",
    "field_accuracy",
)


@dataclass
class CaseScore:
    metrics: dict[str, float | None] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    qty_errors: list[float] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)


def _line_key(line: dict[str, Any], date_key: str, seen: dict[str, int]) -> tuple[str, str]:
    day = str(line.get(date_key) or "")
    plate = normalize_plate(line.get("plate_raw") or line.get("plate_norm") or "")
    if not plate:  # редове без номер се подреждат по дата и поредност
        seen[day] = seen.get(day, 0) + 1
        plate = f"#{seen[day]}"
    return day, plate


def _keyed(lines: list[dict[str, Any]], date_key: str) -> dict[tuple[str, str], dict[str, Any]]:
    seen: dict[str, int] = {}
    return {_line_key(ln, date_key, seen): ln for ln in lines}


def _f1(tp: int, fp: int, fn: int) -> float:
    return 1.0 if tp + fp + fn == 0 else 2 * tp / (2 * tp + fp + fn)


def _dec(value: Any) -> Decimal | None:
    return None if value in (None, "") else Decimal(str(value))


def score_lines(
    out_lines: list[dict[str, Any]],
    exp_lines: list[dict[str, Any]],
    *,
    date_key: str,
    qty_key: str,
    input_text: str,
    plan_plates: list[str],
) -> CaseScore:
    """Редове по (дата, номер): precision/recall, грешка по тонаж, uncertain_recall, халюцинирани номера."""
    # очакваният ред може да ползва същите ключове като изхода или `planned_date`/`plate_norm`/`planned_qty_t`
    exp = _keyed([_expected_line(e, date_key, qty_key) for e in exp_lines], date_key)
    got = _keyed(out_lines, date_key)
    hit = exp.keys() & got.keys()
    tp, fp, fn = len(hit), len(got.keys() - exp.keys()), len(exp.keys() - got.keys())
    score = CaseScore(counts={"tp": tp, "fp": fp, "fn": fn})
    score.metrics["line_f1"] = _f1(tp, fp, fn)
    for key in sorted(got.keys() - exp.keys()):
        score.problems.append(f"излишен ред {key}")
    for key in sorted(exp.keys() - got.keys()):
        score.problems.append(f"липсва ред {key}")

    wanted = found = 0
    for key in hit:
        e, g = exp[key], got[key]
        eq, gq = _dec(e.get(qty_key)), _dec(g.get(qty_key))
        if eq is not None and gq is not None:
            score.qty_errors.append(float(abs(eq - gq)))
        for f in e.get("uncertain_fields") or []:
            wanted += 1
            found += f in (g.get("uncertain_fields") or [])
    score.metrics["uncertain_recall"] = found / wanted if wanted else None

    haystack = normalize_plate(input_text)
    allowed = {normalize_plate(p) for p in plan_plates}
    bad = [
        p
        for g in out_lines
        if (p := normalize_plate(g.get("plate_raw") or "")) and p not in allowed and p not in haystack
    ]
    score.metrics["no_hallucinated_plates"] = 0.0 if bad else 1.0
    score.problems.extend(f"измислен номер {p}" for p in bad)
    return score


def _expected_line(e: dict[str, Any], date_key: str, qty_key: str) -> dict[str, Any]:
    return {
        **e,
        date_key: e.get(date_key) or e.get("planned_date") or e.get("load_date"),
        "plate_raw": e.get("plate_raw") or e.get("plate_norm") or "",
        qty_key: e.get(qty_key) if e.get(qty_key) is not None else e.get("planned_qty_t") or e.get("qty_t"),
    }


def score_fields(out: dict[str, Any], expected: dict[str, Any], fields: list[str]) -> CaseScore:
    """Точно съвпадение по полета (низове; числата — като Decimal); само полетата, дадени в expected."""
    score, hit, total = CaseScore(), 0, 0
    for name in fields:
        if name not in expected:
            continue
        total += 1
        want, got = expected[name], out.get(name)
        if isinstance(want, list):
            ok = sorted(normalize_plate(str(x)) for x in want) == sorted(
                normalize_plate(str(x)) for x in got or []
            )
        elif _is_number(want) and _is_number(got):
            ok = Decimal(str(want)) == Decimal(str(got))
        else:
            ok = want == got
        hit += ok
        if not ok:
            score.problems.append(f"{name}: очаквано {want!r}, получено {got!r}")
    score.metrics["field_accuracy"] = hit / total if total else None
    return score


def _is_number(v: Any) -> bool:
    try:
        Decimal(str(v))
    except Exception:
        return False
    return v is not None and not isinstance(v, bool)


def median_or_none(values: list[float]) -> float | None:
    return median(values) if values else None
