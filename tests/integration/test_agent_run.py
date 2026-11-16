from typing import Any

import pytest
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.base import run_agent
from app.agents.graph import build_extract_graph
from app.core.audit_context import AuditContext
from app.models.cards import AgentRun


class Kind(BaseModel):
    kind: str


def _messages(inp: dict[str, Any]) -> list[tuple[str, str]]:
    return [("human", inp["text"])]


def _graph(answers: list[str]):
    calls = iter(answers)
    model = RunnableLambda(lambda _m: Kind(kind=next(calls)))
    return build_extract_graph(
        model,
        _messages,
        lambda out, _inp: [] if out.kind in {"request", "report"} else [f"непознат вид {out.kind}"],
    )


def test_run_agent_ok(db_session: Session, audit_ctx: AuditContext) -> None:
    outcome = run_agent(
        db_session,
        "email_classifier",
        ("inbound_emails", 7),
        _graph(["request"]),
        {"text": "hi"},
        "email_classifier@1",
    )
    assert outcome.ok and outcome.output == Kind(kind="request")
    run = db_session.get(AgentRun, outcome.run_id)
    assert run is not None
    assert (run.status, run.agent, run.model, run.prompt_version) == (
        "ok",
        "email_classifier",
        "stub",
        "email_classifier@1",
    )
    assert (run.subject_type, run.subject_id) == ("inbound_emails", 7)
    assert run.output == {"kind": "request"}
    assert run.error is None and run.duration_ms is not None and run.tokens_in == 0


def test_run_agent_retries_once_then_ok(db_session: Session, audit_ctx: AuditContext) -> None:
    seen: list[list[Any]] = []

    def model(m: list[Any]) -> Kind:
        seen.append(m)
        return Kind(kind="смесено" if len(seen) == 1 else "report")

    graph = build_extract_graph(
        RunnableLambda(model), _messages, lambda o, _i: [] if o.kind == "report" else ["вид"]
    )
    outcome = run_agent(db_session, "email_classifier", None, graph, {"text": "x"}, "v1")
    assert outcome.ok and len(seen) == 2
    assert "поправи" in seen[1][-1][1]


def test_run_agent_error_is_recorded_not_raised(db_session: Session, audit_ctx: AuditContext) -> None:
    outcome = run_agent(
        db_session, "request_parser", None, _graph(["x", "y"]), {"text": "hi"}, "request_parser@1"
    )
    assert not outcome.ok and outcome.output is None
    run = db_session.scalars(select(AgentRun).where(AgentRun.id == outcome.run_id)).one()
    assert run.status == "error"
    assert run.output is None
    assert run.error is not None and "AgentValidationError" in run.error


def test_run_agent_writes_with_agent_actor(db_session: Session, audit_ctx: AuditContext) -> None:
    from app.models.identity import Actor

    outcome = run_agent(db_session, "report_parser", None, _graph(["report"]), {"text": "t"}, "v1")
    run = db_session.get(AgentRun, outcome.run_id)
    assert run is not None
    actor = db_session.scalar(select(Actor.code).where(Actor.id == run.created_by_id))
    assert actor == "agent:report_parser"


def test_run_agent_rejects_unknown_agent(db_session: Session, audit_ctx: AuditContext) -> None:
    with pytest.raises(ValueError):
        run_agent(db_session, "nope", None, _graph(["request"]), {"text": "t"}, "v1")
