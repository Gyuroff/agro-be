"""LLM клиент (docs/llm.md §2, architecture §7).

`anthropic` → `ChatAnthropic`; `stub` → `StubChatModel` с fixtures (CI и pytest, без мрежа).
Изходът винаги е структуриран: `get_structured_model(agent, Schema)`.
"""

import hashlib
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Protocol, cast

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import BaseMessage
from langchain_core.runnables import Runnable, RunnableLambda
from pydantic import BaseModel, SecretStr

from app.core.config import LlmProvider, Settings, get_settings

FIXTURES_DIR = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures" / "llm"
STUB_MODEL_ID = "stub"

type Messages = str | Sequence[BaseMessage | tuple[str, str] | dict[str, Any]]


class StubMissing(LookupError):
    """Няма fixture за този вход. Съобщението съдържа пътя и входа — създай файла с очаквания JSON."""

    def __init__(self, path: Path, normalized_input: str) -> None:
        self.path = path
        self.normalized_input = normalized_input
        super().__init__(f"Липсва fixture за stub LLM: {path}\nВход (нормализиран):\n{normalized_input}")


class StructuredModel(Protocol):
    def with_structured_output(self, schema: type[BaseModel], **kwargs: Any) -> Runnable[Any, Any]: ...


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    parts = [c if isinstance(c, str) else str(c.get("text", "")) for c in content]
    return "\n".join(parts)


def normalize_input(messages: Messages) -> str:
    """Стабилен текст на входа: роля + съдържание, whitespace свит — fixture-ите не се чупят от интервали."""
    if isinstance(messages, str):
        items: list[tuple[str, str]] = [("human", messages)]
    else:
        items = []
        for m in messages:
            if isinstance(m, BaseMessage):
                items.append((m.type, _content_text(m.content)))
            elif isinstance(m, tuple):
                items.append((m[0], m[1]))
            else:
                items.append((str(m.get("role", "human")), _content_text(m.get("content", ""))))
    return "\n".join(f"{role}: {re.sub(r'\s+', ' ', text).strip()}" for role, text in items)


def input_digest(messages: Messages) -> str:
    return hashlib.sha1(normalize_input(messages).encode("utf-8")).hexdigest()


class StubChatModel:
    """Подменя `ChatAnthropic` в тестове: отговорът е `tests/fixtures/llm/<agent>/<sha1>.json`."""

    def __init__(self, agent: str, fixtures_dir: Path = FIXTURES_DIR) -> None:
        self.agent = agent
        self.fixtures_dir = fixtures_dir

    def fixture_path(self, messages: Messages) -> Path:
        return self.fixtures_dir / self.agent / f"{input_digest(messages)}.json"

    def with_structured_output(self, schema: type[BaseModel], **_: Any) -> Runnable[Any, Any]:
        def invoke(messages: Messages) -> BaseModel:
            path = self.fixture_path(messages)
            if not path.is_file():
                raise StubMissing(path, normalize_input(messages))
            return schema.model_validate_json(path.read_text(encoding="utf-8"))

        return RunnableLambda(invoke)


def resolve_model(agent: str, settings: Settings | None = None) -> str:
    s = settings or get_settings()
    return getattr(s, f"llm_model_{agent}", None) or s.llm_model_default


def resolve_effort(agent: str, settings: Settings | None = None) -> str:
    s = settings or get_settings()
    return getattr(s, f"llm_effort_{agent}", None) or s.llm_effort_default


def model_id(agent: str) -> str:
    """Какво се записва в `agent_runs.model`."""
    s = get_settings()
    return STUB_MODEL_ID if s.llm_provider is LlmProvider.STUB else resolve_model(agent, s)


def get_chat_model(agent: str) -> StructuredModel:
    s = get_settings()
    if s.llm_provider is LlmProvider.STUB:
        return StubChatModel(agent)
    key: SecretStr | None = s.llm_api_key
    if key is None:
        raise RuntimeError("APP_LLM_API_KEY не е зададен, а APP_LLM_PROVIDER=anthropic.")
    return ChatAnthropic(
        model_name=resolve_model(agent, s),
        api_key=key,
        timeout=60,
        max_retries=2,
        stop=None,
        effort=cast(Any, resolve_effort(agent, s)),  # low|medium|high|xhigh|max
    )


def get_structured_model(agent: str, schema: type[BaseModel]) -> Runnable[Any, Any]:
    """Модел с native structured output (Claude `json_schema`); stub чете fixtures."""
    return get_chat_model(agent).with_structured_output(schema, method="json_schema")
