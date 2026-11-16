"""Граф на `request_parser`: само извличане; правилата (номера, дати, договор) са в `request_intake`."""

from typing import Any

from app.agents.graph import build_extract_graph
from app.agents.llm import get_structured_model
from app.agents.request_parser.prompts import build_messages
from app.agents.request_parser.schemas import RequestParserOutput

AGENT = "request_parser"


def _validate(output: RequestParserOutput, _inp: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if not output.lines:
        problems.append("няма нито един ред (камион)")
    if output.period_from and output.period_to and output.period_from > output.period_to:
        problems.append("period_from е след period_to")
    return problems


def build_graph():
    return build_extract_graph(get_structured_model(AGENT, RequestParserOutput), build_messages, _validate)
