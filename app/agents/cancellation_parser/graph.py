"""Граф на `cancellation_parser`: само извличане; намирането на курсовете е в `cancellation_intake`."""

from typing import Any

from app.agents.cancellation_parser.prompts import build_messages
from app.agents.cancellation_parser.schemas import CancellationParserOutput
from app.agents.graph import build_extract_graph
from app.agents.llm import get_structured_model

AGENT = "cancellation_parser"


def _validate(output: CancellationParserOutput, _inp: dict[str, Any]) -> list[str]:
    return [] if output.plates else ["няма нито един номер"]


def build_graph():
    return build_extract_graph(
        get_structured_model(AGENT, CancellationParserOutput), build_messages, _validate
    )
