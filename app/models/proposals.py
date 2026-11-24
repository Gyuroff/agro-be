"""Съветен режим (docs/data-model.md, `action_proposals`; docs/advisory-mode.md)."""

from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Index, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import AuditMixin, Base, enum_check


class ActionMode(StrEnum):
    ADVISORY = "advisory"
    LIVE = "live"


class ProposalStatus(StrEnum):
    PENDING = "pending"
    CONFIRMED_CORRECT = "confirmed_correct"
    CONFIRMED_WRONG = "confirmed_wrong"


class FeedbackVerdict(StrEnum):
    CORRECT = "correct"
    WRONG = "wrong"


class ActionProposal(AuditMixin, Base):
    """„Бих направила следното“: снимък на блокираното действие, построен със същия код като истинското."""

    __tablename__ = "action_proposals"
    __table_args__ = (
        enum_check("status", ProposalStatus),
        Index("ix_action_proposals_status_created_at", "status", text("created_at DESC")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    card_id: Mapped[int | None] = mapped_column(ForeignKey("action_cards.id"), nullable=True)
    deal_ref: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default=ProposalStatus.PENDING.value,
        server_default=ProposalStatus.PENDING.value,
    )
    requested_by_id: Mapped[int | None] = mapped_column(ForeignKey("actors.id"), nullable=True)


class ProposalFeedback(AuditMixin, Base):
    """Вердикт на човека върху цялото предложение; един на предложение (docs/data-model.md)."""

    __tablename__ = "proposal_feedback"
    __table_args__ = (
        enum_check("verdict", FeedbackVerdict),
        UniqueConstraint("proposal_id", name="uq_proposal_feedback_proposal_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    proposal_id: Mapped[int] = mapped_column(ForeignKey("action_proposals.id"), nullable=False)
    verdict: Mapped[str] = mapped_column(Text, nullable=False)
    note_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    error_label: Mapped[str | None] = mapped_column(Text, nullable=True)
