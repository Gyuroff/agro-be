"""Преименуване mi_* / microinvest_* → mega_* (agro-6xw, част от agro-bpi)

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-23 12:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (таблица, constraint без префикса ck_<table>_, колона, [(старо, ново)], пълен списък нови стойности)
_KINDS_MAIL = [
    "request",
    "report",
    "cancellation",
    "{}_invoice",
    "supplier_doc",
    "inquiry",
    "payment_question",
    "reply",
    "other",
]
_AGENTS = [
    "email_classifier",
    "request_parser",
    "report_parser",
    "report_matcher",
    "cancellation_parser",
    "{}_invoice_parser",
    "invoice_comparator",
]
_RESOLUTIONS = [
    "sent",
    "rejected",
    "confirmed",
    "kept",
    "linked",
    "ignored",
    "hidden",
    "issued",
    "{}_correct",
    "notify_accountant",
    "retried",
]
_STORED = ["email_raw", "attachment", "invoice_pdf", "{}_xml", "other"]

# (таблица, constraint, колона, шаблон)
_ENUMS = [
    ("inbound_emails", "kind", "kind", _KINDS_MAIL, "{}_invoice"),
    ("agent_runs", "agent", "agent", _AGENTS, "{}_invoice_parser"),
    ("action_cards", "resolution", "resolution", _RESOLUTIONS, "{}_correct"),
    ("stored_objects", "kind", "kind", _STORED, "{}_xml"),
]


def _check(column: str, values: list[str], prefix: str) -> str:
    return f"{column} IN (" + ",".join("'" + v.format(prefix) + "'" for v in values) + ")"


def _rename_enums(old: str, new: str) -> None:
    for table, constraint, column, values, tmpl in _ENUMS:
        op.drop_constraint(constraint, table, type_="check")
        op.execute(
            f"UPDATE {table} SET {column} = '{tmpl.format(new)}' WHERE {column} = '{tmpl.format(old)}'"
        )
        op.create_check_constraint(constraint, table, _check(column, values, new))


def _rename(old: str, new: str, long_old: str, long_new: str) -> None:
    """old/new — „mi“/„mega“ (кратко); long_old/long_new — „microinvest“/„mega“ (в колоните на код)."""
    op.drop_constraint(
        "erp_needs_mi_code" if old == "mi" else "erp_needs_mega_code", "counterparties", type_="check"
    )
    for table in ("counterparties", "products"):
        op.alter_column(table, f"{long_old}_code", new_column_name=f"{long_new}_code")
        op.execute(
            f"ALTER TABLE {table} RENAME CONSTRAINT uq_{table}_{long_old}_code TO uq_{table}_{long_new}_code"
        )
    op.create_check_constraint(
        "erp_needs_mega_code" if new == "mega" else "erp_needs_mi_code",
        "counterparties",
        f"invoicing_mode <> 'erp' OR {long_new}_code IS NOT NULL",
    )
    op.alter_column(
        "company_settings",
        f"{old}_invoice_grace_working_days",
        new_column_name=f"{new}_invoice_grace_working_days",
    )
    op.execute(
        f"UPDATE actors SET code = 'agent:{new}_invoice_parser' WHERE code = 'agent:{old}_invoice_parser'"
    )
    op.execute(
        f"UPDATE action_cards SET dedupe_key = replace(dedupe_key, '{old}_missing:', '{new}_missing:') "
        f"WHERE dedupe_key LIKE '{old}\\_missing:%'"
    )


def upgrade() -> None:
    _rename_enums("mi", "mega")
    _rename("mi", "mega", "microinvest", "mega")
    op.execute(
        "UPDATE actors SET display_name = 'Агент: фактура от Мега' WHERE code = 'agent:mega_invoice_parser'"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE actors SET display_name = 'Агент: фактура от Microinvest' "
        "WHERE code = 'agent:mega_invoice_parser'"
    )
    _rename_enums("mega", "mi")
    _rename("mega", "mi", "mega", "microinvest")
