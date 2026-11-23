"""Граф на `email_classifier` и правилото за ниска увереност (в кода, не в промпта)."""

from dataclasses import dataclass
from typing import Any

from app.agents.email_classifier.prompts import build_messages
from app.agents.email_classifier.schemas import ClassifierOutput, DocKind, EmailKind, ReplyStatus
from app.agents.graph import build_extract_graph
from app.agents.llm import get_structured_model

AGENT = "email_classifier"
MIN_CONFIDENCE = 0.7
UNCERTAIN_REASON = "несигурен вид"


@dataclass(frozen=True)
class ClassifierDecision:
    kind: EmailKind
    confidence: float
    reason: str
    doc_kind: DocKind | None
    reply_status: ReplyStatus | None
    # True → извикващият (mail.process_email) вдига карта unknown_sender с причина „несигурен вид“
    uncertain: bool


def decide(output: ClassifierOutput) -> ClassifierDecision:
    if output.confidence < MIN_CONFIDENCE:
        return ClassifierDecision(
            EmailKind.OTHER, output.confidence, UNCERTAIN_REASON, None, None, uncertain=True
        )
    # допълнителните полета важат само за своя вид; чуждите се изхвърлят
    doc_kind = output.doc_kind if output.kind is EmailKind.SUPPLIER_DOC else None
    reply_status = output.reply_status if output.kind is EmailKind.REPLY else None
    return ClassifierDecision(
        output.kind, output.confidence, output.reason, doc_kind, reply_status, uncertain=False
    )


def _validate(output: ClassifierOutput, _inp: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    if output.kind is EmailKind.SUPPLIER_DOC and output.doc_kind is None:
        problems.append("за supplier_doc е нужно doc_kind (proforma или invoice)")
    if output.kind is EmailKind.REPLY and output.reply_status is None:
        problems.append("за reply е нужно reply_status (confirmed или needs_attention)")
    return problems


def build_graph():
    return build_extract_graph(get_structured_model(AGENT, ClassifierOutput), build_messages, _validate)
