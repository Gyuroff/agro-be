import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session

from app.agents.base import run_agent
from app.agents.email_classifier.graph import AGENT, _validate, build_graph, decide
from app.agents.email_classifier.prompts import PROMPT_VERSION, render_user
from app.agents.email_classifier.schemas import ClassifierOutput, DocKind, EmailKind, ReplyStatus
from app.core.audit_context import AuditContext

_DIR = Path(__file__).parent.parent / "fixtures" / "classifier"
CASES: list[dict[str, Any]] = [
    c for f in sorted(_DIR.glob("*cases.json")) for c in json.loads(f.read_text(encoding="utf-8"))
]


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_classifier_stub_fixtures(db_session: Session, audit_ctx: AuditContext, case: dict[str, Any]) -> None:
    if case.get("pending_spec"):
        pytest.skip("видът чака решение за спецификацията")
    outcome = run_agent(db_session, AGENT, None, build_graph(), case["input"], PROMPT_VERSION)
    assert outcome.ok, outcome.error
    assert isinstance(outcome.output, ClassifierOutput)
    decision = decide(outcome.output)
    assert decision.kind == EmailKind(case["expected"])
    assert (decision.doc_kind.value if decision.doc_kind else None) == case.get("doc_kind")
    assert (decision.reply_status.value if decision.reply_status else None) == case.get("reply_status")


def test_all_kinds_covered() -> None:
    assert {c["expected"] for c in CASES if not c.get("pending_spec")} == {k.value for k in EmailKind}


def test_extra_fields_are_dropped_for_other_kinds() -> None:
    out = ClassifierOutput(
        kind=EmailKind.REQUEST,
        confidence=0.9,
        reason="ясно",
        doc_kind=DocKind.INVOICE,
        reply_status=ReplyStatus.CONFIRMED,
    )
    d = decide(out)
    assert d.doc_kind is None and d.reply_status is None


def test_missing_extra_fields_fail_validation() -> None:
    doc = ClassifierOutput(kind=EmailKind.SUPPLIER_DOC, confidence=0.9, reason="ясно")
    reply = ClassifierOutput(kind=EmailKind.REPLY, confidence=0.9, reason="ясно")
    assert _validate(doc, {}) and _validate(reply, {})
    assert not _validate(ClassifierOutput(kind=EmailKind.REQUEST, confidence=0.9, reason="ясно"), {})


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
