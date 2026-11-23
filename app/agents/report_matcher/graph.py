"""Граф на `report_matcher`: обяснява кандидатите от `suggest_substitutes`; не избира и не сменя нищо."""

from typing import Any

from app.agents.graph import build_extract_graph
from app.agents.llm import get_structured_model
from app.agents.report_matcher.prompts import build_messages
from app.agents.report_matcher.schemas import ReportMatcherOutput

AGENT = "report_matcher"


def _validate(output: ReportMatcherOutput, inp: dict[str, Any]) -> list[str]:
    expected = {c["unplanned_line_id"] for c in inp["candidates"]}
    got = [e.unplanned_line_id for e in output.explanations]
    problems = []
    if unknown := sorted(set(got) - expected):
        problems.append(f"непознати unplanned_line_id: {unknown}")
    if missing := sorted(expected - set(got)):
        problems.append(f"липсва обяснение за: {missing}")
    return problems


def build_graph():
    return build_extract_graph(get_structured_model(AGENT, ReportMatcherOutput), build_messages, _validate)
