"""pg_trgm, citext, таблица actors и сийд на системните актьори (agro-l2v.1)

Revision ID: 0001
Revises:
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# docs/data-model.md §1 — фиксирани кодове.
# Не променяй кодовете: на тях се опират одитът и агентите.
SEED_ACTORS: list[tuple[str, str, str]] = [
    ("system", "system:migration", "Миграция"),
    ("system", "system:seed", "Начални данни"),
    ("system", "system:scheduler", "График (Celery beat)"),
    ("system", "system:mail_ingest", "Четене на пощата"),
    ("system", "system:api", "API (анонимно — само за /auth/login)"),
    ("agent", "agent:email_classifier", "Агент: вид на писмото"),
    ("agent", "agent:request_parser", "Агент: заявки"),
    ("agent", "agent:report_parser", "Агент: справки"),
    ("agent", "agent:report_matcher", "Агент: съпоставяне на справка"),
    ("agent", "agent:cancellation_parser", "Агент: откази"),
    ("agent", "agent:mi_invoice_parser", "Агент: фактура от Microinvest"),
    ("agent", "agent:invoice_comparator", "Агент: сравнение на фактури"),
]


def upgrade() -> None:
    # Търсене по име в пикерите (gin gin_trgm_ops) — data-model §5
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")

    actors = op.create_table(
        "actors",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("kind IN ('user','agent','system')", name=op.f("ck_actors_kind")),
        sa.CheckConstraint(
            "kind <> 'user' OR user_id IS NOT NULL", name=op.f("ck_actors_user_requires_user_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_actors")),
        sa.UniqueConstraint("code", name=op.f("uq_actors_code")),
        sa.UniqueConstraint("user_id", name=op.f("uq_actors_user_id")),
    )

    op.bulk_insert(
        actors,
        [{"kind": kind, "code": code, "display_name": name} for kind, code, name in SEED_ACTORS],
    )


def downgrade() -> None:
    op.drop_table("actors")
    # pg_trgm и citext умишлено остават: може да се ползва от други обекти (безвреден).
