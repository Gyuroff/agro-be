"""Общ граф на агента: extract → validate → (retry ≤1) → END (architecture §7, best-practices B5).

Агентът само извлича. Валидацията е код (`validate(output) -> [проблем, …]`); при проблеми
извличането се повтаря веднъж с подсказка какво е сгрешено, после — `AgentValidationError`.
"""

from collections.abc import Callable
from typing import Any, TypedDict

from langchain_core.messages import BaseMessage
from langchain_core.runnables import Runnable
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

MAX_RETRIES = 1


class AgentValidationError(Exception):
    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("Изходът на агента не мина валидацията: " + "; ".join(problems))


class ExtractState(TypedDict):
    input: dict[str, Any]
    output: BaseModel | None
    problems: list[str]
    retries: int


def build_extract_graph(
    model: Runnable[Any, Any],
    build_messages: Callable[[dict[str, Any]], list[BaseMessage] | list[tuple[str, str]]],
    validate: Callable[[Any, dict[str, Any]], list[str]],
):
    """`model` е вече със structured output; `build_messages(input)` прави промпта."""

    def extract(state: ExtractState) -> dict[str, Any]:
        messages = list(build_messages(state["input"]))
        if problems := state.get("problems"):
            messages.append(("human", "Предишният отговор има грешки, поправи ги: " + "; ".join(problems)))
        return {"output": model.invoke(messages)}

    def check(state: ExtractState) -> dict[str, Any]:
        problems = validate(state["output"], state["input"])
        retries = state.get("retries", 0) + (1 if problems else 0)
        return {"problems": problems, "retries": retries}

    def route(state: ExtractState) -> str:
        if not state["problems"]:
            return "done"
        return "retry" if state["retries"] <= MAX_RETRIES else "fail"

    def fail(state: ExtractState) -> dict[str, Any]:
        raise AgentValidationError(state["problems"])

    g = StateGraph(ExtractState)
    g.add_node("extract", extract)
    g.add_node("validate", check)
    g.add_node("fail", fail)
    g.add_edge(START, "extract")
    g.add_edge("extract", "validate")
    g.add_conditional_edges("validate", route, {"done": END, "retry": "extract", "fail": "fail"})
    g.add_edge("fail", END)
    return g.compile()
