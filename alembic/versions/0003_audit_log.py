"""audit_log (само добавяне) + тригер audit_log_immutable (agro-l2v.2)

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("actor_id", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.BigInteger(), nullable=False),
        sa.Column("changes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "source IN ('api','admin','worker','agent','seed','migration','shell')",
            name=op.f("ck_audit_log_source"),
        ),
        sa.CheckConstraint("action IN ('create','update','delete')", name=op.f("ck_audit_log_action")),
        sa.ForeignKeyConstraint(["actor_id"], ["actors.id"], name=op.f("fk_audit_log_actor_id_actors")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_log")),
    )
    op.create_index(
        "ix_audit_log_entity", "audit_log", ["entity_type", "entity_id", sa.text("occurred_at DESC")]
    )
    op.create_index("ix_audit_log_actor", "audit_log", ["actor_id", sa.text("occurred_at DESC")])
    op.create_index("ix_audit_log_occurred_at", "audit_log", ["occurred_at"])

    op.execute(
        """
        CREATE FUNCTION audit_log_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_log_immutable
        BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION audit_log_immutable()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER audit_log_immutable ON audit_log")
    op.execute("DROP FUNCTION audit_log_immutable()")
    op.drop_table("audit_log")
