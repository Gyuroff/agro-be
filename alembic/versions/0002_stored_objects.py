"""stored_objects (agro-1k3)

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-13
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "stored_objects",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("s3_key", sa.Text(), nullable=False),
        sa.Column("local_path", sa.Text(), nullable=True),
        sa.Column("location", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.Text(), nullable=True),
        sa.Column("upload_attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_upload_error", sa.Text(), nullable=True),
        sa.Column("last_upload_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_by_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by_id", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('email_raw','attachment','invoice_pdf','mi_xml','other')",
            name=op.f("ck_stored_objects_kind"),
        ),
        sa.CheckConstraint("location IN ('s3','local')", name=op.f("ck_stored_objects_location")),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["actors.id"], name=op.f("fk_stored_objects_created_by_id_actors")
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"], ["actors.id"], name=op.f("fk_stored_objects_updated_by_id_actors")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_stored_objects")),
        sa.UniqueConstraint("s3_key", name=op.f("uq_stored_objects_s3_key")),
    )
    # FK от email_attachments / inbound_emails / invoices / microinvest_exports към stored_objects.id
    # се добавят в миграциите на агро-l2v.6 / l2v.10 (тези таблици още не съществуват).


def downgrade() -> None:
    op.drop_table("stored_objects")
