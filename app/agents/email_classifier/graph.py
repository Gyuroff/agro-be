"""Граф на `email_classifier` и правилото за ниска увереност (в кода, не в промпта)."""

from dataclasses import dataclass
from typing import Any

from app.agents.email_classifier.prompts import build_messages
from app.agents.email_classifier.schemas import ClassifierOutput, EmailKind
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
    # True → извикващият (mail.process_email) вдига карта unknown_sender с причина „несигурен вид“
    uncertain: bool


def decide(output: ClassifierOutput) -> ClassifierDecision:
    if output.confidence < MIN_CONFIDENCE:
        return ClassifierDecision(EmailKind.OTHER, output.confidence, UNCERTAIN_REASON, uncertain=True)
    return ClassifierDecision(output.kind, output.confidence, output.reason, uncertain=False)


def _validate(_output: ClassifierOutput, _inp: dict[str, Any]) -> list[str]:
    return []  # схемата (enum, 0..1) е цялата валидация


def build_graph():
    return build_extract_graph(get_structured_model(AGENT, ClassifierOutput), build_messages, _validate)
