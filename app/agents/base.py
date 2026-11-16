"""`run_agent` — изпълнение на граф с проследяване в `agent_runs` (architecture §7, data-model §13).

Запис в `agent_runs` е под одитен контекст `agent:<name>` (source=agent). Грешка в графа НЕ се
хвърля нагоре: иначе rollback на извикващия би изтрил и реда за грешката. Връща `AgentOutcome`;
извикващият решава (напр. карта със „неуверено“).
"""

import time
from dataclasses import dataclass
from typing import Any

from langchain_core.callbacks import UsageMetadataCallbackHandler
from langgraph.graph.state import CompiledStateGraph
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.agents.llm import model_id
from app.core.audit_context import AuditSource, actor_ctx_by_code, audit_context, get_audit_context
from app.models.cards import AgentName, AgentRun, AgentRunStatus

MAX_ERROR_LEN = 2000


@dataclass(frozen=True)
class AgentOutcome:
    run_id: int
    ok: bool
    output: BaseModel | None
    error: str | None


def _dump(value: Any) -> dict[str, Any] | None:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return value


def run_agent(
    session: Session,
    agent_name: AgentName | str,
    subject: tuple[str, int] | None,
    graph: CompiledStateGraph,
    input: dict[str, Any],
    prompt_version: str,
    *,
    input_summary: dict[str, Any] | None = None,
) -> AgentOutcome:
    """`subject` = (`inbound_emails`, 812). Графът получава `{"input": input}`; изход: `state["output"]`."""
    agent = AgentName(agent_name).value
    ctx = actor_ctx_by_code(
        f"agent:{agent}",
        AuditSource.AGENT,
        session=session,
        request_id=_current_request_id(),
        reason=f"agent_run:{agent}",
    )
    with audit_context(ctx):
        run = AgentRun(
            agent=agent,
            subject_type=subject[0] if subject else None,
            subject_id=subject[1] if subject else None,
            status=AgentRunStatus.RUNNING.value,
            model=model_id(agent),
            prompt_version=prompt_version,
            input_summary=input_summary,
        )
        session.add(run)
        session.flush()

        usage = UsageMetadataCallbackHandler()
        started = time.monotonic()
        output: BaseModel | None = None
        error: str | None = None
        try:
            state = graph.invoke({"input": input}, config={"callbacks": [usage]})
            output = state.get("output")
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:MAX_ERROR_LEN]

        run.duration_ms = int((time.monotonic() - started) * 1000)
        run.tokens_in = sum(u.get("input_tokens", 0) for u in usage.usage_metadata.values())
        run.tokens_out = sum(u.get("output_tokens", 0) for u in usage.usage_metadata.values())
        run.status = (AgentRunStatus.ERROR if error else AgentRunStatus.OK).value
        run.output = _dump(output)
        run.error = error
        session.flush()
        return AgentOutcome(run_id=run.id, ok=error is None, output=output, error=error)


def _current_request_id() -> str | None:
    try:
        return get_audit_context().request_id
    except Exception:
        return None
