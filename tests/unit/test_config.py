import pytest
from pydantic import ValidationError

from app.core.config import Env, LlmProvider, Settings, get_settings


def test_settings_from_env() -> None:
    settings = get_settings()
    assert settings.env is Env.TEST
    assert settings.llm_provider is LlmProvider.STUB
    assert settings.jwt_secret.get_secret_value() == "test-jwt-secret"


def test_secrets_are_masked() -> None:
    assert "test-jwt-secret" not in repr(get_settings())


def test_empty_value_is_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    # .env.example има празни стойности — не трябва да чупят числа/булеви
    monkeypatch.setenv("APP_IMAP_PORT", "")
    monkeypatch.setenv("APP_IMAP_SSL", "")
    settings = Settings()  # pyright: ignore[reportCallIssue]
    assert settings.imap_port == 993
    assert settings.imap_ssl is True


def test_missing_required_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("APP_DATABASE_URL")
    with pytest.raises(ValidationError):
        Settings()  # pyright: ignore[reportCallIssue]
