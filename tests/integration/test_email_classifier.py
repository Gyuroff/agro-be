import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.agents.base import run_agent
from app.agents.email_classifier.graph import AGENT, build_graph, decide
from app.agents.email_classifier.prompts import PROMPT_VERSION, render_user
from app.agents.email_classifier.schemas import ClassifierOutput, EmailKind
from app.core.audit_context import AuditContext

CASES: list[dict[str, Any]] = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "classifier" / "cases.json").read_text(encoding="utf-8")
)


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_classifier_stub_fixtures(db_session: Session, audit_ctx: AuditContext, case: dict[str, Any]) -> None:
    outcome = run_agent(db_session, AGENT, None, build_graph(), case["input"], PROMPT_VERSION)
    assert outcome.ok, outcome.error
    assert isinstance(outcome.output, ClassifierOutput)
    assert decide(outcome.output).kind == EmailKind(case["expected"])


def test_all_five_kinds_covered() -> None:
    assert {c["expected"] for c in CASES} == {k.value for k in EmailKind}


def test_low_confidence_becomes_other() -> None:
    d = decide(ClassifierOutput(kind=EmailKind.REQUEST, confidence=0.69, reason="неясно"))
    assert d.kind is EmailKind.OTHER and d.uncertain and d.reason == "несигурен вид"


def test_threshold_is_inclusive() -> None:
    d = decide(ClassifierOutput(kind=EmailKind.REPORT, confidence=0.7, reason="ясно"))
    assert d.kind is EmailKind.REPORT and not d.uncertain


def test_render_user_limits_body_and_xlsx_lines() -> None:
    text = render_user(
        {
            "from_address": "a@b.bg",
            "subject": "s",
            "body_text": "x" * 5000,
            "attachments": [{"name": "t.xlsx", "tsv": "\n".join(f"r{i}" for i in range(100))}],
        }
    )
    assert "x" * 4000 in text and "x" * 4001 not in text
    assert "r29" in text and "r30" not in text
