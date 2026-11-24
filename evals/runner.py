"""Пуска агентите върху случаите, точкува и сглобява резултата (docs/testing.md §3.3, §5).

Графовете се викат директно (`graph.invoke`) — без сесия към базата, без `agent_runs`, без писма.
В резултата са закачени `prompt_version` и `model`, за да е ясно с какво е мерено.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from langchain_core.callbacks import UsageMetadataCallbackHandler
from pydantic import BaseModel

from app.agents.llm import model_id
from app.core import clock
from evals import metrics
from evals.dataset import Case
from evals.metrics import CaseScore

Scorer = Callable[[Case, dict[str, Any]], CaseScore]

# Gate за MVP (testing.md §3.3). `>=` за дяловете, `<=` за `qty_mae_median` (тонове).
GATES_MIN: dict[str, dict[str, float]] = {
    "email_classifier": {"schema_valid": 1.0, "kind_accuracy": 0.95},
    "request_parser": {
        "schema_valid": 1.0,
        "line_f1": 0.90,
        "uncertain_recall": 0.90,
        "no_hallucinated_plates": 1.0,
    },
    "report_parser": {
        "schema_valid": 1.0,
        "line_f1": 0.90,
        "uncertain_recall": 0.90,
        "no_hallucinated_plates": 1.0,
    },
    "cancellation_parser": {"schema_valid": 1.0},
    "mega_invoice_parser": {"schema_valid": 1.0},
    "report_matcher": {"schema_valid": 1.0},
}
GATES_MAX: dict[str, float] = {"qty_mae_median": 0.05}


def _input_text(case: Case) -> str:
    inp = case.input
    atts = " ".join(a.get("tsv", "") for a in inp.get("attachments", []))
    return " ".join([inp.get("subject", ""), inp.get("body_text", ""), atts])


def _score_classifier(case: Case, out: dict[str, Any]) -> CaseScore:
    from app.agents.email_classifier.graph import decide
    from app.agents.email_classifier.schemas import ClassifierOutput

    d = decide(ClassifierOutput.model_validate(out))
    got = {
        "kind": d.kind.value,
        "doc_kind": d.doc_kind.value if d.doc_kind else None,
        "reply_status": d.reply_status.value if d.reply_status else None,
    }
    want = {k: case.expected.get(k) for k in got}
    score = CaseScore(metrics={"kind_accuracy": float(got == want)})
    if got != want:
        score.problems.append(f"очаквано {want}, получено {got} ({out.get('reason')})")
    return score


def _lines_scorer(date_key: str) -> Scorer:
    def score(case: Case, out: dict[str, Any]) -> CaseScore:
        return metrics.score_lines(
            out.get("lines", []),
            case.expected.get("lines", []),
            date_key=date_key,
            qty_key="qty_t",
            input_text=_input_text(case),
            plan_plates=case.plan_plates,
        )

    return score


def _fields_scorer(*fields: str) -> Scorer:
    return lambda case, out: metrics.score_fields(out, case.expected, list(fields))


def _no_score(_case: Case, _out: dict[str, Any]) -> CaseScore:
    return CaseScore()


@dataclass(frozen=True)
class AgentSpec:
    name: str
    prompt_version: str
    build_graph: Callable[[], Any]
    scorer: Scorer


def registry() -> dict[str, AgentSpec]:
    from app.agents.cancellation_parser import graph as cancel
    from app.agents.cancellation_parser.prompts import PROMPT_VERSION as cancel_v
    from app.agents.email_classifier import graph as classify
    from app.agents.email_classifier.prompts import PROMPT_VERSION as classify_v
    from app.agents.mega_invoice_parser import graph as invoice
    from app.agents.mega_invoice_parser.prompts import PROMPT_VERSION as invoice_v
    from app.agents.report_matcher import graph as matcher
    from app.agents.report_matcher.prompts import PROMPT_VERSION as matcher_v
    from app.agents.report_parser import graph as report
    from app.agents.report_parser.prompts import PROMPT_VERSION as report_v
    from app.agents.request_parser import graph as request
    from app.agents.request_parser.prompts import PROMPT_VERSION as request_v

    specs = [
        AgentSpec("email_classifier", classify_v, classify.build_graph, _score_classifier),
        AgentSpec("request_parser", request_v, request.build_graph, _lines_scorer("date")),
        AgentSpec("report_parser", report_v, report.build_graph, _lines_scorer("load_date")),
        AgentSpec("cancellation_parser", cancel_v, cancel.build_graph, _fields_scorer("date", "plates")),
        AgentSpec(
            "mega_invoice_parser",
            invoice_v,
            invoice.build_graph,
            _fields_scorer("number", "issue_date", "recipient_eik", "subtotal", "vat_amount", "total"),
        ),
        AgentSpec("report_matcher", matcher_v, matcher.build_graph, _no_score),
    ]
    return {s.name: s for s in specs}


def _prepare_input(agent: str, inp: dict[str, Any]) -> dict[str, Any]:
    if agent == "request_parser" and "today" not in inp:
        return {**inp, "today": clock.today_sofia().isoformat()}
    return inp


def run_case(spec: AgentSpec, graph: Any, case: Case) -> dict[str, Any]:
    usage = UsageMetadataCallbackHandler()
    started = time.monotonic()
    error: str | None = None
    out: dict[str, Any] | None = None
    try:
        state = graph.invoke({"input": _prepare_input(spec.name, case.input)}, config={"callbacks": [usage]})
        result = state["output"]
        out = result.model_dump(mode="json") if isinstance(result, BaseModel) else result
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:500]
    score = (
        CaseScore(metrics={"schema_valid": 0.0}, problems=[error or ""])
        if out is None
        else spec.scorer(case, out)
    )
    if out is not None:
        score.metrics["schema_valid"] = 1.0
    return {
        "id": case.id,
        "ok": out is not None and not score.problems,
        "error": error,
        "problems": score.problems,
        "metrics": score.metrics,
        "counts": score.counts,
        "qty_errors": score.qty_errors,
        "latency_ms": int((time.monotonic() - started) * 1000),
        "tokens_in": sum(u.get("input_tokens", 0) for u in usage.usage_metadata.values()),
        "tokens_out": sum(u.get("output_tokens", 0) for u in usage.usage_metadata.values()),
        "output": out,
        "difficulty": case.meta.get("difficulty"),
    }


def aggregate(results: list[dict[str, Any]]) -> dict[str, float | None]:
    """Дял по метрика (средно на случаите, където се прилага); `line_f1` е микро-средно по tp/fp/fn."""
    agg: dict[str, float | None] = {}
    names = {m for r in results for m in r["metrics"]}
    for name in sorted(names):
        values = [r["metrics"][name] for r in results if r["metrics"].get(name) is not None]
        agg[name] = sum(values) / len(values) if values else None
    tp, fp, fn = (sum(r["counts"].get(k, 0) for r in results) for k in ("tp", "fp", "fn"))
    if tp + fp + fn:
        agg["line_f1"] = 2 * tp / (2 * tp + fp + fn)
    mae = metrics.median_or_none([e for r in results for e in r["qty_errors"]])
    if mae is not None:
        agg["qty_mae_median"] = mae
    return agg


def check_gate(agent: str, agg: dict[str, float | None]) -> list[str]:
    failed = [
        f"{m} {agg[m]:.3f} < {need}"
        for m, need in GATES_MIN.get(agent, {}).items()
        if agg.get(m) is not None and (agg[m] or 0) < need
    ]
    for m, cap in GATES_MAX.items():
        if agg.get(m) is not None and (agg[m] or 0) > cap:
            failed.append(f"{m} {agg[m]:.3f} > {cap}")
    return failed


def run_eval(cases: list[Case], agents: list[str]) -> dict[str, Any]:
    specs = registry()
    out: dict[str, Any] = {}
    for name in agents:
        mine = [c for c in cases if c.agent == name]
        if not mine:
            continue
        spec = specs[name]
        graph = spec.build_graph()
        results = [run_case(spec, graph, c) for c in mine]
        agg = aggregate(results)
        out[name] = {
            "model": model_id(name),
            "prompt_version": spec.prompt_version,
            "n": len(results),
            "metrics": agg,
            "gate_failures": check_gate(name, agg),
            "cases": results,
        }
    return {"run_at": datetime.now(UTC).isoformat(timespec="seconds"), "agents": out}
