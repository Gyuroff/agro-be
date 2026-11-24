"""Граф на `mega_invoice_parser`: само извличане; аритметиката се проверява в `mega_invoice_intake`."""

from app.agents.graph import build_extract_graph
from app.agents.llm import get_structured_model
from app.agents.mega_invoice_parser.prompts import build_messages
from app.agents.mega_invoice_parser.schemas import MegaInvoiceParserOutput

AGENT = "mega_invoice_parser"


def build_graph():
    return build_extract_graph(
        get_structured_model(AGENT, MegaInvoiceParserOutput), build_messages, lambda _out, _inp: []
    )
