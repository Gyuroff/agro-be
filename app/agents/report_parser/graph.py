"""Граф на `report_parser`: само извличане; нормализацията и съпоставянето са в `report_intake`."""

from typing import Any

from app.agents.graph import build_extract_graph
from app.agents.llm import get_structured_model
from app.agents.report_parser.prompts import build_messages
from app.agents.report_parser.schemas import ReportParserOutput

AGENT = "report_parser"


def _validate(output: ReportParserOutput, _inp: dict[str, Any]) -> list[str]:
    return [] if output.lines else ["няма нито един курс"]


def build_graph():
    return build_extract_graph(get_structured_model(AGENT, ReportParserOutput), build_messages, _validate)
