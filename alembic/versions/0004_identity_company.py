"""users, devices, refresh_tokens, company_settings, number_sequences

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-15 11:44:02.868518
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("short_name", sa.Text(), nullable=False),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_admin", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("push_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("failed_login_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_by_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["created_by_id"], ["actors.id"], name=op.f("fk_users_created_by_id_actors")),
        sa.ForeignKeyConstraint(["updated_by_id"], ["actors.id"], name=op.f("fk_users_updated_by_id_actors")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_table(
        "company_settings",
        sa.Column("id", sa.BigInteger(), autoincrement=False, nullable=False),
        sa.Column("legal_name", sa.Text(), nullable=False),
        sa.Column("short_name", sa.Text(), nullable=False),
        sa.Column("eik", sa.Text(), nullable=False),
        sa.Column("vat_number", sa.Text(), nullable=True),
        sa.Column("address_city", sa.Text(), nullable=False),
        sa.Column("address_line", sa.Text(), nullable=False),
        sa.Column("mol", sa.Text(), nullable=False),
        sa.Column("bank_name", sa.Text(), nullable=True),
        sa.Column("iban", sa.Text(), nullable=True),
        sa.Column("bic", sa.Text(), nullable=True),
        sa.Column("email_signature", sa.Text(), nullable=False),
        sa.Column("system_mailbox_address", sa.Text(), nullable=False),
        sa.Column(
            "default_vat_rate",
            sa.Numeric(precision=5, scale=2),
            server_default=sa.text("20.00"),
            nullable=False,
        ),
        sa.Column(
            "default_planned_truck_qty_t",
            sa.Numeric(precision=12, scale=3),
            server_default=sa.text("25.200"),
            nullable=False,
        ),
        sa.Column(
            "invoice_rounding_tolerance_eur",
            sa.Numeric(precision=14, scale=2),
            server_default=sa.text("0.01"),
            nullable=False,
        ),
        sa.Column("mi_invoice_grace_days", sa.Integer(), server_default=sa.text("2"), nullable=False),
        sa.Column("mailbox_alarm_minutes", sa.Integer(), server_default=sa.text("60"), nullable=False),
        sa.Column("email_undo_seconds", sa.Integer(), server_default=sa.text("10"), nullable=False),
        sa.Column("app_lock_background_minutes", sa.Integer(), server_default=sa.text("5"), nullable=False),
        sa.Column("biometric_max_attempts", sa.Integer(), server_default=sa.text("3"), nullable=False),
        sa.Column("shadow_cycles_required", sa.Integer(), server_default=sa.text("3"), nullable=False),
        sa.Column("push_quiet_from", sa.Time(), server_default=sa.text("'21:00'"), nullable=False),
        sa.Column("push_quiet_to", sa.Time(), server_default=sa.text("'07:00'"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_by_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by_id", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_company_settings_single_row")),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["actors.id"], name=op.f("fk_company_settings_created_by_id_actors")
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"], ["actors.id"], name=op.f("fk_company_settings_updated_by_id_actors")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_company_settings")),
    )
    op.create_table(
        "devices",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("installation_id", sa.Text(), nullable=False),
        sa.Column("platform", sa.Text(), nullable=False),
        sa.Column("device_model", sa.Text(), nullable=True),
        sa.Column("os_version", sa.Text(), nullable=True),
        sa.Column("app_version", sa.Text(), nullable=True),
        sa.Column("push_provider", sa.Text(), server_default="none", nullable=False),
        sa.Column("push_token", sa.Text(), nullable=True),
        sa.Column("push_token_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("biometric_enrolled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_by_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by_id", sa.BigInteger(), nullable=False),
        sa.CheckConstraint("platform IN ('ios','android')", name=op.f("ck_devices_platform")),
        sa.CheckConstraint("push_provider IN ('fcm','hms','none')", name=op.f("ck_devices_push_provider")),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["actors.id"], name=op.f("fk_devices_created_by_id_actors")
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"], ["actors.id"], name=op.f("fk_devices_updated_by_id_actors")
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_devices_user_id_users")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_devices")),
        sa.UniqueConstraint("user_id", "installation_id", name="user_installation"),
    )
    op.create_table(
        "number_sequences",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("code", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("next_number", sa.BigInteger(), nullable=False),
        sa.Column("range_start", sa.BigInteger(), nullable=True),
        sa.Column("range_end", sa.BigInteger(), nullable=True),
        sa.Column("width", sa.Integer(), server_default=sa.text("10"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_by_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by_id", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "range_start IS NULL OR range_end IS NULL OR next_number BETWEEN range_start AND range_end",
            name=op.f("ck_number_sequences_next_in_range"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["actors.id"], name=op.f("fk_number_sequences_created_by_id_actors")
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"], ["actors.id"], name=op.f("fk_number_sequences_updated_by_id_actors")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_number_sequences")),
        sa.UniqueConstraint("code", name=op.f("uq_number_sequences_code")),
    )
    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("device_id", sa.BigInteger(), nullable=False),
        sa.Column("token_hash", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_by_id", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("created_by_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["actors.id"], name=op.f("fk_refresh_tokens_created_by_id_actors")
        ),
        sa.ForeignKeyConstraint(
            ["device_id"], ["devices.id"], name=op.f("fk_refresh_tokens_device_id_devices")
        ),
        sa.ForeignKeyConstraint(
            ["updated_by_id"], ["actors.id"], name=op.f("fk_refresh_tokens_updated_by_id_actors")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_refresh_tokens")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_refresh_tokens_token_hash")),
    )
    op.create_foreign_key(op.f("fk_actors_user_id_users"), "actors", "users", ["user_id"], ["id"])

    # Единствените данни, без които системата не тръгва (docs/data-model.md §4).
    # Неизвестните реквизити (ЕИК, МОЛ, адрес, подпис, пощенска кутия) са празни — попълват се от админа.
    op.execute(
        """
        INSERT INTO company_settings (
            id, legal_name, short_name, eik, address_city, address_line, mol,
            email_signature, system_mailbox_address, created_by_id, updated_by_id
        )
        SELECT 1, 'Агро Импорт Трейдинг ООД', 'Агро Импорт Трейдинг', '', '', '', '', '', '', a.id, a.id
        FROM actors a WHERE a.code = 'system:migration'
        """
    )
    op.execute(
        """
        INSERT INTO number_sequences (
            code, description, next_number, range_start, range_end, created_by_id, updated_by_id
        )
        SELECT v.code, v.description, v.next_number, v.range_start, v.range_end, a.id, a.id
        FROM actors a,
             (VALUES
                ('invoice_main', 'Фактури (основна номерация)', 1, NULL::bigint, NULL::bigint),
                ('invoice_pilot', 'Фактури (пилот, отделен диапазон)', 9000000001, 9000000001, 9000009999),
                ('email_ref', 'Номер на писмо (АИТ-NNNN)', 1, NULL::bigint, NULL::bigint)
             ) AS v(code, description, next_number, range_start, range_end)
        WHERE a.code = 'system:migration'
        """
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_actors_user_id_users"), "actors", type_="foreignkey")
    op.drop_table("refresh_tokens")
    op.drop_table("number_sequences")
    op.drop_table("devices")
    op.drop_table("company_settings")
    op.drop_table("users")
