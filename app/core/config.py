"""Конфигурация от env (префикс APP_). Ключовете са от docs/architecture.md §6.

Без os.environ.get извън този модул. Празна стойност в .env (както в .env.example)
се третира като незададена (env_ignore_empty).
"""

from enum import StrEnum
from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Env(StrEnum):
    DEV = "dev"
    TEST = "test"
    PROD = "prod"


class LlmProvider(StrEnum):
    ANTHROPIC = "anthropic"
    STUB = "stub"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="APP_",
        env_file=".env",
        env_ignore_empty=True,
        extra="forbid",
        frozen=True,
    )

    env: Env = Env.DEV

    # Задължителни
    database_url: str
    redis_url: str
    jwt_secret: SecretStr
    admin_session_secret: SecretStr

    # Файлове (S3 първо; локалната папка е само опашка при S3 fail — §11)
    storage_dir: str = "/var/lib/agro/storage"
    s3_endpoint: str | None = None
    s3_region: str | None = None
    s3_bucket: str | None = None
    s3_access_key: SecretStr | None = None
    s3_secret_key: SecretStr | None = None

    # Бекъп (§11)
    backup_s3_endpoint: str | None = None
    backup_s3_region: str | None = None
    backup_s3_bucket: str | None = None
    backup_s3_access_key: SecretStr | None = None
    backup_s3_secret_key: SecretStr | None = None
    backup_ssh_host: str | None = None
    backup_ssh_user: str | None = None
    backup_ssh_key_file: str | None = None

    # Поща
    imap_host: str | None = None
    imap_port: int = 993
    imap_user: str | None = None
    imap_password: SecretStr | None = None
    imap_ssl: bool = True
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None
    smtp_starttls: bool = True
    mail_authserv_id: str | None = None

    # LLM (docs/llm.md §2): модел и effort по агент; празно → llm_model_default / llm_effort_default.
    llm_provider: LlmProvider = LlmProvider.STUB
    llm_api_key: SecretStr | None = None
    llm_model_default: str = "claude-sonnet-5-5"
    llm_model_email_classifier: str = "claude-haiku-5-5"
    llm_model_request_parser: str | None = None
    llm_model_report_parser: str | None = None
    llm_model_report_matcher: str | None = None
    llm_model_cancellation_parser: str = "claude-haiku-5-5"
    llm_model_mi_invoice_parser: str | None = None
    llm_model_invoice_comparator: str | None = None
    llm_effort_default: str = "medium"
    llm_effort_email_classifier: str = "low"
    llm_effort_cancellation_parser: str = "low"

    # Пуш
    fcm_credentials_file: str | None = None
    hms_app_id: str | None = None
    hms_client_id: str | None = None
    hms_client_secret: SecretStr | None = None

    # Други
    ingest_token: SecretStr | None = None
    mi_test_export_dir: str | None = None
    mi_prod_export_dir: str | None = None
    cors_origins: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]  # задължителните идват от env
