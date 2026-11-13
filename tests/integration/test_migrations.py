"""AGENTS.md: `alembic upgrade head` и `alembic downgrade -1` трябва да минават.

Тестът пипа схемата на споделената agro_test, затова връща всичко в `head` накрая.
"""

from alembic import command
from sqlalchemy import Engine, inspect

from tests.conftest import alembic_config


def test_downgrade_one_and_upgrade(db_engine: Engine) -> None:
    cfg = alembic_config()
    command.downgrade(cfg, "-1")
    command.upgrade(cfg, "head")
    assert "actors" in inspect(db_engine).get_table_names()


def test_downgrade_base_and_upgrade(db_engine: Engine) -> None:
    cfg = alembic_config()
    command.downgrade(cfg, "base")
    try:
        assert "actors" not in inspect(db_engine).get_table_names()
    finally:
        command.upgrade(cfg, "head")
    assert "actors" in inspect(db_engine).get_table_names()


def test_extensions_installed(db_engine: Engine) -> None:
    with db_engine.connect() as conn:
        found = set(conn.exec_driver_sql("SELECT extname FROM pg_extension").scalars())
    assert {"pg_trgm", "citext"} <= found
