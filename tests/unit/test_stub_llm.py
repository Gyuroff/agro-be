from pathlib import Path

import pytest
from pydantic import BaseModel

from app.agents.llm import (
    FIXTURES_DIR,
    StubChatModel,
    StubMissing,
    get_chat_model,
    input_digest,
    model_id,
    normalize_input,
    resolve_effort,
    resolve_model,
)


class Kind(BaseModel):
    kind: str


def test_stub_returns_fixture(tmp_path: Path) -> None:
    model = StubChatModel("email_classifier", tmp_path)
    messages = [("system", "Класифицирай"), ("human", "Заявка   за\nкамиони")]
    path = model.fixture_path(messages)
    path.parent.mkdir(parents=True)
    path.write_text('{"kind": "request"}', encoding="utf-8")
    # whitespace не променя хеша
    same = [("system", "Класифицирай"), ("human", "Заявка за камиони")]
    assert model.with_structured_output(Kind).invoke(same) == Kind(kind="request")


def test_stub_missing_reports_path_and_input(tmp_path: Path) -> None:
    model = StubChatModel("request_parser", tmp_path)
    messages = [("human", "непознато писмо")]
    with pytest.raises(StubMissing) as exc:
        model.with_structured_output(Kind).invoke(messages)
    expected = tmp_path / "request_parser" / f"{input_digest(messages)}.json"
    assert exc.value.path == expected
    assert str(expected) in str(exc.value)
    assert "human: непознато писмо" in str(exc.value)
    assert normalize_input(messages) in str(exc.value)


def test_get_chat_model_is_stub_in_tests() -> None:
    model = get_chat_model("email_classifier")
    assert isinstance(model, StubChatModel)
    assert model.fixtures_dir == FIXTURES_DIR
    assert model_id("email_classifier") == "stub"


def test_model_resolution_per_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    assert resolve_model("email_classifier") == "claude-haiku-5-5"
    assert resolve_model("cancellation_parser") == "claude-haiku-5-5"
    assert resolve_model("report_parser") == "claude-sonnet-5-5"
    assert resolve_effort("email_classifier") == "low"
    assert resolve_effort("request_parser") == "medium"
    monkeypatch.setenv("APP_LLM_MODEL_REPORT_PARSER", "claude-opus-5-5")
    get_settings.cache_clear()
    assert resolve_model("report_parser") == "claude-opus-5-5"


def test_anthropic_provider_builds_client(monkeypatch: pytest.MonkeyPatch) -> None:
    from langchain_anthropic import ChatAnthropic

    from app.core.config import get_settings

    monkeypatch.setenv("APP_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("APP_LLM_API_KEY", "sk-ant-test")
    get_settings.cache_clear()
    model = get_chat_model("email_classifier")
    assert isinstance(model, ChatAnthropic)
    assert model.model == "claude-haiku-5-5"
    assert model.max_retries == 2
    assert model_id("email_classifier") == "claude-haiku-5-5"
