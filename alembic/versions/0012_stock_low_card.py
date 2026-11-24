"""stock_low card kind

Revision ID: 0012
Revises: 0011
Create Date: 2026-11-16 17:00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BASE = [
    "client_request",
    "supplier_report",
    "cancellation",
    "invoice_due",
    "invoice_mismatch",
    "invoice_missing",
    "unknown_sender",
    "contract_over",
    "export_error",
    "mailbox_alarm",
    "storage_upload_failed",
    "inquiry",
    "one_shot_send",
    "supplier_doc",
    "one_shot_pay_supplier",
    "supplier_advance_low",
]


def _in(kinds: list[str]) -> str:
    return "kind IN (" + ",".join(f"'{k}'" for k in kinds) + ")"


def upgrade() -> None:
    for table in ("action_cards", "push_notifications"):
        op.drop_constraint("kind", table, type_="check")
    op.create_check_constraint("kind", "action_cards", _in([*_BASE, "stock_low"]))
    op.create_check_constraint(
        "kind", "push_notifications", _in([*_BASE, "stock_low", "send_failed", "digest"])
    )


def downgrade() -> None:
    # Старият CHECK не познава `stock_low`: картите (и пушовете към тях) се махат, иначе връщането гърми.
    op.execute(
        "DELETE FROM push_notifications WHERE kind = 'stock_low' "
        "OR action_card_id IN (SELECT id FROM action_cards WHERE kind = 'stock_low')"
    )
    op.execute("DELETE FROM action_cards WHERE kind = 'stock_low'")
    for table in ("action_cards", "push_notifications"):
        op.drop_constraint("kind", table, type_="check")
    op.create_check_constraint("kind", "action_cards", _in(_BASE))
    op.create_check_constraint("kind", "push_notifications", _in([*_BASE, "send_failed", "digest"]))
