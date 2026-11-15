import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session

from app.core.audit_context import AuditContext, AuditSource, audit_context
from app.core.config import get_settings
from app.main import create_app
from app.models.identity import Actor

ROOT = Path(__file__).resolve().parent.parent

# Postgres от docker-compose (база agro_test). При друг порт: TEST_DATABASE_URL=... pytest
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://agro:agro@localhost:5432/agro_test"
)

TEST_ENV = {
    "APP_ENV": "test",
    "APP_DATABASE_URL": TEST_DATABASE_URL,
    "APP_REDIS_URL": "redis://localhost:6379/1",
    "APP_JWT_SECRET": "test-jwt-secret",
    "APP_ADMIN_SESSION_SECRET": "test-admin-secret",
    "APP_LLM_PROVIDER": "stub",
}


@pytest.fixture(autouse=True)
def _settings_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Изолирани настройки за всеки тест; без реален .env, без истински LLM."""
    monkeypatch.chdir("/")  # env_file=".env" да не хваща файла от проекта
    for key, value in TEST_ENV.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def alembic_config() -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.attributes["url"] = TEST_DATABASE_URL
    return cfg


@pytest.fixture(scope="session")
def db_engine() -> Iterator[Engine]:
    """Схемата се прави с `alembic upgrade head` веднъж на сесия (architecture §10)."""
    command.upgrade(alembic_config(), "head")
    engine = create_engine(TEST_DATABASE_URL)
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(db_engine: Engine) -> Iterator[Session]:
    """Всеки тест е в транзакция със savepoint-и и rollback в края."""
    connection = db_engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        transaction.rollback()
        connection.close()


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def audit_ctx(db_session: Session) -> Iterator[AuditContext]:
    """Одитен контекст `system:seed` / `seed` за тестове, които пишат в одитирани таблици."""
    actor_id = db_session.scalar(select(Actor.id).where(Actor.code == "system:seed"))
    assert actor_id is not None
    with audit_context(AuditContext(actor_id, AuditSource.SEED, request_id="test-req")) as ctx:
        yield ctx
